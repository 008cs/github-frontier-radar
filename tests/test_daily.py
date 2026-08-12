from __future__ import annotations

from datetime import date
from pathlib import Path

from radar.config import QueryBank, RadarConfig, WatchlistConfig
from radar.daily import _search_queries, run_daily_pipeline
from radar.models import RepoCandidate, TrendingRepository
from radar.state_store import load_state


def candidate(repo_id: int, stars: int, *, source: str = "github_search") -> RepoCandidate:
    return RepoCandidate(
        repo_id=repo_id,
        full_name=f"acme/repo-{repo_id}",
        stars=stars,
        forks=3,
        sources={source},
    )


class FakeGitHubSource:
    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.repository_calls: list[int | str] = []
        self.fail_metadata_for: set[int] = set()
        self.search_results = [candidate(1, 100), candidate(2, 50), candidate(1, 100)]

    def fetch_trending(self, period: str) -> list[TrendingRepository]:
        if period == "daily":
            return [
                TrendingRepository(
                    full_name="acme/repo-1", rank=1, period="daily", period_stars=40
                )
            ]
        return []

    def search_repositories(self, query: str, *, sort: str = "stars", order: str = "desc") -> list[RepoCandidate]:
        self.search_calls.append(query)
        return self.search_results

    def get_repository(self, repo: int | str) -> RepoCandidate | None:
        self.repository_calls.append(repo)
        repo_id = int(str(repo).rsplit("-", maxsplit=1)[-1]) if isinstance(repo, str) else repo
        if repo_id in self.fail_metadata_for:
            raise OSError("temporary GitHub failure")
        return candidate(repo_id, 100 if repo_id == 1 else 50, source="github_metadata")


def minimal_config(**overrides: object) -> RadarConfig:
    base: dict[str, object] = {
        "discovery": {
            "trending_periods": ["daily"],
            "breakout_windows_days": [30],
            "recent_push_days": 30,
        },
        "limits": {
            "max_daily_candidates": 10,
            "max_tracked_repos": 10,
            "max_weekly_prescore": 10,
            "max_llm_triage": 5,
            "max_final_briefs": 10,
            "final_report_max_projects": 10,
        },
    }
    base.update(overrides)
    return RadarConfig.model_validate(base)


def test_daily_pipeline_discovers_deduplicates_refreshes_and_snapshots(tmp_path: Path) -> None:
    source = FakeGitHubSource()
    state_path = tmp_path / "state.json"
    result = run_daily_pipeline(
        source,
        minimal_config(),
        QueryBank({"automation": ["automation"]}),
        WatchlistConfig(owners=["acme"]),
        state_path=state_path,
        run_date=date(2026, 8, 12),
    )

    state = load_state(state_path)
    assert result.discovered == 1 + len(source.search_calls) * 3
    assert result.deduplicated == 2
    assert result.refreshed == 2
    assert result.snapshotted == 2
    assert state.repositories[1].snapshots[0].stars == 100
    assert state.repositories[1].snapshots[0].date == date(2026, 8, 12)
    assert state.repositories[1].last_seen == date(2026, 8, 12)
    assert not hasattr(source, "llm_calls")
    assert not hasattr(source, "feishu_calls")


def test_daily_pipeline_is_deterministic_on_repeated_same_day_run(tmp_path: Path) -> None:
    source = FakeGitHubSource()
    state_path = tmp_path / "state.json"
    kwargs = dict(
        source=source,
        config=minimal_config(),
        queries=QueryBank({"automation": ["automation"]}),
        watchlist=WatchlistConfig(),
        state_path=state_path,
        run_date=date(2026, 8, 12),
    )

    first = run_daily_pipeline(**kwargs)
    second = run_daily_pipeline(**kwargs)

    assert first.snapshotted == 2
    assert second.snapshotted == 0
    assert len(load_state(state_path).repositories[1].snapshots) == 1


def test_daily_pipeline_skips_individual_metadata_failure_and_respects_capacity(tmp_path: Path) -> None:
    source = FakeGitHubSource()
    source.fail_metadata_for.add(2)
    config = minimal_config(
        limits={
            "max_daily_candidates": 10,
            "max_tracked_repos": 1,
            "max_weekly_prescore": 10,
            "max_llm_triage": 5,
            "max_final_briefs": 10,
            "final_report_max_projects": 10,
        }
    )
    result = run_daily_pipeline(
        source,
        config,
        QueryBank({"automation": ["automation"]}),
        WatchlistConfig(),
        state_path=tmp_path / "state.json",
        run_date=date(2026, 8, 12),
    )

    assert result.repository_failures == 1
    assert result.snapshotted == 1
    assert result.skipped_capacity == 0


def test_daily_pipeline_enforces_capacity_for_new_repositories(tmp_path: Path) -> None:
    source = FakeGitHubSource()
    config = minimal_config(
        limits={
            "max_daily_candidates": 10,
            "max_tracked_repos": 1,
            "max_weekly_prescore": 10,
            "max_llm_triage": 5,
            "max_final_briefs": 10,
            "final_report_max_projects": 10,
        }
    )
    result = run_daily_pipeline(
        source,
        config,
        QueryBank({"automation": ["automation"]}),
        WatchlistConfig(),
        state_path=tmp_path / "state.json",
        run_date=date(2026, 8, 12),
    )

    assert result.snapshotted == 1
    assert result.skipped_capacity == 1


def test_daily_search_queries_are_bounded_and_rotate_noncritical_channels() -> None:
    config = minimal_config(
        discovery={
            "trending_periods": [],
            "breakout_windows_days": [30],
            "max_daily_search_queries": 3,
        }
    )
    queries = QueryBank({"automation": ["one", "two", "three"]})

    first = list(_search_queries(config, queries, WatchlistConfig(), date(2026, 8, 12)))
    second = list(_search_queries(config, queries, WatchlistConfig(), date(2026, 8, 13)))

    assert len(first) == len(second) == 3
    assert [channel for _, channel in first[:2]] == ["breakout_30d", "exploration"]
    assert [channel for _, channel in second[:2]] == ["breakout_30d", "exploration"]
    assert first[2] != second[2]  # Query Bank entries rotate deterministically.
