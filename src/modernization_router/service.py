from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import ChatMessage, PrivacyMode, RouterRequest, RouterResult, TaskType
from .router import ModelRouter


class ModernizationAI:
    """SaaS-facing facade. Controllers and workers depend on this single interface."""

    def __init__(self, router: ModelRouter) -> None:
        self.router = router

    async def run(
        self,
        task: TaskType,
        messages: Sequence[ChatMessage],
        *,
        privacy: PrivacyMode = PrivacyMode.PROPRIETARY,
        allow_premium_fallback: bool = True,
        estimated_input_tokens: int = 2_000,
        max_output_tokens: int = 2_000,
        required_capabilities: frozenset[str] = frozenset(),
        metadata: Mapping[str, str] | None = None,
    ) -> RouterResult:
        return await self.router.route(
            RouterRequest(
                task=task,
                messages=messages,
                privacy=privacy,
                allow_premium_fallback=allow_premium_fallback,
                estimated_input_tokens=estimated_input_tokens,
                max_output_tokens=max_output_tokens,
                required_capabilities=required_capabilities,
                metadata=metadata or {},
            )
        )

    async def analyze_code(self, prompt: str, *, proprietary: bool = True) -> RouterResult:
        return await self.run(
            TaskType.CODE_ANALYSIS,
            [ChatMessage(role="user", content=prompt)],
            privacy=PrivacyMode.PROPRIETARY if proprietary else PrivacyMode.PUBLIC,
        )

    async def refactor(self, prompt: str, *, proprietary: bool = True) -> RouterResult:
        return await self.run(
            TaskType.REFACTORING,
            [ChatMessage(role="user", content=prompt)],
            privacy=PrivacyMode.PROPRIETARY if proprietary else PrivacyMode.PUBLIC,
        )

    async def generate_tests(self, prompt: str, *, proprietary: bool = True) -> RouterResult:
        return await self.run(
            TaskType.TEST_GENERATION,
            [ChatMessage(role="user", content=prompt)],
            privacy=PrivacyMode.PROPRIETARY if proprietary else PrivacyMode.PUBLIC,
        )

    async def design_architecture(self, prompt: str, *, proprietary: bool = True) -> RouterResult:
        return await self.run(
            TaskType.ARCHITECTURE,
            [ChatMessage(role="user", content=prompt)],
            privacy=PrivacyMode.PROPRIETARY if proprietary else PrivacyMode.PUBLIC,
        )

    async def debug(self, prompt: str, *, proprietary: bool = True) -> RouterResult:
        return await self.run(
            TaskType.DEBUGGING,
            [ChatMessage(role="user", content=prompt)],
            privacy=PrivacyMode.PROPRIETARY if proprietary else PrivacyMode.PUBLIC,
        )
