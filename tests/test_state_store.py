from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from radar.models import RadarState, RepoCandidate
from radar.state_store import (
    StateCorruptionError,
    calculate_star_delta,
    get_historical_snapshot,
    is_in_cooldown,
    load_state,
    mark_featured,
    prune_state,
    record_snapshot,
    save_state,
)


def candidate(repo_id: int = 1, stars: int = 100, forks: int = 10) -> RepoCandidate:
    return RepoCandidate(repo_id=repo_id, full_name=f"acme/repo-{repo_id}", stars=stars, forks=forks)


def test_missing_state_file_is_empty_and_atomic_save_round_trips(tmp_path: Path) -> None:
    state_path = tmp_path / "nested" / "state.json"
    assert load_state(state_path) == RadarState()

    state = RadarState()
    record_snapshot(state, candidate(), date(2026, 8, 1))
    save_state(state, state_path)

    assert load_state(state_path) == state
    assert not list(state_path.parent.glob("*.tmp"))


def test_corrupt_state_is_explicit_and_never_silently_reset(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{ this is invalid", encoding="utf-8")

    with pytest.raises(StateCorruptionError, match="was not modified"):
        load_state(state_path)
    assert state_path.read_text(encoding="utf-8") == "{ this is invalid"


def test_snapshot_is_upserted_once_per_repo_per_date() -> None:
    state = RadarState()
    assert record_snapshot(state, candidate(stars=100), date(2026, 8, 1)) is True
    assert record_snapshot(state, candidate(stars=120), date(2026, 8, 1)) is False

    snapshots = state.repositories[1].snapshots
    assert len(snapshots) == 1
    assert snapshots[0].stars == 120


def test_exact_seven_day_delta_requires_real_history_and_supports_cold_start() -> None:
    state = RadarState()
    record_snapshot(state, candidate(stars=100), date(2026, 8, 1))
    record_snapshot(state, candidate(stars=180), date(2026, 8, 8))
    record = state.repositories[1]

    assert get_historical_snapshot(record, date(2026, 8, 8)).stars == 100  # type: ignore[union-attr]
    assert calculate_star_delta(record, date(2026, 8, 8)) == 80
    assert calculate_star_delta(record, date(2026, 8, 7)) is None


def test_mark_featured_and_cooldown_are_preserved() -> None:
    state = RadarState()
    record_snapshot(state, candidate(), date(2026, 8, 1))
    mark_featured(state, 1, date(2026, 8, 2))
    record = state.repositories[1]

    assert record.feature_count == 1
    assert is_in_cooldown(record, date(2026, 9, 1), cooldown_days=56)
    assert not is_in_cooldown(record, date(2026, 9, 27), cooldown_days=56)


def test_pruning_keeps_feature_history_but_removes_unfeatured_inactive_repositories() -> None:
    state = RadarState()
    record_snapshot(state, candidate(1), date(2026, 7, 1))
    record_snapshot(state, candidate(2), date(2026, 7, 1))
    mark_featured(state, 1, date(2026, 7, 2))

    result = prune_state(
        state,
        date(2026, 8, 12),
        snapshot_retention_days=35,
        inactive_repository_days=30,
        max_tracked_repos=600,
    )

    assert state.repositories[1].snapshots == []
    assert state.repositories[1].feature_count == 1
    assert 2 not in state.repositories
    assert result.repositories_archived == 1
    assert result.repositories_removed == 1


def test_pruning_applies_active_tracking_capacity_deterministically() -> None:
    state = RadarState()
    record_snapshot(state, candidate(1), date(2026, 8, 10))
    record_snapshot(state, candidate(2), date(2026, 8, 11))
    record_snapshot(state, candidate(3), date(2026, 8, 12))

    prune_state(
        state,
        date(2026, 8, 12),
        snapshot_retention_days=35,
        inactive_repository_days=30,
        max_tracked_repos=2,
    )

    assert set(state.repositories) == {2, 3}
