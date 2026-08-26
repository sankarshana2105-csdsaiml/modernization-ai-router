from __future__ import annotations

import json

import httpx
import pytest

from modernization_router.adapters.gemini import GeminiAdapter
from modernization_router.adapters.openai_compatible import OpenAICompatibleAdapter
from modernization_router.models import (
    ChatMessage,
    ModelConfig,
    ProviderConfig,
    RouterRequest,
    TaskType,
)


def model(provider_id: str, upstream: str) -> ModelConfig:
    return ModelConfig(
        id=f"{provider_id}-model",
        provider_id=provider_id,
        upstream_model=upstream,
        capabilities=frozenset({TaskType.CODE_ANALYSIS.value}),
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
    )


def request() -> RouterRequest:
    return RouterRequest(
        task=TaskType.CODE_ANALYSIS,
        messages=[ChatMessage(role="user", content="inspect")],
        max_output_tokens=20,
    )


@pytest.mark.asyncio
async def test_openai_compatible_adapter_preserves_base_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUMMY_PROVIDER_KEY", "dummy-key")

    async def handler(incoming: httpx.Request) -> httpx.Response:
        assert incoming.url.path == "/api/v1/chat/completions"
        assert incoming.headers["authorization"].startswith("Bearer ")
        payload = json.loads(incoming.content)
        assert payload["model"] == "free/coder"
        return httpx.Response(
            200,
            headers={"x-request-id": "req-test"},
            json={
                "choices": [{"message": {"content": "converted"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    client = httpx.AsyncClient(
        base_url="https://provider.invalid/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    config = ProviderConfig(
        id="compatible",
        adapter="openai_compatible",
        base_url="https://provider.invalid/api/v1",
        api_key_env="DUMMY_PROVIDER_KEY",
    )
    adapter = OpenAICompatibleAdapter(config, client=client)

    response = await adapter.generate(request(), model("compatible", "free/coder"))

    assert response.content == "converted"
    assert response.provider_request_id == "req-test"
    assert response.usage.cost_usd == pytest.approx(0.0002)
    await client.aclose()


@pytest.mark.asyncio
async def test_gemini_adapter_uses_official_generate_content_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUMMY_GEMINI_KEY", "dummy-key")

    async def handler(incoming: httpx.Request) -> httpx.Response:
        assert incoming.url.path == "/v1beta/models/gemini-test:generateContent"
        assert incoming.headers.get("x-goog-api-key")
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "part one"}, {"text": " part two"}]}}
                ],
                "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10},
            },
        )

    client = httpx.AsyncClient(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        transport=httpx.MockTransport(handler),
    )
    config = ProviderConfig(
        id="gemini",
        adapter="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="DUMMY_GEMINI_KEY",
    )
    adapter = GeminiAdapter(config, client=client)

    response = await adapter.generate(request(), model("gemini", "gemini-test"))

    assert response.content == "part one part two"
    assert response.usage.total_tokens == 30
    await client.aclose()
