from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Self

from .accounting import UsageLedger, UsageRecord
from .adapters.base import ProviderAdapter
from .exceptions import (
    AllModelsFailedError,
    NoEligibleModelsError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    TransientProviderError,
)
from .health import CircuitBreaker
from .models import Attempt, CostTier, ModelConfig, RouterConfig, RouterRequest, RouterResult
from .quota import InMemoryQuotaTracker
from .registry import ModelRegistry

logger = logging.getLogger("modernization_router.router")


class ModelRouter:
    """One internal interface for policy-aware routing across model providers."""

    def __init__(
        self,
        config: RouterConfig,
        adapters: Mapping[str, ProviderAdapter],
        *,
        quota_tracker: InMemoryQuotaTracker | None = None,
        ledger: UsageLedger | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.adapters = dict(adapters)
        self.registry = ModelRegistry(config.models)
        self.quota_tracker = quota_tracker or InMemoryQuotaTracker()
        self.ledger = ledger or UsageLedger()
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            failure_threshold=config.health_failure_threshold,
            cooldown_seconds=config.health_cooldown_seconds,
        )
        self._sleep = sleep
        missing = {model.provider_id for model in config.models} - self.adapters.keys()
        if missing:
            raise ValueError(f"Missing provider adapters: {sorted(missing)}")

    def _candidate_models(self, request: RouterRequest) -> tuple[ModelConfig, ...]:
        return tuple(
            model
            for model in self.registry.eligible(request)
            if self.config.providers[model.provider_id].enabled
        )

    async def route(self, request: RouterRequest) -> RouterResult:
        candidates = self._candidate_models(request)
        if not candidates:
            raise NoEligibleModelsError(
                "No model satisfies the requested task, privacy mode, and cost policy"
            )

        attempts: list[Attempt] = []
        excluded_providers: set[str] = set()
        logger.info(
            "route_started",
            extra={
                "request_id": request.request_id,
                "task": request.task.value,
                "privacy": request.privacy.value,
                "candidate_count": len(candidates),
            },
        )

        for model in candidates:
            if model.provider_id in excluded_providers:
                continue
            if not await self.circuit_breaker.can_attempt(model.provider_id):
                logger.warning(
                    "provider_circuit_open",
                    extra={"request_id": request.request_id, "provider_id": model.provider_id},
                )
                continue
            adapter = self.adapters[model.provider_id]

            for retry_index in range(model.max_retries + 1):
                reservation = await self.quota_tracker.try_reserve(
                    model,
                    estimated_input_tokens=request.estimated_input_tokens,
                    estimated_output_tokens=request.max_output_tokens,
                )
                if reservation is None:
                    logger.info(
                        "model_quota_unavailable",
                        extra={
                            "request_id": request.request_id,
                            "provider_id": model.provider_id,
                            "model_id": model.id,
                        },
                    )
                    break

                started = time.perf_counter()
                error: ProviderError | None = None
                try:
                    timeout = (
                        model.timeout_seconds
                        or self.config.providers[model.provider_id].timeout_seconds
                    )
                    response = await asyncio.wait_for(
                        adapter.generate(request, model),
                        timeout=timeout,
                    )
                    latency_ms = (time.perf_counter() - started) * 1_000
                    await self.quota_tracker.commit(reservation, response.usage)
                    await self.circuit_breaker.success(model.provider_id)
                    await self.ledger.record(
                        UsageRecord(
                            request_id=request.request_id,
                            provider_id=model.provider_id,
                            model_id=model.id,
                            task=request.task,
                            usage=response.usage,
                        )
                    )
                    attempts.append(
                        Attempt(
                            provider_id=model.provider_id,
                            model_id=model.id,
                            attempt_number=retry_index + 1,
                            outcome="success",
                            latency_ms=latency_ms,
                        )
                    )
                    logger.info(
                        "route_succeeded",
                        extra={
                            "request_id": request.request_id,
                            "provider_id": model.provider_id,
                            "model_id": model.id,
                            "latency_ms": round(latency_ms, 2),
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "cost_usd": response.usage.cost_usd,
                            "premium_fallback": model.cost_tier is CostTier.PREMIUM,
                        },
                    )
                    return RouterResult(
                        request_id=request.request_id,
                        provider_id=model.provider_id,
                        model_id=model.id,
                        content=response.content,
                        usage=response.usage,
                        attempts=tuple(attempts),
                        used_premium_fallback=model.cost_tier is CostTier.PREMIUM,
                        provider_request_id=response.provider_request_id,
                        finish_reason=response.finish_reason,
                    )
                except TimeoutError:
                    error = ProviderTimeoutError("Router timeout expired")
                except asyncio.CancelledError:
                    await self.quota_tracker.release(reservation)
                    raise
                except ProviderError as exc:
                    error = exc
                except Exception as exc:  # noqa: BLE001 - defensive third-party boundary
                    error = TransientProviderError(
                        f"Unexpected adapter failure: {type(exc).__name__}"
                    )

                await self.quota_tracker.release(reservation)
                latency_ms = (time.perf_counter() - started) * 1_000
                attempts.append(
                    Attempt(
                        provider_id=model.provider_id,
                        model_id=model.id,
                        attempt_number=retry_index + 1,
                        outcome="failed",
                        latency_ms=latency_ms,
                        error_type=type(error).__name__,
                    )
                )
                logger.warning(
                    "route_attempt_failed",
                    extra={
                        "request_id": request.request_id,
                        "provider_id": model.provider_id,
                        "model_id": model.id,
                        "attempt": retry_index + 1,
                        "error_type": type(error).__name__,
                    },
                )

                if isinstance(error, (TransientProviderError, ProviderTimeoutError)):
                    await self.circuit_breaker.failure(model.provider_id)
                    if retry_index < model.max_retries:
                        delay = error.retry_after_seconds
                        if delay is None:
                            delay = min(
                                self.config.backoff_max_seconds,
                                self.config.backoff_base_seconds * (2**retry_index),
                            )
                            if delay:
                                delay *= random.uniform(0.8, 1.2)
                        await self._sleep(delay)
                        continue
                elif isinstance(
                    error,
                    (
                        ProviderAuthenticationError,
                        ProviderQuotaExceededError,
                        ProviderRateLimitError,
                    ),
                ):
                    excluded_providers.add(model.provider_id)
                else:
                    await self.circuit_breaker.failure(model.provider_id)
                break

        logger.error(
            "route_failed",
            extra={"request_id": request.request_id, "attempt_count": len(attempts)},
        )
        raise AllModelsFailedError(
            "All eligible models failed or exhausted their configured limits",
            attempts=tuple(attempts),
        )

    async def health(self) -> dict[str, object]:
        async def check(provider_id: str, adapter: ProviderAdapter) -> tuple[str, bool]:
            try:
                healthy = await asyncio.wait_for(adapter.health_check(), timeout=5.0)
            except Exception:  # noqa: BLE001 - health checks must not break the status endpoint
                healthy = False
            if healthy:
                await self.circuit_breaker.success(provider_id)
            else:
                await self.circuit_breaker.failure(provider_id)
            return provider_id, healthy

        checks = await asyncio.gather(
            *(check(provider_id, adapter) for provider_id, adapter in self.adapters.items())
        )
        return {
            "providers": dict(checks),
            "circuits": await self.circuit_breaker.snapshot(),
            "quotas": await self.quota_tracker.snapshot(),
            "usage": await self.ledger.snapshot(),
        }

    async def close(self) -> None:
        await asyncio.gather(*(adapter.close() for adapter in self.adapters.values()))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
