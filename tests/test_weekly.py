from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from radar.config import LLMConfig, LimitsConfig, QueryBank, RadarConfig, UserProfile, WatchlistConfig
from radar.intelligence import IntelligenceService, LLMProviderError
from radar.models import (
    CostInfo,
    DeliveryResult,
    DeliveryStatus,
    EvidenceItem,
    FrictionInfo,
    IntelligenceBrief,
    RepoCandidate,
    RepoSnapshot,
    TriageResult,
    TrendingRepository,
    WhyHot,
)
from radar.state_store import load_state, record_snapshot, save_state
from radar.weekly import run_weekly_pipeline


RUN_DATE = date(2026, 8, 12)


def candidate(repo_id: int, *, stars: int = 200, archived: bool = False) -> RepoCandidate:
    return RepoCandidate(
        repo_id=repo_id,
        full_name=f"acme/repo-{repo_id}",
        html_url=f"https://github.com/acme/repo-{repo_id}",
        description="A practical browser automation tool.",
        topics=["browser-automation"],
        stars=stars,
        forks=5,
        size_kb=50,
        has_readme=True,
        license_name="MIT",
        created_at="2026-08-01T00:00:00Z",
        pushed_at="2026-08-11T00:00:00Z",
        archived=archived,
    )


class FakeGitHub:
    def __init__(self, candidates: list[RepoCandidate]) -> None:
        self.candidates = {item.repo_id: item for item in candidates}
        self.search_calls = 0
        self.readme_calls: list[str] = []
        self.release_calls: list[str] = []
        self.fail_readme_ids: set[int] = set()

    def fetch_trending(self, period: str) -> list[TrendingRepository]:
        return []

    def search_repositories(self, query: str, *, sort: str = "stars", order: str = "desc") -> list[RepoCandidate]:
        self.search_calls += 1
        return list(self.candidates.values())

    def get_repository(self, repo: int | str) -> RepoCandidate | None:
        if isinstance(repo, int):
            return self.candidates.get(repo)
        repo_id = int(repo.rsplit("-", 1)[-1])
        return self.candidates.get(repo_id)

    def get_readme(self, full_name: str) -> str | None:
        repo_id = int(full_name.rsplit("-", 1)[-1])
        self.readme_calls.append(full_name)
        if repo_id in self.fail_readme_ids:
            raise OSError("README unavailable")
        return "# README\nA safe automation tool."

    def get_latest_release(self, full_name: str):
        self.release_calls.append(full_name)
        return None


class FakeProvider:
    def __init__(self, *, fail_triage_ids: set[int] | None = None) -> None:
        self.fail_triage_ids = fail_triage_ids or set()
        self.calls: list[str] = []

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
        self.calls.append(user_prompt)
        payload = json.loads(user_prompt.split("输入：\n", maxsplit=1)[1])
        if "repositories" in payload:
            repo_ids = [item["repo_id"] for item in payload["repositories"]]
            if any(repo_id in self.fail_triage_ids for repo_id in repo_ids):
                raise LLMProviderError("triage unavailable")
            return {
                "repositories": [
                    {
                        "repo_id": repo_id,
                        "project_nature": "tool",
                        "category": "browser_automation",
                        "plain_summary": "浏览器自动化工具。",
                        "personal_utility": 90,
                        "practical_value": 85,
                        "target_users": ["开发者"],
                        "adoption_friction": 30,
                        "demo_probability": 0.01,
                        "confidence": 0.9,
                    }
                    for repo_id in repo_ids
                ]
            }
        repo_id = payload["repository"]["repo_id"]
        return {
            "repo_id": repo_id,
            "one_liner": "把网页操作变成自动化任务。",
            "what_it_does": "自动化常见浏览器工作流程。",
            "why_hot": {"text": "目前无法确认受关注的具体原因。", "confidence": "unknown"},
            "why_it_matters_to_user": "减少重复网页操作。",
            "target_users": ["开发者"],
            "cost": {"type": "free", "note": "开源免费"},
            "adoption_friction": {"score": 2, "summary": "需要基础环境"},
            "main_risk": "需要在真实流程中验证稳定性。",
            "recommendation": "try",
            "recommendation_reason": "与自动化需求直接相关。",
        }


class FakeDelivery:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cards: list[object] = []

    def deliver(self, cards):
        self.cards = list(cards)
        if self.fail:
            raise RuntimeError("Feishu unavailable")
        return DeliveryResult(status=DeliveryStatus.SENT, cards_sent=len(cards))


