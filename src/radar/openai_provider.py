"""Minimal OpenAI-compatible structured-output client.

The domain layer validates every returned object against Radar's Pydantic
schemas.  This adapter is deliberately limited to one Chat Completions request
and never logs prompts, responses, or credentials.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

import httpx
from pydantic import SecretStr

from .config import LLMConfig
from .intelligence import LLMProviderError


class SupportsPost(Protocol):
    """Small, testable slice of a synchronous HTTP client."""

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: object,
    ) -> httpx.Response: ...

    def close(self) -> None: ...


class OpenAICompatibleProvider:
    """Use Chat Completions JSON mode from an OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: SecretStr | str,
        config: LLMConfig,
        *,
        client: SupportsPost | None = None,
    ) -> None:
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not self._api_key:
            raise ValueError("LLM API key must not be blank")
        if not config.model:
            raise ValueError("LLM model must not be blank")
        if not config.api_base_url:
            raise ValueError("LLM API base URL must not be blank")
        self._config = config
        self._endpoint = f"{config.api_base_url.rstrip('/')}/chat/completions"
        self._client = client or httpx.Client(timeout=config.timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenAICompatibleProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
        """Return a parsed JSON object, leaving schema validation to the caller."""

        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        try:
            response = self._client.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as error:
            raise LLMProviderError("LLM transport request failed") from error

        try:
            if response.is_error:
                raise LLMProviderError(f"LLM returned HTTP {response.status_code}")
            body = response.json()
        except (ValueError, httpx.HTTPError) as error:
            raise LLMProviderError("LLM returned an unreadable response") from error
        finally:
            response.close()

        try:
            choices = body["choices"]
            content = choices[0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise LLMProviderError("LLM response did not contain JSON content") from error
