from __future__ import annotations

import base64
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from radar.config import GitHubConfig
from radar.github_sources import GitHubResponseError, GitHubSources, GitHubTransportError, parse_trending_html


def repository_payload(repo_id: int = 101, full_name: str = "acme/radar") -> dict[str, object]:
    return {
        "id": repo_id,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": "Useful project",
        "language": "Python",
        "topics": ["Automation", "ai"],
        "stargazers_count": 123,
        "forks_count": 8,
        "open_issues_count": 2,
        "created_at": "2026-08-01T00:00:00Z",
        "pushed_at": "2026-08-10T00:00:00Z",
        "archived": False,
        "mirror_url": None,
        "is_template": False,
        "fork": False,
        "license": {"spdx_id": "MIT"},
    }


def make_sources(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    config: GitHubConfig | None = None,
    token: SecretStr | str | None = None,
    sleeps: list[float] | None = None,
) -> GitHubSources:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubSources(
        config or GitHubConfig(),
        token,
        client=client,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_search_uses_required_headers_and_maps_repository() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["X-Github-Api-Version"] == "2026-03-10"
        assert request.headers["Authorization"] == "Bearer secret-value"
        assert request.url.params["q"] == "automation"
        return httpx.Response(
            200,
            json={"total_count": 1, "items": [repository_payload()]},
            headers={"X-RateLimit-Limit": "30", "X-RateLimit-Remaining": "29"},
        )

    sources = make_sources(handler, token=SecretStr("secret-value"))
    result = sources.search_repositories(" automation ")

    assert result[0].repo_id == 101
    assert result[0].topics == ["ai", "automation"]
    assert result[0].license_name == "MIT"
    assert sources.last_rate_limit is not None
    assert sources.last_rate_limit.remaining == 29


def test_search_paginates_only_to_configured_maximum() -> None:
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pages.append(request.url.params["page"])
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={"total_count": 6, "items": [repository_payload(page * 10 + index) for index in range(3)]},
        )

    config = GitHubConfig(search_per_page=3, search_max_pages=2)
    sources = make_sources(handler, config=config)
    result = sources.search_repositories("topic:automation")

    assert pages == ["1", "2"]
    assert [repo.repo_id for repo in result] == [10, 11, 12, 20, 21, 22]


def test_rate_limit_retries_using_retry_after_and_records_headers() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={
                    "Retry-After": "2.5",
                    "X-RateLimit-Limit": "30",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "1786406400",
                    "X-RateLimit-Resource": "search",
                },
            )
        return httpx.Response(200, json={"total_count": 0, "items": []})

    sources = make_sources(handler, sleeps=sleeps)
    assert sources.search_repositories("automation") == []
    assert calls == 2
    assert sleeps == [2.5]
    assert sources.last_rate_limit is not None
    assert sources.last_rate_limit.resource == "search"


def test_transient_5xx_is_retried() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"total_count": 0, "items": []})

    sources = make_sources(handler)
    assert sources.search_repositories("automation") == []
    assert calls == 2


def test_timeout_is_retried_then_has_typed_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    sources = make_sources(handler, config=GitHubConfig(max_retries=1))
    with pytest.raises(GitHubTransportError):
        sources.search_repositories("automation")
    assert calls == 2


def test_permanent_http_errors_are_typed() -> None:
    sources = make_sources(lambda _: httpx.Response(403))

    with pytest.raises(GitHubResponseError, match="403"):
        sources.search_repositories("automation")


def test_repository_404_is_a_normal_absence() -> None:
    sources = make_sources(lambda _: httpx.Response(404))
    assert sources.get_repository("acme/missing") is None


def test_readme_decodes_content_and_missing_readme_is_normal() -> None:
    encoded = base64.b64encode("# Radar\n".encode()).decode()
    sources = make_sources(
        lambda _: httpx.Response(200, json={"encoding": "base64", "content": encoded})
    )
    assert sources.get_readme("acme/radar") == "# Radar\n"

    missing = make_sources(lambda _: httpx.Response(404))
    assert missing.get_readme("acme/radar") is None


def test_latest_release_maps_fields_and_missing_release_is_normal() -> None:
    sources = make_sources(
        lambda _: httpx.Response(
            200,
            json={
                "id": 99,
                "tag_name": "v1.2.0",
                "name": "Release 1.2",
                "body": "Important release",
                "html_url": "https://github.com/acme/radar/releases/tag/v1.2.0",
                "published_at": "2026-08-10T00:00:00Z",
                "created_at": "2026-08-09T00:00:00Z",
                "prerelease": False,
                "draft": False,
            },
        )
    )
    release = sources.get_latest_release("acme/radar")
    assert release is not None
    assert release.tag_name == "v1.2.0"
    assert release.release_id == 99

    missing = make_sources(lambda _: httpx.Response(404))
    assert missing.get_latest_release("acme/radar") is None


TRENDING_HTML = """
<article class="Box-row">
  <h2><a href="/acme/radar"> acme / radar </a></h2>
  <p>A useful radar.</p>
  <span itemprop="programmingLanguage">Python</span>
  <a class="Link--muted" href="/acme/radar/stargazers">1,234</a>
  <span class="d-inline-block float-sm-right">56 stars this week</span>
</article>
"""


def test_trending_parser_extracts_available_metrics() -> None:
    result = parse_trending_html(TRENDING_HTML, "weekly")

    assert len(result) == 1
    assert result[0].full_name == "acme/radar"
    assert result[0].total_stars == 1234
    assert result[0].period_stars == 56
    assert result[0].rank == 1


def test_broken_trending_html_degrades_to_empty_without_crashing(caplog: pytest.LogCaptureFixture) -> None:
    sources = make_sources(lambda _: httpx.Response(200, text="<html>login page</html>"))

    assert sources.fetch_trending("daily") == []
    assert "continuing without it" in caplog.text
