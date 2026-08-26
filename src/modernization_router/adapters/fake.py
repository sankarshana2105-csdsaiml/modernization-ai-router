from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass

from ..models import ModelConfig, ProviderResponse, RouterRequest, Usage
from .base import ProviderAdapter


@dataclass(frozen=True, slots=True)
class FakeStep:
    response: str | None = "ok"
    error: Exception | None = None
    delay_seconds: float = 0.0
    input_tokens: int = 10
    output_tokens: int = 5


class FakeProviderAdapter(ProviderAdapter):
    """Deterministic adapter used to prove routing behavior without live credentials."""

    def __init__(self, provider_id: str, *, healthy: bool = True) -> None:
        super().__init__(provider_id)
        self.healthy = healthy
        self.calls: dict[str, int] = defaultdict(int)
        self._steps: dict[str, deque[FakeStep]] = defaultdict(deque)

    def script(self, model_id: str, *steps: FakeStep) -> None:
        self._steps[model_id].extend(steps)

    async def generate(self, request: RouterRequest, model: ModelConfig) -> ProviderResponse:
        self.calls[model.id] += 1
        step = self._steps[model.id].popleft() if self._steps[model.id] else FakeStep()
        if step.delay_seconds:
            await asyncio.sleep(step.delay_seconds)
        if step.error:
            raise step.error
        input_tokens = step.input_tokens
        output_tokens = step.output_tokens
        return ProviderResponse(
            content=step.response or "",
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=model.estimate_cost(input_tokens, output_tokens),
            ),
            provider_request_id=f"fake-{self.calls[model.id]}",
            finish_reason="stop",
        )

    async def health_check(self) -> bool:
        return self.healthy
