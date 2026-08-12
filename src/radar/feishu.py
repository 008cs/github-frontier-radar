"""Feishu custom-bot Schema 2.0 cards and safe Webhook delivery."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx
from pydantic import SecretStr

from .config import FeishuConfig
from .models import DeliveryResult, DeliveryStatus, IntelligenceBrief, RadarReport, RankedCandidate, Recommendation


class SupportsPost(Protocol):
    def post(self, url: str, *, json: object) -> httpx.Response: ...

    def close(self) -> None: ...


class FeishuDeliveryError(RuntimeError):
    """Webhook delivery failed after bounded transient retries."""


def payload_size_bytes(payload: Mapping[str, object]) -> int:
    """Measure exactly what is sent to Feishu: UTF-8 encoded JSON bytes."""

    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_cards(report: RadarReport, safe_payload_max_bytes: int) -> list[dict[str, object]]:
    """Build one or more cards without ever producing an oversized payload."""

    if safe_payload_max_bytes < 1_000 or safe_payload_max_bytes >= 20_000:
        raise ValueError("safe_payload_max_bytes must be from 1000 to 19999")
    if not report.projects:
        card = _build_card(report, [], 1, 1)
        _assert_card_size(card, safe_payload_max_bytes)
        return [card]

    batches: list[list[RankedCandidate]] = []
    current: list[RankedCandidate] = []
    for project in report.projects:
        proposed = current + [project]
        candidate_card = _build_card(report, proposed, 1, 1)
        if payload_size_bytes(candidate_card) <= safe_payload_max_bytes:
            current = proposed
            continue
        if not current:
            compact = _build_card(report, [project], 1, 1, compact=True)
            _assert_card_size(compact, safe_payload_max_bytes)
            batches.append([project])
            continue
        batches.append(current)
        current = [project]
    if current:
        batches.append(current)

    total = len(batches)
    cards: list[dict[str, object]] = []
    for index, batch in enumerate(batches, start=1):
        card = _build_card(report, batch, index, total)
        if payload_size_bytes(card) > safe_payload_max_bytes:
            card = _build_card(report, batch, index, total, compact=True)
        _assert_card_size(card, safe_payload_max_bytes)
        cards.append(card)
    return cards


class FeishuWebhookClient:
    """Synchronous, injectable Webhook sender that never logs the Webhook URL."""

    def __init__(
        self,
        webhook_url: SecretStr | str,
        config: FeishuConfig,
        *,
        client: SupportsPost | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._webhook_url = webhook_url.get_secret_value() if isinstance(webhook_url, SecretStr) else webhook_url
        if not self._webhook_url:
            raise ValueError("Feishu Webhook URL must not be blank")
        self._config = config
        self._sleep = sleep
        self._client = client or httpx.Client(timeout=config.timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FeishuWebhookClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def deliver(self, cards: Sequence[Mapping[str, object]]) -> DeliveryResult:
        """Deliver all cards in order; one permanent failure fails delivery."""

        for card in cards:
            self._post_with_retry(card)
        return DeliveryResult(status=DeliveryStatus.SENT, cards_sent=len(cards), delivered_at=datetime.now(UTC))

    def _post_with_retry(self, payload: Mapping[str, object]) -> None:
        last_error: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._client.post(self._webhook_url, json=payload)
            except httpx.HTTPError as error:
                last_error = error
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = FeishuDeliveryError(f"Feishu transient HTTP {response.status_code}")
                    retry_after = response.headers.get("Retry-After")
                    response.close()
                    if attempt < self._config.max_retries:
                        self._sleep(_retry_delay(attempt, retry_after, self._config.retry_base_delay_seconds))
                        continue
                    break
                if response.is_error:
                    raise FeishuDeliveryError(f"Feishu returned permanent HTTP {response.status_code}")
                try:
                    body = response.json()
                except ValueError as error:
                    raise FeishuDeliveryError("Feishu returned non-JSON success response") from error
                if not isinstance(body, dict) or body.get("code", 0) != 0:
                    code = body.get("code") if isinstance(body, dict) else None
                    raise FeishuDeliveryError(f"Feishu rejected the card payload (code={code})")
                return
            if attempt < self._config.max_retries:
                self._sleep(_retry_delay(attempt, None, self._config.retry_base_delay_seconds))
        raise FeishuDeliveryError("Feishu delivery retries exhausted") from last_error


def _build_card(report: RadarReport, projects: Sequence[RankedCandidate], page: int, page_total: int, *, compact: bool = False) -> dict[str, object]:
    week_year, week_number, _ = report.week_start.isocalendar()
    suffix = f" {page}/{page_total}" if page_total > 1 else ""
    elements: list[object] = [{"tag": "div", "text": {"tag": "lark_md", "content": f"本周从 **{report.total_discovered}** 个发现项目中选出 **{len(report.projects)}** 个值得关注的项目。"}}]
    if report.weekly_observation:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**本周观察**：{report.weekly_observation}"}})
    if not projects:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "本周 Radar 没有发现达到推荐阈值的项目。宁缺毋滥。"}})
    for ranked in projects:
        elements.extend(_project_elements(ranked, report.briefs.get(ranked.candidate.repo_id), report.report_url, compact))
    return {"msg_type": "interactive", "card": {"schema": "2.0", "config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": f"GitHub Frontier Radar · {week_year} W{week_number:02d}{suffix}"}, "template": "blue"}, "body": {"elements": elements}}}


def _project_elements(ranked: RankedCandidate, brief: IntelligenceBrief | None, report_url: object, compact: bool) -> list[object]:
    candidate = ranked.candidate
    if brief is None:
        content = f"### {candidate.full_name}\n{_growth_text(ranked)}\n\n完整情报暂不可用。"
    elif compact:
        content = f"### {_recommendation_emoji(brief.recommendation)} {candidate.full_name}\n{brief.one_liner}\n\n{_growth_text(ranked)} · 全球 {_score_stars(ranked.scores.global_significance)} · 相关 {_score_stars(ranked.scores.personal_utility)}"
    else:
        content = (
            f"### {_recommendation_emoji(brief.recommendation)} {candidate.full_name}\n{brief.one_liner}\n\n"
            f"**热度**：{_growth_text(ranked)}\n**为什么火**：{brief.why_hot.text}\n"
            f"**它能做什么**：{brief.what_it_does}\n**对你的价值**：{brief.why_it_matters_to_user}\n"
            f"**适合**：{' / '.join(brief.target_users) if brief.target_users else '暂未确认'}\n"
            f"**成本**：{_cost_text(brief)}\n⚠️ {brief.main_risk}\n"
            f"**结论**：{_recommendation_label(brief.recommendation)}\n\n"
            f"热度 {_score_stars(ranked.scores.global_significance)}　与你相关 {_score_stars(ranked.scores.personal_utility)}　实际价值 {_score_stars(ranked.scores.practical_value)}　上手难度 {_friction_stars(brief.adoption_friction.score)}"
        )
    elements: list[object] = [{"tag": "markdown", "content": content}, {"tag": "hr"}]
    actions: list[object] = []
    if candidate.html_url:
        actions.append({"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "打开 GitHub"}, "url": str(candidate.html_url)})
    if report_url:
        actions.append({"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": "完整周报"}, "url": str(report_url)})
    if actions:
        elements.append({"tag": "action", "actions": actions})
    return elements


def _assert_card_size(card: Mapping[str, object], limit: int) -> None:
    size = payload_size_bytes(card)
    if size > limit:
        raise ValueError(f"A single Feishu card is {size} bytes, exceeding configured safe limit {limit}")


def _growth_text(ranked: RankedCandidate) -> str:
    if ranked.growth is None:
        return "趋势数据暂不可用"
    if ranked.growth.star_delta_7d is not None:
        return f"7 日 +{ranked.growth.star_delta_7d:,} ⭐"
    if ranked.growth.trending_stars is not None:
        return f"Trending +{ranked.growth.trending_stars:,} ⭐"
    return "7 日增长仍在冷启动积累中"


def _score_stars(score: float | None) -> str:
    if score is None:
        return "未知"
    filled = max(1, min(5, round(score / 20)))
    return "★" * filled + "☆" * (5 - filled)


def _friction_stars(score: int) -> str:
    return "★" * score + "☆" * (5 - score)


def _cost_text(brief: IntelligenceBrief) -> str:
    labels = {"free": "开源免费", "freemium": "免费增值", "paid": "付费", "self_hosted": "需自行部署", "unknown": "暂未确认"}
    return f"{labels[brief.cost.type]}；{brief.cost.note}" if brief.cost.note else labels[brief.cost.type]


def _recommendation_emoji(recommendation: Recommendation) -> str:
    return {Recommendation.TRY: "🔥", Recommendation.SAVE: "⭐", Recommendation.KNOW: "👀"}[recommendation]


def _recommendation_label(recommendation: Recommendation) -> str:
    return {Recommendation.TRY: "🔥 建议试试", Recommendation.SAVE: "⭐ 值得收藏", Recommendation.KNOW: "👀 知道即可"}[recommendation]


def _retry_delay(attempt: int, retry_after: str | None, base_delay: float) -> float:
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                timestamp = parsedate_to_datetime(retry_after)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                return max(0.0, (timestamp - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError):
                pass
    return base_delay * (2**attempt)
