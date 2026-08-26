from __future__ import annotations

import asyncio

import pytest

from modernization_router.accounting import UsageLedger
from modernization_router.adapters.fake import FakeProviderAdapter, FakeStep
from modernization_router.exceptions import (
    AllModelsFailedError,
    ProviderQuotaExceededError,
    TransientProviderError,
)
from modernization_router.models import (
    ChatMessage,
    CostTier,
    ModelConfig,
    PrivacyMode,
    ProviderConfig,
    RouterConfig,
    RouterRequest,
    TaskType,
)
from modernization_router.router import ModelRouter

ALL_TASKS = frozenset(task.value for task in TaskType)


def provider(provider_id: str) -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        adapter="fake",
        base_url="https://unused.invalid",
        timeout_seconds=0.5,
    )


def model(
    model_id: str,
    provider_id: str,
    *,
    priority: int = 10,
    tier: CostTier = CostTier.FREE,
    capabilities: frozenset[str] = ALL_TASKS,
    approved: bool = True,
    local: bool = False,
    retries: int = 0,
    timeout: float | None = None,
    requests_per_day: int | None = None,
    input_cost: float = 0.0,
    output_cost: float = 0.0,
) -> ModelConfig:
    return ModelConfig(
        id=model_id,
        provider_id=provider_id,
        upstream_model=model_id,
        capabilities=capabilities,
        priority=priority,
        cost_tier=tier,
        approved_for_proprietary=approved,
        is_local=local,
        max_retries=retries,
        timeout_seconds=timeout,
        requests_per_day=requests_per_day,
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
    )


def request(
    *,
    task: TaskType = TaskType.CODE_ANALYSIS,
    privacy: PrivacyMode = PrivacyMode.PUBLIC,
    premium: bool = True,
) -> RouterRequest:
    return RouterRequest(
        task=task,
        messages=[ChatMessage(role="user", content="legacy source code")],
        privacy=privacy,
        estimated_input_tokens=10,
        max_output_tokens=10,
        allow_premium_fallback=premium,
    )


def make_router(models: list[ModelConfig], adapters: list[FakeProviderAdapter]) -> ModelRouter:
    providers = {adapter.provider_id: provider(adapter.provider_id) for adapter in adapters}
    return ModelRouter(
        RouterConfig(
            providers=providers,
            models=tuple(models),
            backoff_base_seconds=0.0,
            backoff_max_seconds=0.0,
            health_failure_threshold=5,
        ),
        {adapter.provider_id: adapter for adapter in adapters},
    )


@pytest.mark.asyncio
async def test_tracked_daily_quota_automatically_moves_next_job_to_another_model() -> None:
    first = FakeProviderAdapter("first")
    second = FakeProviderAdapter("second")
    router = make_router(
        [
            model("free-a", "first", priority=1, requests_per_day=1),
            model("free-b", "second", priority=2),
        ],
        [first, second],
    )

    first_result = await router.route(request())
    second_result = await router.route(request())

    assert first_result.model_id == "free-a"
    assert second_result.model_id == "free-b"
    assert first.calls["free-a"] == 1
    assert second.calls["free-b"] == 1


@pytest.mark.asyncio
async def test_provider_reported_quota_exhaustion_fails_over() -> None:
    exhausted = FakeProviderAdapter("exhausted")
    healthy = FakeProviderAdapter("healthy")
    exhausted.script("free-a", FakeStep(error=ProviderQuotaExceededError("daily quota exhausted")))
    healthy.script("free-b", FakeStep(response="continued on fallback"))
    router = make_router(
        [model("free-a", "exhausted", priority=1), model("free-b", "healthy", priority=2)],
        [exhausted, healthy],
    )

    result = await router.route(request())

    assert result.content == "continued on fallback"
    assert result.model_id == "free-b"
    assert [attempt.error_type for attempt in result.attempts] == [
        "ProviderQuotaExceededError",
        None,
    ]


@pytest.mark.asyncio
async def test_transient_failure_retries_then_succeeds_on_same_model() -> None:
    adapter = FakeProviderAdapter("provider")
    adapter.script(
        "retrying",
        FakeStep(error=TransientProviderError("temporary")),
        FakeStep(response="recovered"),
    )
    router = make_router([model("retrying", "provider", retries=1)], [adapter])

    result = await router.route(request())

    assert result.content == "recovered"
    assert adapter.calls["retrying"] == 2
    assert [attempt.outcome for attempt in result.attempts] == ["failed", "success"]


