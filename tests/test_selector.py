from __future__ import annotations

from datetime import date

from radar.config import ScoringConfig, SelectorConfig
from radar.models import (
    ProjectNature,
    RankedCandidate,
    RepoCandidate,
    RepositoryRecord,
    RepeatException,
    ScoreBreakdown,
    TriageResult,
)
from radar.selector import select_projects


AS_OF = date(2026, 8, 12)


def ranked(
    repo_id: int,
    *,
    global_score: float | None = 80,
    utility: float | None = 20,
    practical_value: float | None = None,
    quality: float | None = 80,
    category: str | None = "database",
    nature: ProjectNature = ProjectNature.TOOL,
    exploration: float | None = None,
    **repo_overrides: object,
) -> RankedCandidate:
    candidate = RepoCandidate.model_validate(
        {"repo_id": repo_id, "full_name": f"acme/repo-{repo_id}", "stars": 100, **repo_overrides}
    )
    triage = TriageResult(repo_id=repo_id, category=category, project_nature=nature, personal_utility=utility)
    return RankedCandidate(
        candidate=candidate,
        scores=ScoreBreakdown(
            repo_id=repo_id,
            global_significance=global_score,
            personal_utility=utility,
            practical_value=practical_value,
            quality_confidence=quality,
            exploration_value=exploration,
        ),
        triage=triage,
    )


def select(candidates: list[RankedCandidate], **kwargs: object):
    return select_projects(
        candidates,
        kwargs.pop("history", {}),
        AS_OF,
        ScoringConfig(),
        kwargs.pop("selector_config", SelectorConfig()),
        max_projects=kwargs.pop("max_projects", 10),
        **kwargs,
    )


def history(repo_id: int, featured: date) -> RepositoryRecord:
    return RepositoryRecord(
        repo_id=repo_id,
        full_name=f"acme/repo-{repo_id}",
        first_seen=date(2026, 1, 1),
        last_seen=AS_OF,
        last_featured=featured,
        feature_count=1,
    )


def test_global_only_important_project_qualifies() -> None:
    result = select([ranked(1, global_score=95, utility=10, quality=50)])

    assert [candidate.candidate.repo_id for candidate in result.selected] == [1]
    assert result.decisions[0].route == "global"


def test_utility_only_small_project_qualifies() -> None:
    result = select([ranked(1, global_score=35, utility=92, quality=70)])

    assert [candidate.candidate.repo_id for candidate in result.selected] == [1]
    assert result.decisions[0].route == "utility"


def test_high_global_and_utility_receives_synergy_advantage() -> None:
    both = ranked(1, global_score=90, utility=90, quality=80)
    global_only = ranked(2, global_score=95, utility=10, quality=80)
    result = select([global_only, both])

    assert [candidate.candidate.repo_id for candidate in result.selected] == [1, 2]
    assert result.selected[0].scores.priority > result.selected[1].scores.priority  # type: ignore[operator]


def test_both_weak_and_low_quality_utility_projects_are_excluded() -> None:
    result = select(
        [
            ranked(1, global_score=50, utility=50, quality=90),
            ranked(2, global_score=20, utility=95, quality=40),
        ]
    )

    assert result.selected == []
    assert all(not decision.eligible for decision in result.decisions)


def test_one_strict_recommendation_is_supplemented_to_four_with_learning_candidates() -> None:
    strict = ranked(1, global_score=95, utility=10, quality=75)
    learning_candidates = [
        ranked(
            repo_id,
            global_score=50,
            utility=60,
            quality=75,
            practical_value=65,
            category=f"learning topic {repo_id}",
        )
        for repo_id in range(2, 5)
    ]
    result = select([strict, *learning_candidates])

    assert [item.candidate.repo_id for item in result.selected] == [1, 2, 3, 4]
    assert result.selected[0].selection_route == "global"
    assert [item.selection_route for item in result.selected[1:]] == ["learning"] * 3


