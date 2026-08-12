"""Bounded end-to-end weekly Radar orchestration.

This module coordinates adapters but keeps scoring and selection deterministic.
It deliberately uses small protocols so every external dependency is injectable
and all tests remain offline.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import QueryBank, RadarConfig, UserProfile, WatchlistConfig, date_in_timezone
from .daily import _deduplicate_candidates, _discover_candidates, _refresh_candidates
from .feishu import build_cards
from .github_sources import GitHubSourceError
from .intelligence import IntelligenceService
from .models import (
    EvidenceConfidence,
    EvidenceItem,
    FinalBriefInput,
    GrowthMetrics,
    RankedCandidate,
    RadarReport,
    RepoCandidate,
    RepositoryRecord,
    ScoreBreakdown,
    TriageInput,
    WeeklyRunResult,
)
from .report import write_markdown_report
from .scoring import (
    calculate_event_importance,
    calculate_global_significance,
    calculate_growth_metrics,
    calculate_momentum,
    calculate_quality_confidence,
)
from .selector import select_projects
from .state_store import DEFAULT_STATE_PATH, load_state, mark_featured, mark_seen, save_state


LOGGER = logging.getLogger(__name__)


class WeeklyAnalysisUnavailable(RuntimeError):
    """The weekly shortlist could not be safely analysed by the configured LLM."""


class WeeklyGitHubSource(Protocol):
    def search_repositories(self, query: str, *, sort: str = "stars", order: str = "desc") -> list[RepoCandidate]: ...

    def fetch_trending(self, period: str) -> list[object]: ...

    def get_repository(self, repo: int | str) -> RepoCandidate | None: ...

    def get_readme(self, full_name: str) -> str | None: ...

    def get_latest_release(self, full_name: str) -> object | None: ...


class ExternalSearchProvider(Protocol):
    """Optional bounded source for explaining unusually strong popularity signals."""

    def search_why_hot(self, candidate: RepoCandidate, *, max_results: int) -> list[EvidenceItem]: ...


class ReportWriter(Protocol):
    def __call__(self, report: RadarReport, reports_dir: str | Path = "reports") -> Path: ...


class CardBuilder(Protocol):
    def __call__(self, report: RadarReport, safe_payload_max_bytes: int) -> list[dict[str, object]]: ...


class DeliveryClient(Protocol):
    def deliver(self, cards: Sequence[Mapping[str, object]]) -> object: ...


def run_weekly_pipeline(
    github: WeeklyGitHubSource,
    intelligence: IntelligenceService,
    delivery: DeliveryClient,
    config: RadarConfig,
    queries: QueryBank,
    user_profile: UserProfile,
    watchlist: WatchlistConfig,
    *,
    state_path: str | Path = DEFAULT_STATE_PATH,
    reports_dir: str | Path = "reports",
    run_date: date | None = None,
    report_url: str | None = None,
    external_search: ExternalSearchProvider | None = None,
    report_writer: ReportWriter = write_markdown_report,
    card_builder: CardBuilder = build_cards,
    logger: logging.Logger | None = None,
) -> WeeklyRunResult:
    """Run the weekly pipeline, marking featured state only after successful delivery."""

    today = run_date or date_in_timezone(config.timezone)
    run_logger = logger or LOGGER
    state = load_state(state_path)
    discovered, source_failures = _discover_candidates(github, config, queries, watchlist, today, run_logger)
    deduplicated = _deduplicate_candidates(discovered, config.limits.max_daily_candidates)
    refreshed, repository_failures = _refresh_candidates(github, deduplicated, run_logger)

    hard_filtered = [candidate for candidate in refreshed if _passes_hard_filter(candidate)]
    ranked = _rank_candidates(hard_filtered, state.repositories, today, config)
    ranked.sort(key=_prescore_sort_key)
    prescored = ranked[: config.limits.max_weekly_prescore]

    triage_candidates = prescored[: config.limits.max_llm_triage]
    raw_readmes_by_repo = {
        item.candidate.repo_id: _safe_readme(github, item.candidate, run_logger)
        for item in triage_candidates
    }
    triage_inputs = [
        TriageInput(
            candidate=item.candidate,
            growth=item.growth,
            # TriageInput validates its own boundary before IntelligenceService
            # builds the prompt, so cap external README text here as well.
            readme_excerpt=_cap_readme(
                raw_readmes_by_repo[item.candidate.repo_id],
                config.limits.max_readme_chars_triage,
            ),
        )
        for item in triage_candidates
    ]
    triage_run = intelligence.semantic_triage(triage_inputs)
    if triage_inputs and not triage_run.results:
        raise WeeklyAnalysisUnavailable(
            "weekly triage produced no valid LLM analyses; report delivery was skipped"
        )
    triages = {result.repo_id: result for result in triage_run.results}
    triaged = [_with_triage(item, triages.get(item.candidate.repo_id)) for item in prescored if item.candidate.repo_id in triages]
    triaged = [_with_triage_scores(item) for item in triaged]

    selection_before_briefs = select_projects(
        triaged,
        state.repositories,
        today,
        config.scoring,
        config.selector,
        max_projects=config.limits.max_final_briefs,
    )
    candidates_for_brief = selection_before_briefs.selected[: config.limits.max_final_briefs]
    evidence_by_repo, external_researched = _gather_evidence(
        candidates_for_brief, github, config, external_search, run_logger
    )
    final_inputs = [
        FinalBriefInput(
            candidate=item.candidate,
            scores=item.scores,
            triage=item.triage,  # type: ignore[arg-type]
            evidence=evidence_by_repo[item.candidate.repo_id],
            readme_excerpt=_cap_readme(
                raw_readmes_by_repo.get(item.candidate.repo_id),
                config.limits.max_readme_chars_final,
            ),
        )
        for item in candidates_for_brief
        if item.triage is not None
    ]
    final_run = intelligence.generate_final_brief(final_inputs)
    briefs = {brief.repo_id: brief for brief in final_run.briefs}
    final_candidates = [item for item in candidates_for_brief if item.candidate.repo_id in briefs]
    final_selection = select_projects(
        final_candidates,
        state.repositories,
        today,
        config.scoring,
        config.selector,
        max_projects=config.limits.final_report_max_projects,
    )

    week_start = today - timedelta(days=today.weekday())
    report = RadarReport(
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        generated_at=datetime.now(ZoneInfo(config.timezone)),
        total_discovered=len(discovered),
        projects=final_selection.selected,
        briefs={item.candidate.repo_id: briefs[item.candidate.repo_id] for item in final_selection.selected},
        weekly_observation=_weekly_observation(final_selection.selected),
        report_url=report_url,
    )
    report_path = report_writer(report, reports_dir)
    cards = card_builder(report, config.feishu.safe_payload_max_bytes)
    delivery_result = delivery.deliver(cards)

    # This is intentionally the final state mutation.  A report write or
    # delivery exception leaves feature history untouched for a later retry.
    for item in final_selection.selected:
        # The weekly source can surface a newly trending repository before the
        # daily snapshot job has admitted it to the bounded tracking pool.
        # It was nevertheless delivered, so preserve its feature history
        # rather than failing after the user has already received the report.
        mark_seen(state, item.candidate, today)
        mark_featured(state, item.candidate.repo_id, today)
    save_state(state, state_path)

    result = WeeklyRunResult(
        run_date=today,
        discovered=len(discovered),
        hard_filtered=len(hard_filtered),
        pre_ranked=len(prescored),
        llm_triaged=len(triage_run.results),
        external_researched=external_researched,
        final_briefed=len(final_run.briefs),
        selected=len(final_selection.selected),
        cards_sent=getattr(delivery_result, "cards_sent", len(cards)),
        source_failures=source_failures,
        repository_failures=repository_failures,
        report_path=str(report_path),
    )
    run_logger.info(
        "weekly discovered=%s hard_filtered=%s pre_ranked=%s llm_triaged=%s external_researched=%s "
        "final_briefed=%s selected=%s cards_sent=%s",
        result.discovered,
        result.hard_filtered,
        result.pre_ranked,
        result.llm_triaged,
        result.external_researched,
        result.final_briefed,
        result.selected,
        result.cards_sent,
    )
    return result


def _rank_candidates(
    candidates: Sequence[RepoCandidate],
    records: Mapping[int, RepositoryRecord],
    today: date,
    config: RadarConfig,
) -> list[RankedCandidate]:
    growth_by_repo = {
        candidate.repo_id: calculate_growth_metrics(
            candidate, getattr(records.get(candidate.repo_id), "snapshots", []), today
        )
        for candidate in candidates
    }
    momentum_by_repo = calculate_momentum(growth_by_repo, config.scoring)
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        event = calculate_event_importance(
            candidate, today, config.scoring, momentum=momentum_by_repo[candidate.repo_id]
        )
        quality = calculate_quality_confidence(candidate, today, config.scoring)
        global_score = calculate_global_significance(
            candidate.repo_id,
            config.scoring,
            momentum=momentum_by_repo[candidate.repo_id],
            event_importance=event,
        )
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                growth=growth_by_repo[candidate.repo_id],
                scores=ScoreBreakdown(
                    repo_id=candidate.repo_id,
                    momentum=momentum_by_repo[candidate.repo_id].score,
                    momentum_components=momentum_by_repo[candidate.repo_id].components,
                    event_importance=event.score,
                    quality_confidence=quality.score,
                    global_significance=global_score.score,
                ),
            )
        )
    return ranked


def _gather_evidence(
    candidates: Sequence[RankedCandidate],
    github: WeeklyGitHubSource,
    config: RadarConfig,
    external_search: ExternalSearchProvider | None,
    logger: logging.Logger,
) -> tuple[dict[int, list[EvidenceItem]], int]:
    evidence_by_repo: dict[int, list[EvidenceItem]] = {}
    external_researched = 0
    remaining_external = config.limits.max_external_research
    for item in candidates:
        candidate = item.candidate
        evidence: list[EvidenceItem] = []
        if candidate.created_at:
            evidence.append(
                EvidenceItem(
                    source="github_repository",
                    fact=f"仓库创建于 {candidate.created_at.date().isoformat()}",
                    published_at=candidate.created_at,
                    confidence=EvidenceConfidence.FACT,
                )
            )
        if item.growth and item.growth.star_delta_7d is not None:
            evidence.append(EvidenceItem(source="radar_snapshot", fact=f"Radar 快照显示 7 日新增 {item.growth.star_delta_7d} Star", confidence=EvidenceConfidence.FACT))
        elif item.growth and item.growth.trending_stars is not None:
            evidence.append(EvidenceItem(source="github_trending", fact=f"GitHub Trending 显示本期新增 {item.growth.trending_stars} Star", confidence=EvidenceConfidence.FACT))
        release = _safe_release(github, candidate, logger)
        if release and getattr(release, "published_at", None):
            evidence.append(EvidenceItem(source="github_release", fact=f"{getattr(release, 'tag_name', '最新版本')} 发布于 {release.published_at.date().isoformat()}", published_at=release.published_at, url=getattr(release, "html_url", None), confidence=EvidenceConfidence.FACT))
        needs_external = (
            config.external_search.enabled
            and external_search is not None
            and remaining_external > 0
            and (item.scores.global_significance or 0) >= config.scoring.global_entry
            and not any(entry.source == "github_release" for entry in evidence)
        )
        if needs_external:
            try:
                evidence.extend(external_search.search_why_hot(candidate, max_results=3))
                external_researched += 1
                remaining_external -= 1
            except Exception as error:  # Provider boundary: external search is always degradable.
                logger.warning("External why-hot research failed for %s; continuing: %s", candidate.full_name, type(error).__name__)
        evidence_by_repo[candidate.repo_id] = evidence
    return evidence_by_repo, external_researched


def _safe_readme(github: WeeklyGitHubSource, candidate: RepoCandidate, logger: logging.Logger) -> str | None:
    try:
        return github.get_readme(candidate.full_name)
    except (GitHubSourceError, OSError, ValueError) as error:
        logger.warning("README unavailable for %s; continuing: %s", candidate.full_name, error)
        return None


def _cap_readme(readme: str | None, maximum: int) -> str | None:
    """Keep externally fetched README text within the receiving model's limit."""

    return readme[:maximum] if readme is not None else None


