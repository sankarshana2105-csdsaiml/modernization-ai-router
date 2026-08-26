from __future__ import annotations

import httpx
import pytest

from modernization_router.cloud_api import app, get_ai_service
from modernization_router.models import Attempt, RouterResult, Usage


class StubAI:
    async def run(self, task, messages, **kwargs):
        assert task.value == "code_analysis"
        assert messages[0].content == "Inspect this public example"
        assert kwargs["allow_premium_fallback"] is False
        return RouterResult(
            request_id="req-cloud-test",
            provider_id="vercel_gateway_google",
            model_id="cloud-gemini-flash-lite",
            content="No issue found.",
            usage=Usage(input_tokens=20, output_tokens=5, cost_usd=0.0000185),
            attempts=(
                Attempt(
                    provider_id="vercel_gateway_google",
                    model_id="cloud-gemini-flash-lite",
                    attempt_number=1,
                    outcome="success",
                    latency_ms=12.5,
                ),
            ),
            used_premium_fallback=False,
        )


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as instance:
        yield instance


@pytest.mark.asyncio
async def test_health_is_public_and_reports_configuration(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    monkeypatch.delenv("ROUTER_ACCESS_KEY", raising=False)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "gateway_identity_available": False,
        "route_access_key_configured": False,
    }


@pytest.mark.asyncio
async def test_route_is_disabled_until_owner_configures_access_key(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROUTER_ACCESS_KEY", raising=False)

    response = await client.post(
        "/v1/route",
        json={"task": "code_analysis", "messages": [{"role": "user", "content": "x"}]},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_route_rejects_invalid_access_key(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTER_ACCESS_KEY", "correct-secret")

    response = await client.post(
        "/v1/route",
        headers={"Authorization": "Bearer wrong-secret"},
        json={"task": "code_analysis", "messages": [{"role": "user", "content": "x"}]},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_route_returns_structured_router_result(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTER_ACCESS_KEY", "correct-secret")
    app.dependency_overrides[get_ai_service] = lambda: StubAI()
    try:
        response = await client.post(
            "/v1/route",
            headers={"Authorization": "Bearer correct-secret"},
            json={
                "task": "code_analysis",
                "messages": [{"role": "user", "content": "Inspect this public example"}],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_id"] == "cloud-gemini-flash-lite"
    assert payload["content"] == "No issue found."
    assert payload["usage"]["total_tokens"] == 25
    assert payload["attempts"][0]["outcome"] == "success"
