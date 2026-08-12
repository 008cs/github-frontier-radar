from __future__ import annotations

from datetime import date, datetime, timezone
from typing import cast

import httpx
import pytest

from radar.config import FeishuConfig
from radar.feishu import FeishuDeliveryError, FeishuWebhookClient, build_cards, payload_size_bytes
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


def project(repo_id: int, *, long: bool = False, unknown_growth: bool = False) -> RankedCandidate:
    text = "长内容" * 1_500 if long else "简短描述"
    return RankedCandidate(
        candidate=RepoCandidate(repo_id=repo_id, full_name=f"acme/repo-{repo_id}", html_url=f"https://github.com/acme/repo-{repo_id}", stars=100),
        growth=GrowthMetrics(repo_id=repo_id, current_stars=100) if unknown_growth else GrowthMetrics(repo_id=repo_id, current_stars=100, baseline_stars=20, star_delta_7d=80, has_complete_7d_history=True),
        scores=ScoreBreakdown(repo_id=repo_id, global_significance=80, personal_utility=90, practical_value=85, quality_confidence=75),
        topic_cluster="browser_automation",
    )


def brief(repo_id: int, *, long: bool = False) -> IntelligenceBrief:
    text = "项目说明" * 250 if long else "将网页操作变成可重复的自动化任务。"
    return IntelligenceBrief(
        repo_id=repo_id,
        one_liner=text,
        what_it_does=text,
        why_hot=WhyHot(text=text, confidence="fact"),
        why_it_matters_to_user=text,
        target_users=["开发者"],
        cost=CostInfo(type="free", note="开源免费"),
        adoption_friction=FrictionInfo(score=2, summary="需要基础环境"),
        main_risk=text,
        recommendation=Recommendation.TRY,
        recommendation_reason=text,
    )


def report(projects: list[RankedCandidate], briefs: dict[int, IntelligenceBrief]) -> RadarReport:
    return RadarReport(
        week_start=date(2026, 8, 10),
        week_end=date(2026, 8, 16),
        generated_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        total_discovered=100,
        weekly_observation="本周自动化工具持续升温。",
        report_url="https://github.com/acme/radar/blob/main/reports/2026-W33.md",
        projects=projects,
        briefs=briefs,
    )


def test_card_contains_required_content_urls_and_unknown_growth() -> None:
    radar_report = report([project(1, unknown_growth=True)], {1: brief(1)})
    card = build_cards(radar_report, 17_000)[0]
    serialized = str(card)

    assert card["msg_type"] == "interactive"
    assert card["card"]["schema"] == "2.0"  # type: ignore[index]
    assert "7 日增长仍在冷启动积累中" in serialized
    assert "打开 GitHub" in serialized
    assert "完整周报" in serialized
    assert "https://github.com/acme/repo-1" in serialized
    assert "https://github.com/acme/radar" in serialized
    assert "callback" not in serialized.lower()


def test_card_uses_schema_v2_direct_url_buttons_not_legacy_action_container() -> None:
    card = build_cards(report([project(1)], {1: brief(1)}), 17_000)[0]
    schema = cast(dict[str, object], card["card"])
    config = cast(dict[str, object], schema["config"])
    body = cast(dict[str, object], schema["body"])
    elements = cast(list[dict[str, object]], body["elements"])
    buttons = [element for element in elements if element.get("tag") == "button"]

    assert config == {"update_multi": True}
    assert body["direction"] == "vertical"
    assert not any(element.get("tag") == "action" for element in elements)
    assert len(buttons) == 2
    assert buttons[0]["behaviors"] == [{"type": "open_url", "default_url": "https://github.com/acme/repo-1"}]
    assert buttons[1]["behaviors"] == [{"type": "open_url", "default_url": "https://github.com/acme/radar/blob/main/reports/2026-W33.md"}]


def test_card_labels_learning_candidate_and_summary() -> None:
    learning_project = project(1).model_copy(update={"selection_route": SelectionRoute.LEARNING})
    card = build_cards(report([learning_project], {1: brief(1)}), 17_000)[0]
    serialized = str(card)

    assert "学习项目" in serialized
    assert "🧭 学习候选" in serialized


def test_card_shortens_long_what_it_does_and_personal_value() -> None:
    long_brief = brief(1).model_copy(
        update={
            "what_it_does": "第一句工具说明。第二句工具说明。第三句不应显示。",
            "why_it_matters_to_user": "第一句个人价值。第二句个人价值。第三句不应显示。",
        }
    )
    card = build_cards(report([project(1)], {1: long_brief}), 17_000)[0]
    serialized = str(card)

    assert "第一句工具说明。第二句工具说明。" in serialized
    assert "第一句个人价值。第二句个人价值。" in serialized
    assert "第三句不应显示。" not in serialized


def test_ten_project_report_and_long_chinese_content_are_split_under_safe_byte_limit() -> None:
    projects = [project(repo_id, long=True) for repo_id in range(1, 11)]
    radar_report = report(projects, {repo_id: brief(repo_id, long=True) for repo_id in range(1, 11)})
    cards = build_cards(radar_report, 8_000)

    assert len(cards) > 1
    assert all(payload_size_bytes(card) <= 8_000 for card in cards)
    assert "1/" in str(cards[0])


def test_unsplittable_single_project_raises_before_any_delivery() -> None:
    radar_report = report([project(1, long=True)], {1: brief(1, long=True)})
    with pytest.raises(ValueError, match="exceeding configured safe limit"):
        build_cards(radar_report, 1_000)


def test_webhook_success_and_transient_retry_without_exposing_url() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": 0})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sender = FeishuWebhookClient("https://example.invalid/hook/secret", FeishuConfig(), client=client, sleep=sleeps.append)
    result = sender.deliver(build_cards(report([project(1)], {1: brief(1)}), 17_000))

    assert result.cards_sent == 1
    assert attempts == 2
    assert sleeps == [1]
    assert "secret" not in repr(sender)


def test_webhook_timeout_and_api_error_raise_typed_delivery_error() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    timeout_client = httpx.Client(transport=httpx.MockTransport(timeout_handler))
    timeout_sender = FeishuWebhookClient("https://example.invalid/hook", FeishuConfig(max_retries=0), client=timeout_client)
    with pytest.raises(FeishuDeliveryError, match="retries exhausted"):
        timeout_sender.deliver([{"msg_type": "interactive"}])

    error_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 19001})))
    error_sender = FeishuWebhookClient("https://example.invalid/hook", FeishuConfig(), client=error_client)
    with pytest.raises(FeishuDeliveryError, match="rejected"):
        error_sender.deliver([{"msg_type": "interactive"}])