def test_strict_empty_week_sends_up_to_three_learning_candidates() -> None:
    candidates = [
        ranked(
            repo_id,
            global_score=50,
            utility=60,
            quality=75,
            practical_value=65,
            category=f"learning topic {repo_id}",
        )
        for repo_id in range(1, 5)
    ]
    result = select(candidates)

    assert [item.candidate.repo_id for item in result.selected] == [1, 2, 3]
    assert all(item.selection_route == "learning" for item in result.selected)


def test_strict_selection_of_four_or_more_does_not_add_learning_candidates() -> None:
    strict = [
        ranked(repo_id, global_score=95, utility=10, quality=75, category=f"strict topic {repo_id}")
        for repo_id in range(1, 5)
    ]
    learning = ranked(
        5,
        global_score=50,
        utility=60,
        quality=75,
        practical_value=65,
        category="learning topic",
    )
    result = select([*strict, learning])

    assert [item.candidate.repo_id for item in result.selected] == [1, 2, 3, 4]
    assert all(item.selection_route != "learning" for item in result.selected)


def test_ten_is_an_upper_limit_not_a_target_quota() -> None:
    strict = [
        ranked(repo_id, global_score=95, utility=10, quality=75, category=f"strict topic {repo_id}")
        for repo_id in range(1, 6)
    ]

    result = select(strict, max_projects=10)

    assert [item.candidate.repo_id for item in result.selected] == [1, 2, 3, 4, 5]
    assert all(item.selection_route != "learning" for item in result.selected)


def test_learning_fallback_never_promotes_demo_or_low_quality_project() -> None:
    demo = ranked(
        1,
        global_score=50,
        utility=99,
        quality=99,
        practical_value=99,
        nature=ProjectNature.DEMO,
    )
    weak = ranked(2, global_score=50, utility=99, quality=49, practical_value=99)

    assert select([demo, weak]).selected == []


def test_cooldown_requires_explicit_repeat_exception() -> None:
    item = ranked(1, global_score=90, utility=20, quality=80)
    result = select([item], history={1: history(1, date(2026, 8, 1))})
    assert result.selected == []
    assert result.decisions[0].rejection_reason == "cooldown"

    exception = select(
        [item],
        history={1: history(1, date(2026, 8, 1))},
        repeat_exceptions=[RepeatException(repo_id=1, reason="major release")],
    )
    assert [candidate.candidate.repo_id for candidate in exception.selected] == [1]


def test_same_topic_cap_allows_only_two_similar_projects() -> None:
    candidates = [ranked(repo_id, global_score=95 - repo_id, utility=80, category="browser agent") for repo_id in range(1, 5)]
    result = select(candidates)

    assert [candidate.candidate.repo_id for candidate in result.selected] == [1, 2]
    assert {decision.rejection_reason for decision in result.decisions if decision.repo_id in {3, 4}} == {
        "same-topic similarity cap reached"
    }


def test_cross_domain_global_significance_receives_exploration_but_not_fixed_quota() -> None:
    database = ranked(1, global_score=85, utility=10, quality=80, category="database")
    browser = ranked(2, global_score=85, utility=10, quality=80, category="browser_automation")
    result = select([browser, database])

    assert result.selected[0].candidate.repo_id == 1
    assert result.selected[0].scores.priority > result.selected[1].scores.priority  # type: ignore[operator]
    assert len(result.selected) == 2


def test_fewer_than_ten_and_zero_projects_are_valid() -> None:
    assert len(select([ranked(1)]).selected) == 1
    assert select([ranked(1, global_score=1, utility=1, quality=80)]).selected == []
    assert select([ranked(1)], max_projects=0).selected == []


def test_output_order_is_deterministic() -> None:
    candidates = [ranked(3, global_score=90, utility=20), ranked(1, global_score=90, utility=20), ranked(2, global_score=90, utility=20)]
    first = select(candidates)
    second = select(list(reversed(candidates)))

    assert [candidate.candidate.repo_id for candidate in first.selected] == [1, 2]
    assert [candidate.candidate.repo_id for candidate in second.selected] == [1, 2]
