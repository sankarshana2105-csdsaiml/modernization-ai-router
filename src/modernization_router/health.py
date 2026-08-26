from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class CircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._monotonic = monotonic
        self._states: dict[str, CircuitState] = {}
        self._lock = asyncio.Lock()

    async def can_attempt(self, provider_id: str) -> bool:
        async with self._lock:
            state = self._states.setdefault(provider_id, CircuitState())
            if state.opened_at is None:
                return True
            return self._monotonic() - state.opened_at >= self.cooldown_seconds

    async def success(self, provider_id: str) -> None:
        async with self._lock:
            state = self._states.setdefault(provider_id, CircuitState())
            state.consecutive_failures = 0
            state.opened_at = None
            state.last_success_at = self._monotonic()

    async def failure(self, provider_id: str) -> None:
        async with self._lock:
            state = self._states.setdefault(provider_id, CircuitState())
            state.consecutive_failures += 1
            state.last_failure_at = self._monotonic()
            if state.consecutive_failures >= self.failure_threshold:
                state.opened_at = self._monotonic()

    async def snapshot(self) -> dict[str, dict[str, int | float | bool | None]]:
        async with self._lock:
            now = self._monotonic()
            return {
                provider_id: {
                    "consecutive_failures": state.consecutive_failures,
                    "circuit_open": state.opened_at is not None
                    and now - state.opened_at < self.cooldown_seconds,
                    "last_success_at": state.last_success_at,
                    "last_failure_at": state.last_failure_at,
                }
                for provider_id, state in self._states.items()
            }