class FakeExternalSearch:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def search_why_hot(self, candidate: RepoCandidate, *, max_results: int) -> list[EvidenceItem]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("search unavailable")
        return [EvidenceItem(source="external", fact="Community discussion increased.", confidence="likely")]


def config(*, external: bool = False, max_llm_triage: int = 3, max_final_briefs: int = 2) -> RadarConfig:
    return RadarConfig.model_validate(
        {
            "discovery": {"trending_periods": [], "breakout_windows_days": [30]},
            "limits": {
                "max_daily_candidates": 10,
                "max_tracked_repos": 10,
                "max_weekly_prescore": 3,
                "max_llm_triage": max_llm_triage,
                "llm_triage_batch_size": 2,
                "max_final_briefs": max_final_briefs,
                "final_report_max_projects": 10,
            },
            "external_search": {"enabled": external},
        }
    )


def intelligence(provider: FakeProvider, cfg: RadarConfig) -> IntelligenceService:
    return IntelligenceService(
        provider,
        cfg.limits,
        LLMConfig(structured_output_retries=0),
        UserProfile(interests={"browser_automation": 5}),
    )


def prepare_state(path: Path, repositories: list[RepoCandidate]) -> None:
    state = load_state(path)
    for item in repositories:
        record_snapshot(state, item.model_copy(update={"stars": max(0, item.stars - 100)}), RUN_DATE - __import__("datetime").timedelta(days=7))
        record_snapshot(state, item, RUN_DATE)
    save_state(state, path)


def run(tmp_path: Path, github: FakeGitHub, provider: FakeProvider, delivery: FakeDelivery, **kwargs: object):
    cfg = kwargs.pop("cfg", config())
    state_path = tmp_path / "state.json"
    prepare_state(state_path, list(github.candidates.values()))
    return run_weekly_pipeline(
        github,
        intelligence(provider, cfg),
        delivery,
        cfg,
        QueryBank({"automation": ["automation"]}),
        UserProfile(interests={"browser_automation": 5}),
        WatchlistConfig(),
        state_path=state_path,
        reports_dir=tmp_path / "reports",
        run_date=RUN_DATE,
        **kwargs,
    ), state_path


def test_weekly_happy_path_enforces_budgets_writes_report_delivers_then_marks_featured(tmp_path: Path) -> None:
    github = FakeGitHub([candidate(1), candidate(2, stars=150), candidate(3, stars=120)])
    delivery = FakeDelivery()
    result, state_path = run(
        tmp_path,
        github,
        FakeProvider(),
        delivery,
        report_url="https://github.com/acme/radar/blob/main/reports/2026-W33.md",
    )

    assert result.discovered > 0
    assert result.pre_ranked == 3
    assert result.llm_triaged <= 3
    assert result.final_briefed <= 2
    assert result.selected <= 2
    assert result.cards_sent == 1
    assert Path(result.report_path).exists()  # type: ignore[arg-type]
    state = load_state(state_path)
    featured_ids = {repo_id for repo_id, record in state.repositories.items() if record.feature_count == 1}
    assert len(featured_ids) == result.selected == 2
    assert len(github.readme_calls) == result.llm_triaged
    assert "完整周报" in str(delivery.cards)


def test_one_github_readme_failure_and_one_llm_batch_failure_degrade_without_stopping(tmp_path: Path) -> None:
    github = FakeGitHub([candidate(1), candidate(2, stars=150), candidate(3, stars=120)])
    github.fail_readme_ids.add(3)
    result, _ = run(tmp_path, github, FakeProvider(fail_triage_ids={2, 3}), FakeDelivery())

    assert result.llm_triaged == 1
    assert result.selected == 1
    assert result.cards_sent == 1


def test_external_search_failure_degrades_and_fewer_than_ten_is_valid(tmp_path: Path) -> None:
    github = FakeGitHub([candidate(1)])
    external = FakeExternalSearch(fail=True)
    result, _ = run(tmp_path, github, FakeProvider(), FakeDelivery(), cfg=config(external=True), external_search=external)

    assert external.calls == 1
    assert result.external_researched == 0
    assert result.selected == 1


def test_feishu_failure_does_not_mark_featured_or_save_feature_state(tmp_path: Path) -> None:
    github = FakeGitHub([candidate(1)])
    state_path = tmp_path / "state.json"
    with pytest.raises(RuntimeError, match="Feishu unavailable"):
        run(tmp_path, github, FakeProvider(), FakeDelivery(fail=True))

    state = load_state(state_path)
    assert state.repositories[1].feature_count == 0
    assert state.repositories[1].last_featured is None
