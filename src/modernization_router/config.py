from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .adapters import GeminiAdapter, OpenAICompatibleAdapter, ProviderAdapter
from .models import CostTier, ModelConfig, ProviderConfig, RouterConfig
from .router import ModelRouter


def load_config(path: str | Path) -> RouterConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    providers: dict[str, ProviderConfig] = {}
    for item in raw.get("providers", []):
        provider = ProviderConfig(
            id=item["id"],
            adapter=item["adapter"],
            base_url=item["base_url"],
            base_url_env=item.get("base_url_env"),
            api_key_env=item.get("api_key_env"),
            enabled=item.get("enabled", True),
            timeout_seconds=float(item.get("timeout_seconds", 60.0)),
            extra_headers=item.get("extra_headers", {}),
        )
        providers[provider.id] = provider

    models = tuple(_parse_model(item) for item in raw.get("models", []))
    router_raw = raw.get("router", {})
    config = RouterConfig(
        providers=providers,
        models=models,
        backoff_base_seconds=float(router_raw.get("backoff_base_seconds", 0.25)),
        backoff_max_seconds=float(router_raw.get("backoff_max_seconds", 4.0)),
        health_failure_threshold=int(router_raw.get("health_failure_threshold", 3)),
        health_cooldown_seconds=float(router_raw.get("health_cooldown_seconds", 30.0)),
    )
    _validate_config(config)
    return config


def _optional_int(item: dict[str, Any], key: str) -> int | None:
    value = item.get(key)
    return int(value) if value is not None else None


def _optional_float(item: dict[str, Any], key: str) -> float | None:
    value = item.get(key)
    return float(value) if value is not None else None


def _parse_model(item: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        id=item["id"],
        provider_id=item["provider_id"],
        upstream_model=item["upstream_model"],
        capabilities=frozenset(item["capabilities"]),
        cost_tier=CostTier(item.get("cost_tier", "standard")),
        priority=int(item.get("priority", 100)),
        enabled=item.get("enabled", True),
        is_local=item.get("is_local", False),
        approved_for_proprietary=item.get("approved_for_proprietary", False),
        requests_per_minute=_optional_int(item, "requests_per_minute"),
        requests_per_day=_optional_int(item, "requests_per_day"),
        tokens_per_minute=_optional_int(item, "tokens_per_minute"),
        tokens_per_day=_optional_int(item, "tokens_per_day"),
        daily_cost_limit_usd=_optional_float(item, "daily_cost_limit_usd"),
        input_cost_per_million=float(item.get("input_cost_per_million", 0.0)),
        output_cost_per_million=float(item.get("output_cost_per_million", 0.0)),
        max_retries=int(item.get("max_retries", 2)),
        timeout_seconds=_optional_float(item, "timeout_seconds"),
        metadata=item.get("metadata", {}),
    )


def _validate_config(config: RouterConfig) -> None:
    if not config.providers:
        raise ValueError("At least one provider must be configured")
    if not config.models:
        raise ValueError("At least one model must be configured")
    missing = {model.provider_id for model in config.models} - config.providers.keys()
    if missing:
        raise ValueError(f"Models reference unknown providers: {sorted(missing)}")


def build_router(
    config: RouterConfig,
    *,
    adapter_overrides: dict[str, ProviderAdapter] | None = None,
) -> ModelRouter:
    overrides = adapter_overrides or {}
    adapters: dict[str, ProviderAdapter] = {}
    for provider_id, provider in config.providers.items():
        if provider_id in overrides:
            adapters[provider_id] = overrides[provider_id]
        elif provider.adapter == "openai_compatible":
            adapters[provider_id] = OpenAICompatibleAdapter(provider)
        elif provider.adapter == "gemini":
            adapters[provider_id] = GeminiAdapter(provider)
        else:
            raise ValueError(f"Unsupported provider adapter: {provider.adapter}")
    return ModelRouter(config, adapters)
