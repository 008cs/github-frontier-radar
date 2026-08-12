"""Pure, deterministic Route A / Route B project selection.

The selector consumes prepared candidates and persistent history as values. It
does not access the filesystem, network, LLMs, or delivery services.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from .config import ScoringConfig, SelectorConfig
from .models import (
    ProjectNature,
    RankedCandidate,
    RepeatException,
    RepositoryRecord,
    SelectionDecision,
    SelectionResult,
    SelectionRoute,
)


def select_projects(
    candidates: Sequence[RankedCandidate],
    history: Mapping[int, RepositoryRecord],
    as_of: date,
    scoring_config: ScoringConfig,
    selector_config: SelectorConfig,
    *,
    repeat_exceptions: Iterable[RepeatException] = (),
    max_projects: int = 10,
) -> SelectionResult:
    """Return at most ``max_projects`` deterministically selected candidates.

    A candidate may enter via Global significance or Personal utility.  Scores
    are not averaged; a dominant signal remains dominant and a secondary signal
    supplies a modest synergy bonus.  Exploration only rewards *important and
    novel* projects, never novelty in isolation.
    """

    if max_projects < 0 or max_projects > 10:
        raise ValueError("max_projects must be between 0 and 10")
    exceptions = {exception.repo_id: exception for exception in repeat_exceptions}
    decisions: list[SelectionDecision] = []
    qualifying: list[tuple[RankedCandidate, SelectionDecision]] = []
    learning_candidates: list[tuple[RankedCandidate, SelectionDecision]] = []

    for candidate in sorted(candidates, key=lambda item: item.candidate.repo_id):
        decision = _evaluate_candidate(
            candidate,
            history.get(candidate.candidate.repo_id),
            as_of,
            scoring_config,
            selector_config,
            candidate.candidate.repo_id in exceptions,
        )
        decisions.append(decision)
        if decision.eligible:
            enriched = _enrich_selected_candidate(candidate, decision)
            qualifying.append((enriched, decision))
        elif _is_learning_candidate(candidate, decision, selector_config):
            learning_candidates.append((candidate, decision))

    qualifying.sort(
        key=lambda item: (
            -(item[1].priority or 0),
            -_safe_score(item[0].scores.global_significance),
            -_safe_score(item[0].scores.personal_utility),
            item[0].candidate.repo_id,
        )
    )
    topic_counts: Counter[str] = Counter()
    selected: list[RankedCandidate] = []
    selected_ids: set[int] = set()
    for candidate, decision in qualifying:
        if len(selected) >= max_projects:
            break
        cluster = decision.topic_cluster or "other"
        if topic_counts[cluster] >= selector_config.same_topic_cap:
            decisions[_decision_index(decisions, candidate.candidate.repo_id)] = decision.model_copy(
                update={"eligible": False, "rejection_reason": "same-topic similarity cap reached"}
            )
            continue
        topic_counts[cluster] += 1
        selected.append(candidate)
        selected_ids.add(candidate.candidate.repo_id)

    # Strict candidates always win.  When one to three survive, supplement to
    # four total study items.  A strict-empty week sends up to three safe,
    # relevant study leads instead of an empty report.  These are explicitly
    # labelled as learning candidates, never as high-confidence frontier picks.
    fallback_target = _learning_fallback_target(
        strict_count=len(selected), selector_config=selector_config, max_projects=max_projects
    )
    if len(selected) < fallback_target:
        learning_candidates.sort(key=_learning_sort_key)
        for candidate, rejected_decision in learning_candidates:
            if len(selected) >= fallback_target or len(selected) >= max_projects:
                break
            cluster = infer_topic_cluster(candidate)
            if topic_counts[cluster] >= selector_config.same_topic_cap:
                continue
            decision = rejected_decision.model_copy(
                update={
                    "eligible": True,
                    "route": SelectionRoute.LEARNING,
                    "priority": calculate_priority(candidate, selector_config),
                    "rejection_reason": None,
                }
            )
            enriched = _enrich_selected_candidate(candidate, decision)
            decisions[_decision_index(decisions, candidate.candidate.repo_id)] = decision
            topic_counts[cluster] += 1
            selected.append(enriched)
            selected_ids.add(candidate.candidate.repo_id)

    for index, decision in enumerate(decisions):
        if decision.eligible and decision.repo_id not in selected_ids:
            decisions[index] = decision.model_copy(
                update={"eligible": False, "rejection_reason": "final project limit reached"}
            )
    return SelectionResult(selected=selected, decisions=decisions)


def _learning_fallback_target(
    *, strict_count: int, selector_config: SelectorConfig, max_projects: int
) -> int:
    """Return the desired total after strict selection, never exceeding the caller's cap."""

    if strict_count == 0:
        return min(selector_config.learning_only_max_projects, max_projects)
    if strict_count < selector_config.learning_fill_target:
        return min(selector_config.learning_fill_target, max_projects)
    return strict_count


def _enrich_selected_candidate(
    candidate: RankedCandidate, decision: SelectionDecision
) -> RankedCandidate:
    return candidate.model_copy(
        update={
            "scores": candidate.scores.model_copy(update={"priority": decision.priority}),
            "topic_cluster": decision.topic_cluster,
            "selection_route": decision.route,
        }
    )