@pytest.mark.asyncio
async def test_provider_failure_continues_on_another_eligible_provider() -> None:
    broken = FakeProviderAdapter("broken")
    backup = FakeProviderAdapter("backup")
    broken.script("bad", FakeStep(error=TransientProviderError("offline")))
    backup.script("good", FakeStep(response="backup result"))
    router = make_router(
        [model("bad", "broken", priority=1), model("good", "backup", priority=2)],
        [broken, backup],
    )

    result = await router.route(request())

    assert result.model_id == "good"
    assert result.content == "backup result"


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_fails_over() -> None:
    slow = FakeProviderAdapter("slow")
    fast = FakeProviderAdapter("fast")
    slow.script("slow-model", FakeStep(response="too late", delay_seconds=0.05))
    fast.script("fast-model", FakeStep(response="fast fallback"))
    router = make_router(
        [
            model("slow-model", "slow", priority=1, timeout=0.01),
            model("fast-model", "fast", priority=2),
        ],
        [slow, fast],
    )

    result = await asyncio.wait_for(router.route(request()), timeout=0.5)

    assert result.model_id == "fast-model"
    assert result.attempts[0].error_type == "ProviderTimeoutError"


@pytest.mark.asyncio
async def test_proprietary_code_only_uses_approved_or_local_model() -> None:
    external = FakeProviderAdapter("external")
    local = FakeProviderAdapter("local")
    external.script("external-free", FakeStep(response="must not see proprietary code"))
    local.script("local-model", FakeStep(response="private result"))
    router = make_router(
        [
            model("external-free", "external", priority=1, approved=False),
            model("local-model", "local", priority=2, approved=True, local=True),
        ],
        [external, local],
    )

    result = await router.route(request(privacy=PrivacyMode.PROPRIETARY))

    assert result.model_id == "local-model"
    assert external.calls["external-free"] == 0


@pytest.mark.asyncio
async def test_task_capability_registry_filters_models() -> None:
    generic = FakeProviderAdapter("generic")
    architect = FakeProviderAdapter("architect")
    router = make_router(
        [
            model(
                "analysis-only",
                "generic",
                priority=1,
                capabilities=frozenset({TaskType.CODE_ANALYSIS.value}),
            ),
            model(
                "architecture-model",
                "architect",
                priority=2,
                capabilities=frozenset({TaskType.ARCHITECTURE.value}),
            ),
        ],
        [generic, architect],
    )

    result = await router.route(request(task=TaskType.ARCHITECTURE))

    assert result.model_id == "architecture-model"
    assert generic.calls["analysis-only"] == 0


@pytest.mark.asyncio
async def test_premium_model_is_only_used_as_fallback_and_is_reported() -> None:
    free = FakeProviderAdapter("free")
    premium = FakeProviderAdapter("premium")
    free.script("free-model", FakeStep(error=TransientProviderError("unavailable")))
    premium.script("premium-model", FakeStep(response="premium repair"))
    router = make_router(
        [
            model("free-model", "free", priority=1),
            model("premium-model", "premium", priority=1, tier=CostTier.PREMIUM),
        ],
        [free, premium],
    )

    result = await router.route(request(premium=True))

    assert result.model_id == "premium-model"
    assert result.used_premium_fallback is True


@pytest.mark.asyncio
async def test_premium_fallback_can_be_disabled_per_job() -> None:
    free = FakeProviderAdapter("free")
    premium = FakeProviderAdapter("premium")
    free.script("free-model", FakeStep(error=TransientProviderError("unavailable")))
    router = make_router(
        [
            model("free-model", "free", priority=1),
            model("premium-model", "premium", tier=CostTier.PREMIUM),
        ],
        [free, premium],
    )

    with pytest.raises(AllModelsFailedError):
        await router.route(request(premium=False))

    assert premium.calls["premium-model"] == 0


@pytest.mark.asyncio
async def test_successful_usage_and_cost_are_accounted() -> None:
    adapter = FakeProviderAdapter("metered")
    adapter.script("metered-model", FakeStep(response="ok", input_tokens=100, output_tokens=50))
    ledger = UsageLedger()
    config = RouterConfig(
        providers={"metered": provider("metered")},
        models=(
            model(
                "metered-model",
                "metered",
                input_cost=2.0,
                output_cost=4.0,
            ),
        ),
        backoff_base_seconds=0.0,
    )
    router = ModelRouter(config, {"metered": adapter}, ledger=ledger)

    result = await router.route(request())
    snapshot = await ledger.snapshot()

    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.cost_usd == pytest.approx(0.0004)
    assert snapshot["total_cost_usd"] == pytest.approx(0.0004)
