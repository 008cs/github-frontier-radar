from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from radar.config import ScoringConfig
from radar.models import GitHubRelease, RepoCandidate, RepoSnapshot
from radar.scoring import (
    calculate_event_importance,
    calculate_global_significance,
    calculate_growth_metrics,
    calculate_momentum,
    calculate_quality_confidence,
    percentile_rankings,
)


AS_OF = date(2026, 8, 12)


def candidate(
    repo_id: int,
    stars: int,
    *,
    created_at: str | None = "2026-01-01T00:00:00Z",
    pushed_at: str | None = "2026-08-11T00:00:00Z",
    trending_stars: int | None = None,
    **overrides: object,
) -> RepoCandidate:
    payload: dict[str, object] = {
        "repo_id": repo_id,
        "full_name": f"acme/repo-{repo_id}",
        "stars": stars,
        "created_at": created_at,
        "pushed_at": pushed_at,
        "trending_stars": trending_stars,
        "has_readme": True,
        "license_name": "MIT",
        "size_kb": 50,
    }
    payload.update(overrides)
    return RepoCandidate.model_validate(payload)


def snapshots(*points: tuple[int, int]) -> list[RepoSnapshot]:
    return [RepoSnapshot(date=date(2026, 8, day), stars=stars, forks=1) for day, stars in points]


def test_growth_metrics_capture_small_explosive_project_and_acceleration() -> None:
    repo = candidate(1, 300)
    metrics = calculate_growth_metrics(repo, snapshots((5, 20), (9, 50), (12, 300)), AS_OF)

    assert metrics.star_delta_7d == 280
    assert metrics.relative_growth_7d == 14.0
    assert metrics.recent_3d_delta == 250
    assert metrics.preceding_4d_delta == 30
    assert metrics.acceleration is not None and metrics.acceleration > 0
    assert metrics.has_complete_7d_history


def test_large_absolute_growth_and_weak_old_growth_are_ranked_by_percentile() -> None:
    fast_small = calculate_growth_metrics(candidate(1, 300), snapshots((5, 20), (12, 300)), AS_OF)
    large_growth = calculate_growth_metrics(candidate(2, 32000), snapshots((5, 30000), (12, 32000)), AS_OF)
    slow_old = calculate_growth_metrics(candidate(3, 100000), snapshots((5, 99950), (12, 100000)), AS_OF)

    scores = calculate_momentum({1: fast_small, 2: large_growth, 3: slow_old}, ScoringConfig())

    assert scores[2].components.absolute_growth_percentile == 100
    assert scores[1].components.relative_growth_percentile == 100
    assert scores[3].components.absolute_growth_percentile == 0
    assert scores[1].score is not None and scores[1].score > scores[3].score  # type: ignore[operator]


def test_missing_history_stays_unknown_but_trending_is_a_cold_start_signal() -> None:
    cold_start = calculate_growth_metrics(
        candidate(1, 500, trending_stars=300), snapshots((12, 500)), AS_OF
    )
    no_signal = calculate_growth_metrics(candidate(2, 100), snapshots((12, 100)), AS_OF)
    scores = calculate_momentum({1: cold_start, 2: no_signal}, ScoringConfig())

    assert cold_start.star_delta_7d is None
    assert cold_start.relative_growth_7d is None
    assert scores[1].components.absolute_growth_percentile is None
    assert scores[1].components.trending_signal == 100
    assert scores[1].score == 100
    assert scores[2].components.trending_signal is None
    assert scores[2].score is None


def test_percentile_rankings_are_deterministic_and_assign_ties_same_midrank() -> None:
    first = percentile_rankings({3: 20, 1: 10, 2: 20, 4: None})
    second = percentile_rankings({4: None, 2: 20, 1: 10, 3: 20})

    assert first == second
    assert first == {1: 0.0, 2: 75.0, 3: 75.0, 4: None}


def test_event_importance_scores_new_release_and_old_revival_from_evidence() -> None:
    release = GitHubRelease(
        release_id=5,
        tag_name="v2",
        published_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    new_repo = candidate(1, 100, created_at="2026-08-10T00:00:00Z")
    new_event = calculate_event_importance(new_repo, AS_OF, ScoringConfig(), latest_release=release)
    assert new_event.components.recently_created is not None
    assert new_event.components.recent_release is not None
    assert new_event.score is not None and new_event.score > 50

    old_repo = candidate(2, 100, created_at="2025-01-01T00:00:00Z")
    old_metrics = calculate_growth_metrics(old_repo, snapshots((5, 1), (12, 100)), AS_OF)
    old_momentum = calculate_momentum({2: old_metrics}, ScoringConfig())[2]
    revival = calculate_event_importance(old_repo, AS_OF, ScoringConfig(), momentum=old_momentum)
    assert revival.components.old_project_revival == 100


def test_quality_confidence_uses_available_evidence_and_hard_rejects_forks() -> None:
    quality = calculate_quality_confidence(candidate(1, 100), AS_OF, ScoringConfig())
    assert quality.score is not None and quality.score > 80
    assert quality.components.readme == 100

    unknown = calculate_quality_confidence(
        candidate(2, 100, has_readme=None, license_name=None, size_kb=None, created_at=None, pushed_at=None),
        AS_OF,
        ScoringConfig(),
    )
    assert unknown.score is None

    fork = calculate_quality_confidence(candidate(3, 100, fork=True), AS_OF, ScoringConfig())
    assert fork.score == 0


def test_global_significance_reweights_missing_external_buzz() -> None:
    metrics = calculate_growth_metrics(candidate(1, 500), snapshots((5, 100), (12, 500)), AS_OF)
    momentum = calculate_momentum({1: metrics}, ScoringConfig())[1]
    event = calculate_event_importance(candidate(1, 500), AS_OF, ScoringConfig())
    global_score = calculate_global_significance(
        1, ScoringConfig(), momentum=momentum, event_importance=event
    )

    assert global_score.score is not None
    assert set(global_score.components.applied_weights) == {"momentum", "event_importance"}
    assert sum(global_score.components.applied_weights.values()) == pytest.approx(1)


def test_global_significance_validates_identity_and_score_range() -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        calculate_global_significance(1, ScoringConfig(), momentum=None, event_importance=None, external_buzz=101)