def _evaluate_candidate(
    ranked: RankedCandidate,
    history: RepositoryRecord | None,
    as_of: date,
    scoring: ScoringConfig,
    selector: SelectorConfig,
    has_repeat_exception: bool,
) -> SelectionDecision:
    repo_id = ranked.candidate.repo_id
    global_score = ranked.scores.global_significance
    utility_score = ranked.scores.personal_utility
    quality_score = ranked.scores.quality_confidence
    cluster = infer_topic_cluster(ranked)

    if quality_score is None:
        return SelectionDecision(repo_id=repo_id, eligible=False, topic_cluster=cluster, rejection_reason="quality unknown")
    if _is_hard_rejected(ranked):
        return SelectionDecision(repo_id=repo_id, eligible=False, topic_cluster=cluster, rejection_reason="hard quality filter")
    if history and history.last_featured is not None:
        elapsed_days = (as_of - history.last_featured).days
        if elapsed_days < selector.cooldown_days and not has_repeat_exception:
            return SelectionDecision(repo_id=repo_id, eligible=False, topic_cluster=cluster, rejection_reason="cooldown")

    via_global = (
        global_score is not None
        and global_score >= scoring.global_entry
        and quality_score >= scoring.global_min_quality
    )
    via_utility = (
        utility_score is not None
        and utility_score >= scoring.utility_entry
        and quality_score >= scoring.utility_min_quality
    )
    if not via_global and not via_utility:
        return SelectionDecision(repo_id=repo_id, eligible=False, topic_cluster=cluster, rejection_reason="entry thresholds not met")

    if _is_low_significance_demo(ranked, selector):
        return SelectionDecision(repo_id=repo_id, eligible=False, topic_cluster=cluster, rejection_reason="low-significance demo or meme")

    route = SelectionRoute.BOTH if via_global and via_utility else SelectionRoute.GLOBAL if via_global else SelectionRoute.UTILITY
    priority = calculate_priority(ranked, selector)
    return SelectionDecision(repo_id=repo_id, eligible=True, route=route, priority=priority, topic_cluster=cluster)


def calculate_priority(candidate: RankedCandidate, config: SelectorConfig) -> float:
    """OR-style final priority normalized to 0–100, not an equal average."""

    global_score = _safe_score(candidate.scores.global_significance)
    utility_score = _safe_score(candidate.scores.personal_utility)
    dominant = max(global_score, utility_score)
    secondary = min(global_score, utility_score)
    exploration = _exploration_bonus(candidate, config)
    # The theoretical base maximum is 100 × (1 + secondary weight).  Normalizing
    # before the exploration bonus avoids cap-induced ties: a 90/90 project
    # remains ahead of a 95/10 project rather than both flattening at 100.
    base = (dominant + config.priority_secondary_weight * secondary) / (
        1 + config.priority_secondary_weight
    )
    return min(100.0, base + exploration)


def infer_topic_cluster(candidate: RankedCandidate) -> str:
    """Use LLM category when present, otherwise deterministic topic/language fallback."""

    if candidate.triage and candidate.triage.category and candidate.triage.category.strip():
        return _normalize_cluster(candidate.triage.category)
    for topic in candidate.candidate.topics:
        normalized = _normalize_cluster(topic)
        if normalized:
            return normalized
    if candidate.candidate.language:
        return f"language_{_normalize_cluster(candidate.candidate.language)}"
    return "other"


def _exploration_bonus(candidate: RankedCandidate, config: SelectorConfig) -> float:
    if not config.global_domain_novelty_bonus:
        return 0.0
    global_score = candidate.scores.global_significance
    if global_score is None:
        return 0.0
    novelty = candidate.scores.exploration_value
    if novelty is not None:
        # Scoring stage defines ExplorationValue as global significance × novelty.
        return min(config.exploration_bonus_max, novelty / 100 * config.exploration_bonus_max)
    cluster = infer_topic_cluster(candidate)
    known_clusters = {
        "ai_coding",
        "desktop_automation",
        "browser_automation",
        "data_collection",
        "developer_tools",
        "finance_quant",
    }
    return config.exploration_bonus_max if cluster not in known_clusters else 0.0


def _is_hard_rejected(candidate: RankedCandidate) -> bool:
    repo = candidate.candidate
    return repo.archived or repo.mirror or repo.template or repo.fork


def _is_low_significance_demo(candidate: RankedCandidate, config: SelectorConfig) -> bool:
    nature = candidate.triage.project_nature if candidate.triage else ProjectNature.UNKNOWN
    if nature not in {ProjectNature.DEMO, ProjectNature.MEME}:
        return False
    return _safe_score(candidate.scores.global_significance) < config.demo_global_override


def _is_learning_candidate(
    candidate: RankedCandidate, decision: SelectionDecision, config: SelectorConfig
) -> bool:
    """Allow a vetted study lead only after the strict routes decline it."""

    if decision.rejection_reason != "entry thresholds not met":
        return False
    if candidate.triage is None:
        return False
    if candidate.triage.project_nature in {ProjectNature.DEMO, ProjectNature.MEME}:
        return False
    return (
        _safe_score(candidate.scores.quality_confidence) >= config.learning_min_quality
        and _safe_score(candidate.scores.personal_utility) >= config.learning_min_personal_utility
        and _safe_score(candidate.scores.practical_value) >= config.learning_min_practical_value
    )


def _learning_sort_key(item: tuple[RankedCandidate, SelectionDecision]) -> tuple[float, float, float, int]:
    candidate, _ = item
    return (
        -_safe_score(candidate.scores.personal_utility),
        -_safe_score(candidate.scores.practical_value),
        -_safe_score(candidate.scores.quality_confidence),
        candidate.candidate.repo_id,
    )


def _normalize_cluster(value: str) -> str:
    return "_".join(part for part in value.strip().lower().replace("-", " ").split() if part)


def _safe_score(value: float | None) -> float:
    return value if value is not None else 0.0


def _decision_index(decisions: Sequence[SelectionDecision], repo_id: int) -> int:
    for index, decision in enumerate(decisions):
        if decision.repo_id == repo_id:
            return index
    raise RuntimeError("selection decision missing")
