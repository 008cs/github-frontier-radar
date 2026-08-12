from __future__ import annotations

from collections.abc import Callable

from radar.config import LLMConfig, LimitsConfig, UserProfile
from radar.intelligence import IntelligenceService, LLMProviderError
from radar.models import (
    EvidenceItem,
    FinalBriefInput,
    GrowthMetrics,
    RepoCandidate,
    ScoreBreakdown,
    TriageInput,
    TriageResult,
)


def candidate(repo_id: int) -> RepoCandidate:
    return RepoCandidate(
        repo_id=repo_id,
        full_name=f"acme/repo-{repo_id}",
        description="A browser automation tool.",
        topics=["automation"],
        stars=200,
    )


def triage_input(repo_id: int, readme: str | None = "README") -> TriageInput:
    return TriageInput(
        candidate=candidate(repo_id),
        growth=GrowthMetrics(repo_id=repo_id, current_stars=200, trending_stars=50),
        readme_excerpt=readme,
    )


def triage_payload(repo_id: int) -> dict[str, object]:
    return {
        "repositories": [
            {
                "repo_id": repo_id,
                "project_nature": "tool",
                "category": "browser_automation",
                "plain_summary": "自动化浏览器任务的工具。",
                "personal_utility": 88,
                "practical_value": 80,
                "target_users": ["开发者"],
                "adoption_friction": 40,
                "demo_probability": 0.05,
                "confidence": 0.9,
            }
        ]
    }


class FakeProvider:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
        self.calls.append((system_prompt, user_prompt))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def service(provider: FakeProvider, **limit_overrides: object) -> IntelligenceService:
    limits = LimitsConfig(
        max_weekly_prescore=40,
        max_llm_triage=limit_overrides.pop("max_llm_triage", 3),
        llm_triage_batch_size=limit_overrides.pop("llm_triage_batch_size", 2),
        max_final_briefs=limit_overrides.pop("max_final_briefs", 2),
        final_report_max_projects=10,
        **limit_overrides,
    )
    return IntelligenceService(
        provider,
        limits,
        LLMConfig(structured_output_retries=1),
        UserProfile(interests={"ai_coding": 5}),
    )


def final_input(repo_id: int = 1, readme: str | None = "README") -> FinalBriefInput:
    triage = TriageResult(**triage_payload(repo_id)["repositories"][0])  # type: ignore[arg-type,index]
    return FinalBriefInput(
        candidate=candidate(repo_id),
        scores=ScoreBreakdown(repo_id=repo_id, global_significance=85, quality_confidence=80),
        triage=triage,
        evidence=[
            EvidenceItem(
                source="github_release",
                fact="v2.0 released 2 days ago",
                confidence="fact",
            )
        ],
        readme_excerpt=readme,
    )


def final_payload(repo_id: int) -> dict[str, object]:
    return {
        "repo_id": repo_id,
        "one_liner": "把浏览器重复操作变成自动化任务。",
        "what_it_does": "为常见浏览器操作提供自动化能力。",
        "why_hot": {"text": "两天前发布 v2.0。", "confidence": "fact"},
        "why_it_matters_to_user": "可减少重复网页操作。",
        "target_users": ["开发者"],
        "cost": {"type": "free", "note": "开源免费。"},
        "adoption_friction": {"score": 2, "summary": "需要基础开发环境。"},
        "main_risk": "项目仍需在实际流程中验证稳定性。",
        "recommendation": "try",
        "recommendation_reason": "与自动化需求直接相关。",
    }


def test_triage_batches_retains_repo_identity_and_caps_readme() -> None:
    provider = FakeProvider(
        [
            {"repositories": [triage_payload(1)["repositories"][0], triage_payload(2)["repositories"][0]]},
            triage_payload(3),
        ]
    )
    long_readme = "a" * 120
    result = service(provider, max_readme_chars_triage=100).semantic_triage(
        [triage_input(1, long_readme), triage_input(2), triage_input(3), triage_input(4)]
    )

    assert [item.repo_id for item in result.results] == [1, 2, 3]
    assert result.unavailable == []
    assert len(provider.calls) == 2
    assert "a" * 100 in provider.calls[0][1]
    assert long_readme not in provider.calls[0][1]


def test_invalid_triage_json_is_retried_then_degraded_without_losing_other_batch() -> None:
    provider = FakeProvider(
        [
            {"repositories": [{"repo_id": 999}]},
            {"repositories": [{"repo_id": 999}]},
            triage_payload(1),
            {"repositories": [{"repo_id": 999}]},
            triage_payload(3),
        ]
    )
    result = service(provider).semantic_triage([triage_input(1), triage_input(2), triage_input(3)])

    assert [item.repo_id for item in result.results] == [1, 3]
    assert result.unavailable[0].repo_ids == [2]
    assert result.unavailable[0].attempts == 3
    assert len(provider.calls) == 5


def test_invalid_item_in_a_triage_batch_does_not_discard_valid_siblings() -> None:
    valid = triage_payload(1)["repositories"][0]
    malformed = {"repo_id": 2, "personal_utility": "not-a-score"}
    provider = FakeProvider(
        [
            {"repositories": [valid, malformed]},
            {"repositories": [valid, malformed]},
            {"repositories": [{"repo_id": 999}]},
        ]
    )
    result = service(provider, max_llm_triage=2, llm_triage_batch_size=2).semantic_triage(
        [triage_input(1), triage_input(2)]
    )

    # First response is retried once, then its valid sibling is salvaged.
    assert [item.repo_id for item in result.results] == [1]
    assert result.unavailable[0].repo_ids == [2]
    assert len(provider.calls) == 3


def test_provider_failure_is_typed_and_does_not_raise_for_triage() -> None:
    provider = FakeProvider(
        [
            LLMProviderError("do not expose provider detail"),
            LLMProviderError("again"),
            LLMProviderError("individual recovery also failed"),
        ]
    )
    result = service(provider).semantic_triage([triage_input(1)])

    assert result.results == []
    assert result.unavailable[0].reason == "LLM provider unavailable"


def test_final_brief_is_validated_capped_and_evidence_prompt_is_constrained() -> None:
    provider = FakeProvider([final_payload(1)])
    long_readme = "a" * 120
    result = service(
        provider, max_readme_chars_triage=100, max_readme_chars_final=100
    ).generate_final_brief(
        [final_input(readme=long_readme)]
    )

    assert result.briefs[0].repo_id == 1
    prompt = provider.calls[0][1]
    assert "a" * 100 in prompt
    assert long_readme not in prompt
    assert "只能引用 evidence 内的事实" in prompt
    assert "目前无法确认受关注的具体原因" in prompt


def test_final_brief_wrong_repo_id_retries_and_degrades() -> None:
    provider = FakeProvider([final_payload(999), final_payload(999)])
    result = service(provider).generate_final_brief([final_input(1)])

    assert result.briefs == []
    assert result.unavailable[0].repo_ids == [1]
    assert result.unavailable[0].reason == "LLM structured output validation failed"


def test_final_brief_budget_is_strictly_enforced() -> None:
    provider = FakeProvider([final_payload(1)])
    result = service(provider, max_final_briefs=1).generate_final_brief([final_input(1), final_input(2)])

    assert [brief.repo_id for brief in result.briefs] == [1]
    assert len(provider.calls) == 1
