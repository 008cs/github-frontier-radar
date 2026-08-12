from __future__ import annotations

import json

import httpx
import pytest

from radar.config import LLMConfig
from radar.intelligence import LLMProviderError
from radar.openai_provider import OpenAICompatibleProvider


def test_openai_compatible_provider_requests_json_mode_and_parses_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        "test-key",
        LLMConfig(model="test-model", api_base_url="https://llm.example/v1"),
        client=client,
    )

    assert provider.complete_json(system_prompt="system", user_prompt="user") == {"answer": "ok"}
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"] == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    assert "test-key" not in repr(provider)


def test_openai_compatible_provider_masks_api_response_details() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret")))
    provider = OpenAICompatibleProvider("test-key", LLMConfig(model="test-model"), client=client)

    with pytest.raises(LLMProviderError, match="HTTP 401") as error:
        provider.complete_json(system_prompt="system", user_prompt="user")

    assert "secret" not in str(error.value)
