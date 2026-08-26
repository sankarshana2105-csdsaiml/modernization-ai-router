from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from ..exceptions import ProviderAuthenticationError, ProviderTimeoutError, TransientProviderError
from ..models import ModelConfig, ProviderConfig, ProviderResponse, RouterRequest, Usage
from .base import ProviderAdapter
from .http_errors import raise_for_provider_status


class GeminiAdapter(ProviderAdapter):
    """Official Gemini generateContent adapter; no browser/session credential reuse."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config.id)
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{config.base_url.rstrip('/')}/",
            timeout=httpx.Timeout(config.timeout_seconds),
        )

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key_env:
            raise ProviderAuthenticationError(
                "Gemini provider requires an API key environment name"
            )
        key = os.getenv(self.config.api_key_env)
        if not key:
            raise ProviderAuthenticationError(
                f"Required credential {self.config.api_key_env} is not configured"
            )
        return {
            "content-type": "application/json",
            "x-goog-api-key": key,
            **self.config.extra_headers,
        }

    async def generate(self, request: RouterRequest, model: ModelConfig) -> ProviderResponse:
        payload = {
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in request.messages
            ],
            "generationConfig": {"maxOutputTokens": request.max_output_tokens},
        }
        path = f"models/{quote(model.upstream_model, safe='')}:generateContent"
        try:
            response = await self._client.post(
                path,
                headers=self._headers(),
                json=payload,
                timeout=model.timeout_seconds or self.config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Gemini request timed out") from exc
        except httpx.TransportError as exc:
            raise TransientProviderError("Gemini transport failed") from exc

        raise_for_provider_status(
            response.status_code,
            headers=response.headers,
            body=response.text[:500],
        )
        data = response.json()
        try:
            candidate = data["candidates"][0]
            content = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            raise TransientProviderError("Gemini returned a malformed response") from exc
        usage_data = data.get("usageMetadata") or {}
        input_tokens = int(usage_data.get("promptTokenCount") or 0)
        output_tokens = int(usage_data.get("candidatesTokenCount") or 0)
        return ProviderResponse(
            content=content,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=model.estimate_cost(input_tokens, output_tokens),
            ),
            provider_request_id=response.headers.get("x-request-id"),
            finish_reason=candidate.get("finishReason"),
        )

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("models", headers=self._headers(), timeout=5.0)
            return response.status_code < 500
        except (httpx.HTTPError, ProviderAuthenticationError):
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
