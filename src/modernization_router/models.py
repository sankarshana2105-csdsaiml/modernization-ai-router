from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TaskType(StrEnum):
    CODE_ANALYSIS = "code_analysis"
    REFACTORING = "refactoring"
    TEST_GENERATION = "test_generation"
    ARCHITECTURE = "architecture"
    DEBUGGING = "debugging"
    KNOWLEDGE_ANSWERING = "knowledge_answering"


class PrivacyMode(StrEnum):
    PUBLIC = "public"
    PROPRIETARY = "proprietary"
    LOCAL_ONLY = "local_only"


class CostTier(StrEnum):
    FREE = "free"
    CHEAP = "cheap"
    STANDARD = "standard"
    PREMIUM = "premium"

    @property
    def rank(self) -> int:
        return {
            CostTier.FREE: 0,
            CostTier.CHEAP: 1,
            CostTier.STANDARD: 2,
            CostTier.PREMIUM: 3,
        }[self]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str
    usage: Usage = field(default_factory=Usage)
    provider_request_id: str | None = None
    finish_reason: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RouterRequest:
    task: TaskType
    messages: Sequence[ChatMessage]
    privacy: PrivacyMode = PrivacyMode.PUBLIC
    estimated_input_tokens: int = 2_000
    max_output_tokens: int = 2_000
    allow_premium_fallback: bool = True
    required_capabilities: frozenset[str] = frozenset()
    metadata: Mapping[str, str] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def capabilities(self) -> frozenset[str]:
        return frozenset({self.task.value, *self.required_capabilities})


@dataclass(frozen=True, slots=True)
class Attempt:
    provider_id: str
    model_id: str
    attempt_number: int
    outcome: str
    latency_ms: float
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class RouterResult:
    request_id: str
    provider_id: str
    model_id: str
    content: str
    usage: Usage
    attempts: tuple[Attempt, ...]
    used_premium_fallback: bool
    provider_request_id: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    id: str
    adapter: str
    base_url: str
    base_url_env: str | None = None
    api_key_env: str | None = None
    api_key_fallback_envs: tuple[str, ...] = ()
    enabled: bool = True
    timeout_seconds: float = 60.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelConfig:
    id: str
    provider_id: str
    upstream_model: str
    capabilities: frozenset[str]
    cost_tier: CostTier = CostTier.STANDARD
    priority: int = 100
    enabled: bool = True
    is_local: bool = False
    approved_for_proprietary: bool = False
    requests_per_minute: int | None = None
    requests_per_day: int | None = None
    tokens_per_minute: int | None = None
    tokens_per_day: int | None = None
    daily_cost_limit_usd: float | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    max_retries: int = 2
    timeout_seconds: float | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_cost_per_million
            + output_tokens * self.output_cost_per_million
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class RouterConfig:
    providers: Mapping[str, ProviderConfig]
    models: tuple[ModelConfig, ...]
    backoff_base_seconds: float = 0.25
    backoff_max_seconds: float = 4.0
    health_failure_threshold: int = 3
    health_cooldown_seconds: float = 30.0
