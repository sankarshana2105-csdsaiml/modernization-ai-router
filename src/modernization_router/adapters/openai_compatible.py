from __future__ import annotations

import os
from typing import Any

import httpx

from ..exceptions import ProviderAuthenticationError, ProviderTimeoutError, TransientProviderError
from ..models import ModelConfig, ProviderConfig, ProviderResponse, RouterRequest, Usage
from .base import ProviderAdapter
from .http_errors import raise_for_provider_status


class OpenAICompatibleAdapter(ProviderAdapter):
    """Adapter for OpenAI, OpenRouter, NVIDIA NIM, Ollama, and compatible APIs."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config.id)
        self.config = config
        self._owns_client = client is None
        base_url = (
            os.getenv(config.base_url_env, config.base_url)
            if config.base_url_env
            else config.base_url
        )
        self._client = client or httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=httpx.Timeout(config.timeout_seconds),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", **self.config.extra_headers}
        if self.config.api_key_env:
            env_names = (self.config.api_key_env, *self.config.api_key_fallback_envs)
            api_key = next((os.getenv(name) for name in env_names if os.getenv(name)), None)
            if not api_key:
                raise ProviderAuthenticationError(
                    f"None of the required credentials {', '.join(env_names)} are configured"
                )
            headers["authorization"] = f"Bearer {api_key}"
        return headers

    async def generate(self, request: RouterRequest, model: ModelConfig) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": model.upstream_model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
        }
        try:
            response = await self._client.post(
                "chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=model.timeout_seconds or self.config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Provider request timed out") from exc
        except httpx.TransportError as exc:
            raise TransientProviderError("Provider transport failed") from exc

        raise_for_provider_status(
            response.status_code,
            headers=response.headers,
            body=response.text[:500],
        )
        data = response.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TransientProviderError("Provider returned a malformed response") from exc

        usage_data = data.get("usage") or {}
        input_tokens = int(usage_data.get("prompt_tokens") or 0)
        output_tokens = int(usage_data.get("completion_tokens") or 0)
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=model.estimate_cost(input_tokens, output_tokens),
        )
        return ProviderResponse(
            content=str(content),
            usage=usage,
            provider_request_id=response.headers.get("x-request-id") or data.get("id"),
            finish_reason=choice.get("finish_reason"),
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
