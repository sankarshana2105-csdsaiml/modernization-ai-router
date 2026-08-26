from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import uuid4

from .models import ModelConfig, Usage


@dataclass(frozen=True, slots=True)
class Reservation:
    id: str
    model_id: str
    estimated_tokens: int
    estimated_cost_usd: float


@dataclass(slots=True)
class _QuotaState:
    request_times: deque[float] = field(default_factory=deque)
    token_events: deque[tuple[float, str, int]] = field(default_factory=deque)
    day: date = field(default_factory=lambda: datetime.now(UTC).date())
    daily_requests: int = 0
    daily_tokens: int = 0
    daily_cost_usd: float = 0.0
    reservations: dict[str, Reservation] = field(default_factory=dict)


class InMemoryQuotaTracker:
    """Concurrency-safe quotas. Replace with Redis for horizontally scaled workers."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        utc_date: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._states: dict[str, _QuotaState] = defaultdict(_QuotaState)
        self._lock = asyncio.Lock()
        self._monotonic = monotonic
        self._utc_date = utc_date

    def _roll_windows(self, state: _QuotaState, now: float) -> None:
        minute_ago = now - 60.0
        while state.request_times and state.request_times[0] <= minute_ago:
            state.request_times.popleft()
        while state.token_events and state.token_events[0][0] <= minute_ago:
            state.token_events.popleft()
        today = self._utc_date()
        if state.day != today:
            state.day = today
            state.daily_requests = 0
            state.daily_tokens = 0
            state.daily_cost_usd = 0.0

    async def try_reserve(
        self,
        model: ModelConfig,
        *,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> Reservation | None:
        estimated_tokens = max(0, estimated_input_tokens) + max(0, estimated_output_tokens)
        estimated_cost = model.estimate_cost(
            max(0, estimated_input_tokens), max(0, estimated_output_tokens)
        )
        async with self._lock:
            now = self._monotonic()
            state = self._states[model.id]
            self._roll_windows(state, now)
            minute_tokens = sum(event[2] for event in state.token_events)
            if (
                model.requests_per_minute is not None
                and len(state.request_times) >= model.requests_per_minute
            ):
                return None
            if (
                model.requests_per_day is not None
                and state.daily_requests >= model.requests_per_day
            ):
                return None
            if (
                model.tokens_per_minute is not None
                and minute_tokens + estimated_tokens > model.tokens_per_minute
            ):
                return None
            if (
                model.tokens_per_day is not None
                and state.daily_tokens + estimated_tokens > model.tokens_per_day
            ):
                return None
            if (
                model.daily_cost_limit_usd is not None
                and state.daily_cost_usd + estimated_cost > model.daily_cost_limit_usd
            ):
                return None

            reservation = Reservation(
                id=str(uuid4()),
                model_id=model.id,
                estimated_tokens=estimated_tokens,
                estimated_cost_usd=estimated_cost,
            )
            state.request_times.append(now)
            state.token_events.append((now, reservation.id, estimated_tokens))
            state.daily_requests += 1
            state.daily_tokens += estimated_tokens
            state.daily_cost_usd += estimated_cost
            state.reservations[reservation.id] = reservation
            return reservation

    async def commit(self, reservation: Reservation, usage: Usage) -> None:
        async with self._lock:
            state = self._states[reservation.model_id]
            if state.reservations.pop(reservation.id, None) is None:
                return
            actual_tokens = max(0, usage.total_tokens)
            state.daily_tokens += actual_tokens - reservation.estimated_tokens
            state.daily_cost_usd += max(0.0, usage.cost_usd) - reservation.estimated_cost_usd
            state.token_events = deque(
                (stamp, item_id, actual_tokens if item_id == reservation.id else tokens)
                for stamp, item_id, tokens in state.token_events
            )

    async def release(self, reservation: Reservation) -> None:
        """Release token/cost estimates; the attempted request still counts against request limits."""
        async with self._lock:
            state = self._states[reservation.model_id]
            if state.reservations.pop(reservation.id, None) is None:
                return
            state.daily_tokens = max(0, state.daily_tokens - reservation.estimated_tokens)
            state.daily_cost_usd = max(0.0, state.daily_cost_usd - reservation.estimated_cost_usd)
            state.token_events = deque(
                event for event in state.token_events if event[1] != reservation.id
            )

    async def snapshot(self) -> dict[str, dict[str, float | int | str]]:
        async with self._lock:
            now = self._monotonic()
            result: dict[str, dict[str, float | int | str]] = {}
            for model_id, state in self._states.items():
                self._roll_windows(state, now)
                result[model_id] = {
                    "day": state.day.isoformat(),
                    "requests_last_minute": len(state.request_times),
                    "tokens_last_minute": sum(item[2] for item in state.token_events),
                    "requests_today": state.daily_requests,
                    "tokens_today": state.daily_tokens,
                    "cost_today_usd": round(state.daily_cost_usd, 8),
                }
            return result
