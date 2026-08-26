from __future__ import annotations

from pathlib import Path

from modernization_router.config import load_config
from modernization_router.models import CostTier


def test_example_config_loads_and_keeps_premium_provider_disabled() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "router.example.toml"
    config = load_config(path)

    assert len(config.models) == 9
    assert config.providers["openai_premium"].enabled is False
    assert config.providers["cloudflare_workers_ai"].base_url_env == "CLOUDFLARE_AI_BASE_URL"
    premium = next(model for model in config.models if model.id == "premium-fallback")
    assert premium.cost_tier is CostTier.PREMIUM
    assert premium.approved_for_proprietary is False
