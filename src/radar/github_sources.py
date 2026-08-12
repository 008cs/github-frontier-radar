"""Isolated, synchronous adapters for GitHub REST and Trending data.

This module only retrieves and parses GitHub data.  It does not write state,
score repositories, invoke LLMs, or execute any code from third-party repos.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, Protocol

import httpx
from bs4 import BeautifulSoup
from pydantic import SecretStr, ValidationError

from .config import GitHubConfig
from .models import GitHubRateLimit, GitHubRelease, RepoCandidate, TrendingRepository


LOGGER = logging.getLogger(__name__)
TrendingPeriod = Literal["daily", "weekly"]


class SupportsRequest(Protocol):
    """Small testable portion of an httpx synchronous client."""

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response: ...

    def close(self) -> None: ...


class GitHubSourceError(RuntimeError):
    """A non-secret-bearing error returned by the GitHub source adapter."""


class GitHubResponseError(GitHubSourceError):
    """GitHub returned an unexpected permanent HTTP response."""


class GitHubTransportError(GitHubSourceError):
    """Transient transport failures exhausted the configured retry budget."""


class GitHubPayloadError(GitHubSourceError):
    """A successful HTTP response did not contain the expected GitHub shape."""


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GitHubPayloadError("GitHub response was not a JSON object")
    return value


def _as_string(value: object, field_name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise GitHubPayloadError(f"GitHub response is missing {field_name}")
        return None
    if not isinstance(value, str):
        raise GitHubPayloadError(f"GitHub response field {field_name} must be a string")
    return value


def _as_nonnegative_int(value: object, field_name: str, *, required: bool = False) -> int | None:
    if value is None:
        if required:
            raise GitHubPayloadError(f"GitHub response is missing {field_name}")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GitHubPayloadError(f"GitHub response field {field_name} must be a non-negative integer")
    return value


def _as_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitHubPayloadError(f"GitHub response field {field_name} must be a positive integer")
    return value


def _as_bool(value: object, field_name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise GitHubPayloadError(f"GitHub response field {field_name} must be a boolean")
    return value


def _as_string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GitHubPayloadError(f"GitHub response field {field_name} must be a list of strings")
    return value


def _format_count(value: str | None) -> int | None:
    """Convert a Trending count such as ``1,234`` or ``1.2k`` to an integer."""

    if value is None:
        return None
    compact = value.strip().lower().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([km]?)", compact)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1_000 if suffix == "k" else 1_000_000 if suffix == "m" else 1
    return int(number * multiplier)


def parse_trending_html(html: str, period: TrendingPeriod) -> list[TrendingRepository]:
    """Parse only the small, volatile HTML surface of GitHub Trending.

    Raises ``GitHubPayloadError`` for an unexpected page so callers can
    degrade safely rather than silently treating a login/error page as empty.
    """

    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.Box-row")
    if not articles:
        raise GitHubPayloadError("GitHub Trending HTML contains no repository entries")

    entries: list[TrendingRepository] = []
    for rank, article in enumerate(articles, start=1):
        anchor = article.select_one("h2 a[href]")
        if anchor is None:
            raise GitHubPayloadError("GitHub Trending entry is missing its repository link")
        href = anchor.get("href")
        if not isinstance(href, str):
            raise GitHubPayloadError("GitHub Trending repository link is malformed")
        full_name = href.strip("/").strip()

        description_node = article.select_one("p")
        description = description_node.get_text(" ", strip=True) if description_node else None
        language_node = article.select_one("span[itemprop='programmingLanguage']")
        language = language_node.get_text(" ", strip=True) if language_node else None

        total_stars: int | None = None
        for star_link in article.select("a.Link--muted"):
            href_value = star_link.get("href")
            if isinstance(href_value, str) and href_value.endswith("/stargazers"):
                total_stars = _format_count(star_link.get_text(" ", strip=True))
                break

        period_stars: int | None = None
        for node in article.select("span"):
            text = node.get_text(" ", strip=True)
            if "stars" in text.lower() and "this" in text.lower():
                period_stars = _format_count(text)
                break

        try:
            entries.append(
                TrendingRepository(
                    full_name=full_name,
                    description=description or None,
                    language=language or None,
                    total_stars=total_stars,
                    period_stars=period_stars,
                    rank=rank,
                    period=period,
                )
            )
        except ValidationError as error:
            raise GitHubPayloadError("GitHub Trending repository entry is invalid") from error
    return entries


class GitHubSources:
    """Low-concurrency GitHub source adapter with bounded retries.

    Callers can inject an ``httpx.Client`` with ``MockTransport`` and a no-op
    sleeper.  Production uses one serial synchronous client; this avoids
    exhausting GitHub Search's separate rate-limit bucket.
    """

    def __init__(
        self,
        config: GitHubConfig,
        github_token: SecretStr | str | None = None,
        *,
        client: SupportsRequest | None = None,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._api_base_url = config.api_base_url.rstrip("/")
        self._github_token = (
            github_token.get_secret_value() if isinstance(github_token, SecretStr) else github_token
        )
        self._sleep = sleep
        self._logger = logger or LOGGER
        self._client: SupportsRequest
        self._owns_client = client is None
        if client is None:
            timeout = httpx.Timeout(
                connect=config.connect_timeout_seconds,
                read=config.read_timeout_seconds,
                write=config.read_timeout_seconds,
                pool=config.connect_timeout_seconds,
            )
            self._client = httpx.Client(timeout=timeout, follow_redirects=True)
        else:
            self._client = client
        self.last_rate_limit: GitHubRateLimit | None = None

    def close(self) -> None:
        """Close only a client that this adapter constructed."""

        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubSources:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search_repositories(
        self,
        query: str,
        *,
        sort: Literal["stars", "updated", "forks", "help-wanted-issues"] = "stars",
        order: Literal["asc", "desc"] = "desc",
    ) -> list[RepoCandidate]:
        """Search repositories serially up to the configured page cap."""

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("GitHub search query must not be blank")

        repositories: list[RepoCandidate] = []
        for page in range(1, self._config.search_max_pages + 1):
            response = self._request_rest(
                "GET",
                "/search/repositories",
                params={
                    "q": normalized_query,
                    "sort": sort,
                    "order": order,
                    "per_page": self._config.search_per_page,
                    "page": page,
                },
            )
            payload = self._json_mapping(response)
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise GitHubPayloadError("GitHub search response is missing an items list")

            for item in raw_items:
                try:
                    repositories.append(self._repo_candidate_from_payload(_as_mapping(item), "github_search"))
                except (GitHubPayloadError, ValidationError) as error:
                    self._logger.warning("Skipping malformed GitHub search repository: %s", error)

            total_count = _as_nonnegative_int(payload.get("total_count"), "total_count", required=True)
            if len(raw_items) < self._config.search_per_page or len(repositories) >= total_count:
                break
        return repositories

    def get_repository(self, repo: int | str) -> RepoCandidate | None:
        """Retrieve a repository by stable GitHub ID or ``owner/name``."""

        if isinstance(repo, int):
            if repo <= 0:
                raise ValueError("repo ID must be positive")
            path = f"/repositories/{repo}"
        else:
            full_name = repo.strip().strip("/")
            if full_name.count("/") != 1:
                raise ValueError("repository name must have the form 'owner/repository'")
            path = f"/repos/{full_name}"

        response = self._request_rest("GET", path, allow_not_found=True)
        if response is None:
            return None
        return self._repo_candidate_from_payload(self._json_mapping(response), "github_metadata")

    def get_readme(self, full_name: str) -> str | None:
        """Return decoded README text; a missing or non-decodable README is normal."""

        response = self._request_rest("GET", f"/repos/{self._validated_full_name(full_name)}/readme", allow_not_found=True)
        if response is None:
            return None
        payload = self._json_mapping(response)
        content = _as_string(payload.get("content"), "content")
        encoding = _as_string(payload.get("encoding"), "encoding")
        if content is None:
            self._logger.warning("GitHub README response had no inline content for %s", full_name)
            return None
        if encoding not in {None, "base64"}:
            self._logger.warning("GitHub README used unsupported encoding for %s", full_name)
            return None
        try:
            return base64.b64decode(content.encode("ascii"), validate=False).decode("utf-8")
        except (UnicodeDecodeError, ValueError, binascii.Error):
            self._logger.warning("GitHub README could not be decoded for %s", full_name)
            return None

    def get_latest_release(self, full_name: str) -> GitHubRelease | None:
        """Return the latest release; repositories with no release return ``None``."""

        response = self._request_rest(
            "GET", f"/repos/{self._validated_full_name(full_name)}/releases/latest", allow_not_found=True
        )
        if response is None:
            return None
        payload = self._json_mapping(response)
        try:
            return GitHubRelease(
                release_id=_as_positive_int(payload.get("id"), "id"),
                tag_name=_as_string(payload.get("tag_name"), "tag_name", required=True),
                name=_as_string(payload.get("name"), "name"),
                body=_as_string(payload.get("body"), "body"),
                html_url=_as_string(payload.get("html_url"), "html_url"),
                published_at=_as_string(payload.get("published_at"), "published_at"),
                created_at=_as_string(payload.get("created_at"), "created_at"),
                prerelease=_as_bool(payload.get("prerelease"), "prerelease"),
                draft=_as_bool(payload.get("draft"), "draft"),
            )
        except ValidationError as error:
            raise GitHubPayloadError("GitHub latest release response is invalid") from error

    def fetch_trending(self, period: TrendingPeriod) -> list[TrendingRepository]:
        """Fetch a degradable Trending signal from GitHub's non-contract HTML page."""

        if period not in {"daily", "weekly"}:
            raise ValueError("Trending period must be 'daily' or 'weekly'")
        try:
            response = self._request(
                "GET",
                "https://github.com/trending",
                params={"since": period},
                headers={"Accept": "text/html", "User-Agent": "github-frontier-radar"},
            )
            return parse_trending_html(response.text, period)
        except (GitHubSourceError, httpx.HTTPError) as error:
            self._logger.warning("GitHub Trending unavailable; continuing without it: %s", error)
            return []

    def _request_rest(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        response = self._request(
            method,
            f"{self._api_base_url}{path}",
            params=params,
            headers=self._rest_headers(),
            allow_not_found=allow_not_found,
        )
        return response

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        allow_not_found: bool = False,
    ) -> httpx.Response | None:
        last_transport_error: httpx.HTTPError | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._client.request(method, url, params=params, headers=headers)
            except httpx.HTTPError as error:
                last_transport_error = error
                if attempt == self._config.max_retries:
                    raise GitHubTransportError("GitHub transport retries exhausted") from error
                self._sleep(self._retry_delay(attempt, None))
                continue

            self._record_rate_limit(response)
            if response.status_code == 404 and allow_not_found:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self._config.max_retries:
                    raise GitHubResponseError(
                        f"GitHub request failed after retries with HTTP {response.status_code}"
                    )
                delay = self._retry_delay(attempt, response.headers.get("Retry-After"))
                response.close()
                self._sleep(delay)
                continue
            if response.is_error:
                raise GitHubResponseError(f"GitHub request failed with HTTP {response.status_code}")
            return response

        raise GitHubTransportError("GitHub transport retries exhausted") from last_transport_error

    def _rest_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._config.api_version,
            "User-Agent": "github-frontier-radar",
        }
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"
        return headers

    def _record_rate_limit(self, response: httpx.Response) -> None:
        headers = response.headers
        limit = self._safe_header_int(headers.get("X-RateLimit-Limit"))
        remaining = self._safe_header_int(headers.get("X-RateLimit-Remaining"))
        reset_epoch = self._safe_header_int(headers.get("X-RateLimit-Reset"))
        reset_at = datetime.fromtimestamp(reset_epoch, tz=UTC) if reset_epoch is not None else None
        if any(value is not None for value in (limit, remaining, reset_at)):
            self.last_rate_limit = GitHubRateLimit(
                limit=limit,
                remaining=remaining,
                reset_at=reset_at,
                resource=headers.get("X-RateLimit-Resource"),
            )

    @staticmethod
    def _safe_header_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        parsed_retry_after = self._parse_retry_after(retry_after)
        if parsed_retry_after is not None:
            return parsed_retry_after
        return self._config.retry_base_delay_seconds * (2**attempt)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())

    @staticmethod
    def _json_mapping(response: httpx.Response) -> Mapping[str, object]:
        try:
            return _as_mapping(response.json())
        except ValueError as error:
            raise GitHubPayloadError("GitHub response did not contain valid JSON") from error

    @staticmethod
    def _validated_full_name(full_name: str) -> str:
        normalized = full_name.strip().strip("/")
        if normalized.count("/") != 1 or any(not component for component in normalized.split("/")):
            raise ValueError("repository name must have the form 'owner/repository'")
        return normalized

    @staticmethod
    def _repo_candidate_from_payload(payload: Mapping[str, object], source: str) -> RepoCandidate:
        license_payload = payload.get("license")
        license_name: str | None = None
        if license_payload is not None:
            license_mapping = _as_mapping(license_payload)
            license_name = _as_string(license_mapping.get("spdx_id"), "license.spdx_id")
            if license_name == "NOASSERTION":
                license_name = _as_string(license_mapping.get("name"), "license.name")

        mirror_url = _as_string(payload.get("mirror_url"), "mirror_url")
        try:
            return RepoCandidate(
                repo_id=_as_positive_int(payload.get("id"), "id"),
                full_name=_as_string(payload.get("full_name"), "full_name", required=True),
                html_url=_as_string(payload.get("html_url"), "html_url"),
                description=_as_string(payload.get("description"), "description"),
                language=_as_string(payload.get("language"), "language"),
                topics=_as_string_list(payload.get("topics"), "topics"),
                stars=_as_nonnegative_int(payload.get("stargazers_count"), "stargazers_count") or 0,
                forks=_as_nonnegative_int(payload.get("forks_count"), "forks_count") or 0,
                size_kb=_as_nonnegative_int(payload.get("size"), "size"),
                open_issues=_as_nonnegative_int(payload.get("open_issues_count"), "open_issues_count"),
                created_at=_as_string(payload.get("created_at"), "created_at"),
                pushed_at=_as_string(payload.get("pushed_at"), "pushed_at"),
                archived=_as_bool(payload.get("archived"), "archived"),
                mirror=mirror_url is not None,
                template=_as_bool(payload.get("is_template"), "is_template"),
                fork=_as_bool(payload.get("fork"), "fork"),
                license_name=license_name,
                sources={source},
            )
        except ValidationError as error:
            raise GitHubPayloadError("GitHub repository response is invalid") from error
