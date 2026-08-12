from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from radar.models import (
    DeliveryResult,
    DeliveryStatus,
    EvidenceConfidence,
    EvidenceItem,
    GrowthMetrics,
    IntelligenceBrief,
    ProjectNature,
    RadarReport,
    RadarState,
    Recommendation,
    RepoCandidate,
    RepoSnapshot,
    RepositoryRecord,
    ScoreBreakdown,
    TriageResult,
    WhyHot,
    CostInfo,
    FrictionInfo,
)


def candidate() -> RepoCandidate:
    return RepoCandidate(
        repo_id=123,
        full_name="openai/radar",
        html_url="https://github.com/openai/radar",
        stars=10,
    )


def test_repo_candidate_requires_positive_github_id() -> None:
    with pytest.raises(ValidationError):
        RepoCandidate(repo_id=0, full_name="openai/radar")


def test_scores_reject_values_outside_explicit_range() -> None:
    with pytest.raises(ValidationError):
        TriageResult(repo_id=123, personal_utility=101)

    with pytest.raises(ValidationError):
        ScoreBreakdown(repo_id=123, global_significance=-0.1)


def test_unknown_external_values_are_optional_not_coerced_to_zero() -> None:
    result = TriageResult(repo_id=123, project_nature=ProjectNature.UNKNOWN)
    metrics = GrowthMetrics(repo_id=123, current_stars=10)

    assert result.personal_utility is None
    assert metrics.star_delta_7d is None
    assert metrics.has_complete_7d_history is False


def test_repo_candidate_normalizes_topics_and_validates_full_name() -> None:
    repo = RepoCandidate(
        repo_id=123,
        full_name="  openai/radar  ",
        topics=["AI", "ai", " automation "],
    )

    assert repo.full_name == "openai/radar"
    assert repo.topics == ["ai", "automation"]

    with pytest.raises(ValidationError):
        RepoCandidate(repo_id=123, full_name="not-a-repository")


def test_complete_growth_history_requires_baseline() -> None:
    with pytest.raises(ValidationError):
        GrowthMetrics(repo_id=123, current_stars=10, has_complete_7d_history=True)


def test_state_requires_matching_repository_key_and_unique_snapshots() -> None:
    record = RepositoryRecord(
        repo_id=123,
        full_name="openai/radar",
        first_seen=date(2026, 8, 1),
        last_seen=date(2026, 8, 2),
        snapshots=[RepoSnapshot(date=date(2026, 8, 2), stars=10, forks=1)],
    )
    state = RadarState(repositories={123: record})
    assert state.repositories[123].full_name == "openai/radar"

    with pytest.raises(ValidationError):
        RadarState(repositories={999: record})

    with pytest.raises(ValidationError):
        RepositoryRecord(
            repo_id=123,
            full_name="openai/radar",
            first_seen=date(2026, 8, 1),
            last_seen=date(2026, 8, 2),
            snapshots=[
                RepoSnapshot(date=date(2026, 8, 2), stars=10, forks=1),
                RepoSnapshot(date=date(2026, 8, 2), stars=11, forks=1),
            ],
        )


def test_final_brief_and_report_keep_typed_dates_and_enums() -> None:
    brief = IntelligenceBrief(
        repo_id=123,
        one_liner="用 GitHub 信号发现开源项目。",
        what_it_does="每天采集项目指标，并每周生成简报。",
        why_hot=WhyHot(text="本周 Trending 出现且 Star 增长明显。", confidence=EvidenceConfidence.FACT),
        why_it_matters_to_user="可以减少手动浏览 GitHub 的时间。",
        cost=CostInfo(),
        adoption_friction=FrictionInfo(score=2, summary="需要配置 API 密钥。"),
        main_risk="冷启动期间趋势数据不完整。",
        recommendation=Recommendation.TRY,
        recommendation_reason="当前工作流直接可用。",
    )
    report = RadarReport(
        week_start=date(2026, 8, 3),
        week_end=date(2026, 8, 9),
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        total_discovered=1,
    )
    delivery = DeliveryResult(status=DeliveryStatus.SENT, cards_sent=1)

    assert brief.recommendation is Recommendation.TRY
    assert report.week_start == date(2026, 8, 3)
    assert delivery.status is DeliveryStatus.SENT


def test_evidence_requires_fact_and_supports_unknown_confidence() -> None:
    evidence = EvidenceItem(source="github_release", fact="v2.0 released", confidence="unknown")
    assert evidence.confidence is EvidenceConfidence.UNKNOWN

    with pytest.raises(ValidationError):
        EvidenceItem(source="github_release", fact="")


def test_report_period_is_validated() -> None:
    with pytest.raises(ValidationError):
        RadarReport(
            week_start=date(2026, 8, 9),
            week_end=date(2026, 8, 3),
            generated_at=datetime.now(timezone.utc),
        )
