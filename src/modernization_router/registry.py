from __future__ import annotations

from collections.abc import Iterable

from .models import CostTier, ModelConfig, PrivacyMode, RouterRequest


class ModelRegistry:
    def __init__(self, models: Iterable[ModelConfig]) -> None:
        self._models = tuple(models)
        ids = [model.id for model in self._models]
        if len(ids) != len(set(ids)):
            raise ValueError("Model IDs must be unique")

    def eligible(self, request: RouterRequest) -> tuple[ModelConfig, ...]:
        required = request.capabilities()
        candidates = []
        for model in self._models:
            if not model.enabled or not required.issubset(model.capabilities):
                continue
            if model.cost_tier is CostTier.PREMIUM and not request.allow_premium_fallback:
                continue
            if request.privacy is PrivacyMode.LOCAL_ONLY and not model.is_local:
                continue
            if request.privacy is PrivacyMode.PROPRIETARY and not (
                model.is_local or model.approved_for_proprietary
            ):
                continue
            candidates.append(model)

        return tuple(
            sorted(
                candidates,
                key=lambda model: (
                    model.cost_tier.rank,
                    model.priority,
                    model.estimate_cost(
                        request.estimated_input_tokens,
                        request.max_output_tokens,
                    ),
                    model.id,
                ),
            )
        )

    def all(self) -> tuple[ModelConfig, ...]:
        return self._models
