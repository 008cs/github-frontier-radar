"""Pure deterministic scoring for GitHub Frontier Radar.

No network, state-store, environment, LLM, or delivery dependency is allowed
in this module.  Unknown data remains ``None`` and weighted combinations are
renormalized over the signals that are actually available.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta

from .config import ScoringConfig
from .models import (
    EventImportanceComponents,
    EventImportanceScore,
    GitHubRelease,
    GlobalSignificanceComponents,
    GlobalSignificanceScore,
    GrowthMetrics,
    MomentumComponents,
    MomentumScore,
    QualityConfidenceComponents,
    QualityConfidenceScore,
    RepoCandidate,
    RepoSnapshot,
)


def calculate_growth_metrics(
    candidate: RepoCandidate,
    snapshots: Sequence[RepoSnapshot],
    as_of: date,
) -> GrowthMetrics:
    """Calculate truthful 7d trend metrics from first-party daily snapshots.

    Each comparison requires exact snapshots on the relevant dates.  A partial
    history is visible in ``days_covered`` but never mislabeled as a 7-day
    delta.  Trending stars remain a cold-start fallback independent of state.
    """

    by_date = {snapshot.date: snapshot for snapshot in snapshots if snapshot.date <= as_of}
    current = by_date.get(as_of)
    # Weekly refresh supplies a truthful current GitHub counter even if the
    # scheduled daily snapshot ran at another time.  Only historical baseline
    # points need to come from our persisted daily series.
    current_stars = candidate.stars
    baseline = by_date.get(as_of - timedelta(days=7))
    three_days_ago = by_date.get(as_of - timedelta(days=3))
    seven_days_ago = baseline
    existing_dates = sorted(by_date)
    earliest = existing_dates[0] if existing_dates else None
    days_covered = min(7, (as_of - earliest).days) if earliest is not None else None

    delta_7d = current_stars - baseline.stars if baseline else None
    relative_growth = (delta_7d / baseline.stars) if delta_7d is not None and baseline.stars > 0 else None
    recent_3d_delta = current_stars - three_days_ago.stars if three_days_ago else None
    preceding_4d_delta = (
        three_days_ago.stars - seven_days_ago.stars
        if three_days_ago is not None and seven_days_ago is not None
        else None
    )
    acceleration = (
        (recent_3d_delta / 3) - (preceding_4d_delta / 4)
        if recent_3d_delta is not None and preceding_4d_delta is not None
        else None
    )
    return GrowthMetrics(
        repo_id=candidate.repo_id,
        current_stars=current_stars,
        baseline_stars=baseline.stars if baseline is not None else None,
        days_covered=days_covered,
        star_delta_7d=delta_7d,
        relative_growth_7d=relative_growth,
        recent_3d_delta=recent_3d_delta,
        preceding_4d_delta=preceding_4d_delta,
        acceleration=acceleration,
        trending_stars=candidate.trending_stars,
        has_complete_7d_history=delta_7d is not None,
    )


def percentile_rankings(values: Mapping[int, float | int | None]) -> dict[int, float | None]:
    """Return deterministic midrank percentiles in [0, 100] for available values.

    Equal values receive the same midrank and candidate dictionary order has no
    influence.  A one-item population receives 100 because it is the sole
    observed maximum, not because unavailable data was substituted.
    """

    available = sorted((float(value), repo_id) for repo_id, value in values.items() if value is not None)
    result: dict[int, float | None] = {repo_id: None for repo_id in values}
    if not available:
        return result
    total = len(available)
    index = 0
    while index < total:
        group_end = index + 1
        while group_end < total and available[group_end][0] == available[index][0]:
            group_end += 1
        percentile = 100.0 if total == 1 else ((index + group_end - 1) / 2) / (total - 1) * 100
        for _, repo_id in available[index:group_end]:
            result[repo_id] = percentile
        index = group_end
    return result


def calculate_momentum(
    metrics_by_repo: Mapping[int, GrowthMetrics], config: ScoringConfig
) -> dict[int, MomentumScore]:
    """Compute percentile-normalized momentum from all available weekly signals."""

    absolute = percentile_rankings(
        {repo_id: metrics.star_delta_7d for repo_id, metrics in metrics_by_repo.items()}
    )
    relative = percentile_rankings(
        {repo_id: metrics.relative_growth_7d for repo_id, metrics in metrics_by_repo.items()}
    )
    acceleration = percentile_rankings(
        {repo_id: metrics.acceleration for repo_id, metrics in metrics_by_repo.items()}
    )
    trending = _trending_signals(metrics_by_repo)

    results: dict[int, MomentumScore] = {}
    for repo_id in sorted(metrics_by_repo):
        components = MomentumComponents(
            absolute_growth_percentile=absolute[repo_id],
            relative_growth_percentile=relative[repo_id],
            trending_signal=trending[repo_id],
            acceleration_percentile=acceleration[repo_id],
        )
        score = _renormalized_weighted_score(
            (
                (components.absolute_growth_percentile, config.momentum_absolute_growth_weight),
                (components.relative_growth_percentile, config.momentum_relative_growth_weight),
                (components.trending_signal, config.momentum_trending_weight),
                (components.acceleration_percentile, config.momentum_acceleration_weight),
            )
        )
        results[repo_id] = MomentumScore(repo_id=repo_id, score=score, components=components)
    return results


def calculate_event_importance(
    candidate: RepoCandidate,
    as_of: date,
    config: ScoringConfig,
    *,
    latest_release: GitHubRelease | None = None,
    momentum: MomentumScore | None = None,
) -> EventImportanceScore:
    """Score evidence-supported recency events; missing evidence is excluded."""

    created_age = _age_days(candidate.created_at, as_of)
    pushed_age = _age_days(candidate.pushed_at, as_of)
    release_date = latest_release.published_at or latest_release.created_at if latest_release else None
    release_age = _age_days(release_date, as_of)
    project_is_old = created_age is not None and created_age >= config.revival_project_age_days
    momentum_is_exceptional = momentum is not None and (momentum.score or 0) >= config.revival_momentum_threshold

    components = EventImportanceComponents(
        recently_created=_recency_score(created_age, config.recent_project_days),
        recent_release=_recency_score(release_age, config.recent_release_days),
        recent_push=_recency_score(pushed_age, config.recent_push_days),
        old_project_revival=100.0 if project_is_old and momentum_is_exceptional else None,
    )
    score = _renormalized_weighted_score(
        (
            (components.recently_created, config.event_created_weight),
            (components.recent_release, config.event_release_weight),
            (components.recent_push, config.event_push_weight),
            (components.old_project_revival, config.event_revival_weight),
        )
    )
    return EventImportanceScore(repo_id=candidate.repo_id, score=score, components=components)


def calculate_quality_confidence(
    candidate: RepoCandidate,
    as_of: date,
    config: ScoringConfig,
) -> QualityConfidenceScore:
    """Assess available non-semantic quality evidence without fabricating absent fields."""

    if candidate.archived or candidate.mirror or candidate.template or candidate.fork:
        return QualityConfidenceScore(repo_id=candidate.repo_id, score=0.0)

    project_age = _age_days(candidate.created_at, as_of)
    push_age = _age_days(candidate.pushed_at, as_of)
    maturity = (
        min(100.0, max(config.quality_new_project_floor, project_age / config.quality_maturity_days * 100))
        if project_age is not None
        else None
    )
    content = 100.0 if candidate.size_kb is not None and candidate.size_kb > 0 else None
    components = QualityConfidenceComponents(
        readme=100.0 if candidate.has_readme is True else 0.0 if candidate.has_readme is False else None,
        license=100.0 if candidate.license_name else 0.0 if candidate.license_name == "" else None,
        recent_maintenance=_maintenance_score(push_age, config.quality_stale_after_days),
        repository_content=content,
        maturity=maturity,
    )
    score = _renormalized_weighted_score(
        (
            (components.readme, config.quality_readme_weight),
            (components.license, config.quality_license_weight),
            (components.recent_maintenance, config.quality_maintenance_weight),
            (components.repository_content, config.quality_content_weight),
            (components.maturity, config.quality_maturity_weight),
        )
    )
    return QualityConfidenceScore(repo_id=candidate.repo_id, score=score, components=components)


def calculate_global_significance(
    repo_id: int,
    config: ScoringConfig,
    *,
    momentum: MomentumScore | None,
    event_importance: EventImportanceScore | None,
    external_buzz: float | None = None,
) -> GlobalSignificanceScore:
    """Combine global signals, renormalizing weights if a source is unavailable."""

    if repo_id <= 0:
        raise ValueError("repo_id must be positive")
    if momentum is not None and momentum.repo_id != repo_id:
        raise ValueError("momentum must belong to repo_id")
    if event_importance is not None and event_importance.repo_id != repo_id:
        raise ValueError("event_importance must belong to repo_id")
    if external_buzz is not None and not 0 <= external_buzz <= 100:
        raise ValueError("external_buzz must be between 0 and 100")

    components = GlobalSignificanceComponents(
        momentum=momentum.score if momentum else None,
        event_importance=event_importance.score if event_importance else None,
        external_buzz=external_buzz,
    )
    named_components = (
        ("momentum", components.momentum, config.global_momentum_weight),
        ("event_importance", components.event_importance, config.global_event_weight),
        ("external_buzz", components.external_buzz, config.global_external_buzz_weight),
    )
    available_weight = sum(weight for _, value, weight in named_components if value is not None)
    applied_weights = {
        name: weight / available_weight
        for name, value, weight in named_components
        if value is not None and available_weight > 0
    }
    components.applied_weights = applied_weights
    return GlobalSignificanceScore(
        repo_id=repo_id,
        score=_renormalized_weighted_score((value, weight) for _, value, weight in named_components),
        components=components,
    )


def _trending_signals(metrics_by_repo: Mapping[int, GrowthMetrics]) -> dict[int, float | None]:
    """Normalize only observed Trending gains; unavailable data remains unknown."""

    available = {repo_id: metrics.trending_stars for repo_id, metrics in metrics_by_repo.items()}
    percentiles = percentile_rankings(available)
    return {
        repo_id: percentiles[repo_id] if value is not None else None
        for repo_id, value in available.items()
    }


def _renormalized_weighted_score(items: Iterable[tuple[float | None, float]]) -> float | None:
    usable = [(value, weight) for value, weight in items if value is not None and weight > 0]
    total_weight = sum(weight for _, weight in usable)
    if total_weight == 0:
        return None
    return sum(value * weight for value, weight in usable) / total_weight


def _age_days(value: datetime | None, as_of: date) -> int | None:
    if value is None:
        return None
    return max(0, (as_of - value.date()).days)


def _recency_score(age_days: int | None, window_days: int) -> float | None:
    if age_days is None:
        return None
    if age_days > window_days:
        return 0.0
    return max(0.0, 100.0 * (1 - age_days / window_days))


def _maintenance_score(age_days: int | None, stale_after_days: int) -> float | None:
    if age_days is None:
        return None
    return max(0.0, 100.0 * (1 - age_days / stale_after_days))