def _safe_release(github: WeeklyGitHubSource, candidate: RepoCandidate, logger: logging.Logger):
    try:
        return github.get_latest_release(candidate.full_name)
    except (GitHubSourceError, OSError, ValueError) as error:
        logger.warning("Release unavailable for %s; continuing: %s", candidate.full_name, error)
        return None


def _passes_hard_filter(candidate: RepoCandidate) -> bool:
    return not (
        candidate.archived
        or candidate.mirror
        or candidate.template
        or candidate.fork
        or candidate.size_kb == 0
    )


def _prescore_sort_key(candidate: RankedCandidate) -> tuple[float, float, int]:
    return (
        -(candidate.scores.global_significance or 0),
        -(candidate.scores.quality_confidence or 0),
        candidate.candidate.repo_id,
    )


def _with_triage(candidate: RankedCandidate, triage) -> RankedCandidate:
    return candidate.model_copy(update={"triage": triage})


def _with_triage_scores(candidate: RankedCandidate) -> RankedCandidate:
    triage = candidate.triage
    if triage is None:
        return candidate
    return candidate.model_copy(
        update={
            "scores": candidate.scores.model_copy(
                update={
                    "personal_utility": triage.personal_utility,
                    "practical_value": triage.practical_value,
                }
            )
        }
    )


def _weekly_observation(selected: Sequence[RankedCandidate]) -> str | None:
    if not selected:
        return None
    clusters = [item.topic_cluster for item in selected if item.topic_cluster]
    if not clusters:
        return "本周项目分布较为分散，未出现单一主导主题。"
    most_common = max(sorted(set(clusters)), key=clusters.count)
    return f"本周 {most_common.replace('_', ' ')} 方向的信号较集中。"
