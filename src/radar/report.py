"""Markdown rendering and atomic storage for weekly Radar reports."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import IntelligenceBrief, RadarReport, RankedCandidate, Recommendation


def report_filename(report: RadarReport) -> str:
    """Use ISO week-year, avoiding calendar-year ambiguity around New Year."""

    week_year, week_number, _ = report.week_start.isocalendar()
    return f"{week_year}-W{week_number:02d}.md"


def render_markdown(report: RadarReport) -> str:
    """Render a complete durable report; missing briefs remain explicitly visible."""

    week_year, week_number, _ = report.week_start.isocalendar()
    lines = [
        "# GitHub Frontier Radar",
        "",
        f"**{week_year} W{week_number:02d}** · {report.week_start.isoformat()} 至 {report.week_end.isoformat()}",
        "",
        f"本周从 {report.total_discovered} 个发现项目中选出 {len(report.projects)} 个值得关注的项目。",
    ]
    if report.weekly_observation:
        lines.extend(["", f"> 本周观察：{report.weekly_observation}"])
    if not report.projects:
        lines.extend(["", "本周 Radar 没有发现达到推荐阈值的项目。宁缺毋滥。"])
        return "\n".join(lines) + "\n"

    for position, ranked in enumerate(report.projects, start=1):
        lines.extend(["", _render_project(position, ranked, report.briefs.get(ranked.candidate.repo_id))])
    return "\n".join(lines) + "\n"


def write_markdown_report(report: RadarReport, reports_dir: str | Path = "reports") -> Path:
    """Atomically persist the rendered report to its ISO-week filename."""

    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / report_filename(report)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=directory, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _render_project(position: int, ranked: RankedCandidate, brief: IntelligenceBrief | None) -> str:
    candidate = ranked.candidate
    lines = [f"## {position}. {candidate.full_name}", ""]
    if candidate.html_url:
        lines.extend([f"GitHub：{candidate.html_url}", ""])
    lines.extend([f"**热度**：{_growth_text(ranked)}", ""])
    if brief is None:
        lines.extend(["本项目未能生成完整情报简报；保留基础趋势信息供后续观察。", "", _score_summary(ranked)])
        return "\n".join(lines)

    lines.extend(
        [
            f"**一句话**：{brief.one_liner}",
            "",
            "### 它能做什么",
            brief.what_it_does,
            "",
            "### 为什么最近受关注",
            f"{brief.why_hot.text}（{_confidence_label(brief.why_hot.confidence)}）",
            "",
            "### 对你的价值",
            brief.why_it_matters_to_user,
            "",
            f"**适合**：{' / '.join(brief.target_users) if brief.target_users else '暂未确认'}",
            "",
            f"**成本**：{_cost_text(brief)}",
            "",
            f"**主要风险**：{brief.main_risk}",
            "",
            f"**结论**：{_recommendation_label(brief.recommendation)} — {brief.recommendation_reason}",
            "",
            _score_summary(ranked),
        ]
    )
    return "\n".join(lines)


def _growth_text(ranked: RankedCandidate) -> str:
    growth = ranked.growth
    if growth is None:
        return "趋势数据暂不可用"
    if growth.star_delta_7d is not None:
        return f"7 日 +{growth.star_delta_7d:,} ⭐"
    if growth.trending_stars is not None:
        return f"Trending +{growth.trending_stars:,} ⭐"
    return "7 日增长数据尚在冷启动积累中"


def _score_summary(ranked: RankedCandidate) -> str:
    scores = ranked.scores
    return " | ".join(
        (
            f"全球意义：{_score_text(scores.global_significance)}",
            f"与你相关：{_score_text(scores.personal_utility)}",
            f"实际价值：{_score_text(scores.practical_value)}",
            f"质量置信：{_score_text(scores.quality_confidence)}",
        )
    )


def _score_text(value: float | None) -> str:
    return f"{value:.0f}/100" if value is not None else "未知"


def _cost_text(brief: IntelligenceBrief) -> str:
    labels = {
        "free": "开源免费",
        "freemium": "免费增值",
        "paid": "付费",
        "self_hosted": "需自行部署",
        "unknown": "成本暂未确认",
    }
    label = labels[brief.cost.type]
    return f"{label}；{brief.cost.note}" if brief.cost.note else label


def _confidence_label(confidence: str) -> str:
    return {"fact": "事实", "likely": "推测", "unknown": "暂未确认"}[confidence]


def _recommendation_label(recommendation: Recommendation) -> str:
    return {
        Recommendation.TRY: "🔥 建议试试",
        Recommendation.SAVE: "⭐ 值得收藏",
        Recommendation.KNOW: "👀 知道即可",
    }[recommendation]
