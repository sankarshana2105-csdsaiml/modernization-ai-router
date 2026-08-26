from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

from .models import TaskType, Usage


@dataclass(frozen=True, slots=True)
class UsageRecord:
    request_id: str
    provider_id: str
    model_id: str
    task: TaskType
    usage: Usage


class UsageLedger:
    """In-memory accounting boundary; production can persist the same records to SQL."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []
        self._lock = asyncio.Lock()

    async def record(self, record: UsageRecord) -> None:
        async with self._lock:
            self._records.append(record)

    async def snapshot(self) -> dict[str, object]:
        async with self._lock:
            by_model: dict[str, dict[str, int | float]] = defaultdict(
                lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            )
            for record in self._records:
                totals = by_model[record.model_id]
                totals["requests"] += 1
                totals["input_tokens"] += record.usage.input_tokens
                totals["output_tokens"] += record.usage.output_tokens
                totals["cost_usd"] += record.usage.cost_usd
            return {
                "total_requests": len(self._records),
                "total_cost_usd": round(sum(r.usage.cost_usd for r in self._records), 8),
                "by_model": {model: dict(values) for model, values in by_model.items()},
            }
