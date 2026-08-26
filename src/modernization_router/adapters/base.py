from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ModelConfig, ProviderResponse, RouterRequest


class ProviderAdapter(ABC):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    @abstractmethod
    async def generate(self, request: RouterRequest, model: ModelConfig) -> ProviderResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError

    async def close(self) -> None:
        return None
