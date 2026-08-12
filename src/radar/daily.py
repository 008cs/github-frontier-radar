"""Daily discovery and snapshot orchestration without LLM or delivery calls."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from .config import QueryBank, RadarConfig, WatchlistConfig, date_in_timezone
from .github_sources import GitHubSourceError, TrendingPeriod
from .models import DailyRunResult, RepoCandidate, TrendingRepository
from .state_store import (
    DEFAULT_STATE_PATH,
    active_repository_count,
    load_state,
    prune_state,
    record_snapshot,
    save_state,
)


LOGGER = logging.getLogger(__name__)


class DailyGitHubSource(Protocol):
    """The minimal injectable GitHub interface used by the daily pipeline."""

    def search_repositories(self, query: str, *, sort: str = "stars", order: str = "desc") -> list[RepoCandidate]: ...

    def fetch_trending(self, period: TrendingPeriod) -> list[TrendingRepository]: ...

    def get_repository(self, repo: int | str) -> RepoCandidate | None: ...


def run_daily_pipeline(
    source: DailyGitHubSource,
    config: RadarConfig,
    queries: QueryBank,
    watchlist: WatchlistConfig,
    *,
    state_path: str | Path = DEFAULT_STATE_PATH,
    run_date: date | None = None,
    logger: logging.Logger | None = None,
) -> DailyRunResult:
    """Discover, refresh and snapshot candidates; no analysis or notifications occur here."""

    today = run_date or date_in_timezone(config.timezone)
    run_logger = logger or LOGGER
    state = load_state(state_path)
    initial_prune = prune_state(
        state,
        today,
        snapshot_retention_days=config.state.snapshot_retention_days,
        inactive_repository_days=config.state.inactive_repository_days,
        max_tracked_repos=config.limits.max_tracked_repos,
    )

    discovered, source_failures = _discover_candidates(source, config, queries, watchlist, today, run_logger)
    deduplicated = _deduplicate_candidates(discovered, config.limits.max_daily_candidates)
    refreshed, repository_failures = _refresh_candidates(source, deduplicated, run_logger)

    snapshotted = 0
    skipped_capacity = 0
    for candidate in refreshed:
        if candidate.repo_id not in state.repositories and active_repository_count(state) >= config.limits.max_tracked_repos:
            skipped_capacity += 1
            continue
        if record_snapshot(state, candidate, today):
            snapshotted += 1

    final_prune = prune_state(
        state,
        today,
        snapshot_retention_days=config.state.snapshot_retention_days,
        inactive_repository_days=config.state.inactive_repository_days,
        max_tracked_repos=config.limits.max_tracked_repos,
    )
    save_state(state, state_path)

    result = DailyRunResult(
        run_date=today,
        discovered=len(discovered),
        deduplicated=len(deduplicated),
        refreshed=len(refreshed),
        snapshotted=snapshotted,
        skipped_capacity=skipped_capacity,
        source_failures=source_failures,
        repository_failures=repository_failures,
        prune_result=initial_prune.model_copy(
            update={
                "snapshots_removed": initial_prune.snapshots_removed + final_prune.snapshots_removed,
                "repositories_archived": initial_prune.repositories_archived
                + final_prune.repositories_archived,
                "repositories_removed": initial_prune.repositories_removed + final_prune.repositories_removed,
            }
        ),
    )
    run_logger.info(
        "daily discovered=%s deduplicated=%s refreshed=%s snapshotted=%s skipped_capacity=%s "
        "source_failures=%s repository_failures=%s",
        result.discovered,
        result.deduplicated,
        result.refreshed,
        result.snapshotted,
        result.skipped_capacity,
        result.source_failures,
        result.repository_failures,
    )
    return result


def _discover_candidates(
    source: DailyGitHubSource,
    config: RadarConfig,
    queries: QueryBank,
    watchlist: WatchlistConfig,
    today: date,
    logger: logging.Logger,
) -> tuple[list[RepoCandidate], int]:
    candidates: list[RepoCandidate] = []
    failures = 0

    for period in config.discovery.trending_periods:
        try:
            for entry in source.fetch_trending(period):
                resolved = source.get_repository(entry.full_name)
                if resolved is None:
                    logger.warning("Trending repository no longer exists: %s", entry.full_name)
                    continue
                candidates.append(_with_trending_signal(resolved, entry))
        except (GitHubSourceError, OSError, ValueError) as error:
            failures += 1
            logger.warning("Trending discovery failed for %s; continuing: %s", period, error)

    for query, channel in _search_queries(config, queries, watchlist, today):
        try:
            results = source.search_repositories(query)
        except (GitHubSourceError, OSError, ValueError) as error:
            failures += 1
            logger.warning("Discovery channel %s failed; continuing: %s", channel, error)
            continue
        candidates.extend(_with_source(candidate, channel) for candidate in results)
    return candidates, failures


def _search_queries(
    config: RadarConfig, queries: QueryBank, watchlist: WatchlistConfig, today: date
) -> Iterable[tuple[str, str]]:
    filters = "archived:false mirror:false template:false"
    fixed_queries: list[tuple[str, str]] = []
    for days in config.discovery.breakout_windows_days:
        since = today - timedelta(days=days)
        fixed_queries.append(
            (
                f"created:>={since.isoformat()} stars:>={config.discovery.breakout_min_stars} {filters}",
                f"breakout_{days}d",
            )
        )
    exploration_since = today - timedelta(days=max(config.discovery.breakout_windows_days))
    fixed_queries.append(
        (
            f"created:>={exploration_since.isoformat()} stars:>={config.discovery.exploration_min_stars} {filters}",
            "exploration",
        )
    )

    rotating_queries: list[tuple[str, str]] = []
    recent_push = today - timedelta(days=config.discovery.recent_push_days)
    for category, terms in sorted(queries.root.items()):
        for term in terms:
            rotating_queries.append(
                (f'"{term}" pushed:>={recent_push.isoformat()} {filters}', f"query_{category}")
            )
    owner_since = today - timedelta(days=config.discovery.owner_lookback_days)
    for owner in watchlist.owners:
        rotating_queries.append(
            (f"user:{owner} created:>={owner_since.isoformat()} {filters}", "watchlist")
        )

    budget = config.discovery.max_daily_search_queries
    selected = fixed_queries[:budget]
    remaining = budget - len(selected)
    if remaining > 0 and rotating_queries:
        offset = today.toordinal() % len(rotating_queries)
        rotated = rotating_queries[offset:] + rotating_queries[:offset]
        selected.extend(rotated[:remaining])
    yield from selected


def _deduplicate_candidates(candidates: list[RepoCandidate], maximum: int) -> list[RepoCandidate]:
    """Merge channels by stable ID with deterministic ordering and a hard cap."""

    deduplicated: dict[int, RepoCandidate] = {}
    for candidate in candidates:
        existing = deduplicated.get(candidate.repo_id)
        if existing is None:
            deduplicated[candidate.repo_id] = candidate
            continue
        deduplicated[candidate.repo_id] = _merge_candidates(existing, candidate)

    return sorted(
        deduplicated.values(), key=lambda candidate: (-candidate.stars, candidate.repo_id)
    )[:maximum]


def _refresh_candidates(
    source: DailyGitHubSource, candidates: list[RepoCandidate], logger: logging.Logger
) -> tuple[list[RepoCandidate], int]:
    refreshed: list[RepoCandidate] = []
    failures = 0
    for candidate in candidates:
        try:
            metadata = source.get_repository(candidate.repo_id)
        except (GitHubSourceError, OSError, ValueError) as error:
            failures += 1
            logger.warning("Metadata refresh failed for %s; skipping: %s", candidate.full_name, error)
            continue
        if metadata is None:
            logger.warning("Repository disappeared before daily snapshot: %s", candidate.full_name)
            continue
        refreshed.append(_merge_candidates(candidate, metadata))
    return refreshed, failures


def _with_source(candidate: RepoCandidate, source: str) -> RepoCandidate:
    return candidate.model_copy(update={"sources": candidate.sources | {source}})


def _with_trending_signal(candidate: RepoCandidate, entry: TrendingRepository) -> RepoCandidate:
    return candidate.model_copy(
        update={
            "trending_rank": entry.rank,
            "trending_stars": entry.period_stars,
            "sources": candidate.sources | {f"trending_{entry.period}"},
        }
    )


def _merge_candidates(left: RepoCandidate, right: RepoCandidate) -> RepoCandidate:
    """Prefer refreshed REST metadata, preserving discovery-only trend annotations."""

    return right.model_copy(
        update={
            "sources": left.sources | right.sources,
            "trending_rank": right.trending_rank or left.trending_rank,
            "trending_stars": right.trending_stars or left.trending_stars,
        }
    )
