from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from radar.models import (
    CostInfo,
    FrictionInfo,
    GrowthMetrics,
    IntelligenceBrief,
    RadarReport,
    RankedCandidate,
    Recommendation,
    RepoCandidate,
    ScoreBreakdown,
    WhyHot,
    SelectionRoute,
)
from radar.report import render_markdown, report_filename, write_markdown_report


def project(repo_id: int = 1) -> RankedCandidate:
    return RankedCandidate(
        candidate=RepoCandidate(repo_id=repo_id, full_name=f"acme/repo-{repo_id}", html_url=f"https://github.com/acme/repo-{repo_id}", stars=100),
        growth=GrowthMetrics(repo_id=repo_id, current_stars=100, baseline_stars=30, star_delta_7d=70, has_complete_7d_history=True),
        scores=ScoreBreakdown(repo_id=repo_id, global_significance=80, personal_utility=90, practical_value=85, quality_confidence=75),
    )


def brief(repo_id: int = 1) -> IntelligenceBrief:
    return IntelligenceBrief(
        repo_id=repo_id,
        one_liner="把网页重复工作变成自动化任务。",
        what_it_does="为常见网页流程提供可编排的自动化能力。",
        why_hot=WhyHot(text="两天前发布了主要版本。", confidence="fact"),
        why_it_matters_to_user="可减少手动处理网页任务的时间。",
        target_users=["开发者", "自动化用户"],
        cost=CostInfo(type="free", note="开源免费。"),
        adoption_friction=FrictionInfo(score=2, summary="需要基础开发环境。"),
        main_risk="项目仍需在实际业务流程中验证稳定性。",
        recommendation=Recommendation.TRY,
        recommendation_reason="与浏览器自动化需求直接相关。",
    )


def report(projects: list[RankedCandidate] | None = None, briefs: dict[int, IntelligenceBrief] | None = None) -> RadarReport:
    return RadarReport(
        week_start=date(2026, 8, 10),
        week_end=date(2026, 8, 16),
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        total_discovered=50,
        weekly_observation="浏览器自动化项目的热度明显上升。",
        projects=projects if projects is not None else [project()],
        briefs=briefs if briefs is not None else {1: brief()},
    )


def test_render_markdown_preserves_required_project_intelligence() -> None:
    content = render_markdown(report())

    assert "# GitHub Frontier Radar" in content
    assert "acme/repo-1" in content
    assert "7 日 +70 ⭐" in content
    assert "它能做什么" in content
    assert "为什么最近受关注" in content
    assert "对你的价值" in content
    assert "开发者 / 自动化用户" in content
    assert "开源免费；开源免费。" in content
    assert "主要风险" in content
    assert "🔥 建议试试" in content
    assert "全球意义：80/100" in content
    assert "https://github.com/acme/repo-1" in content


def test_report_handles_no_project_and_missing_brief() -> None:
    assert "宁缺毋滥" in render_markdown(report(projects=[], briefs={}))

    content = render_markdown(report(projects=[project()], briefs={}))
    assert "完整情报简报" in content
    assert "全球意义：80/100" in content


def test_report_labels_learning_candidates_without_calling_them_frontier_picks() -> None:
    learning_project = project().model_copy(update={"selection_route": SelectionRoute.LEARNING})
    content = render_markdown(report(projects=[learning_project]))

    assert "值得学习的候选" in content
    assert "学习候选：未达到本周重点推荐阈值" in content


def test_write_markdown_report_is_named_by_iso_week_and_overwrites_atomically(tmp_path: Path) -> None:
    radar_report = report()
    target = write_markdown_report(radar_report, tmp_path)

    assert target.name == "2026-W33.md"
    assert target.read_text(encoding="utf-8") == render_markdown(radar_report)
    assert not list(tmp_path.glob("*.tmp"))
    assert report_filename(radar_report) == "2026-W33.md"
