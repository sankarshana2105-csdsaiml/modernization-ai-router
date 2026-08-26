from __future__ import annotations


class RouterError(Exception):
    """Base router exception."""


class NoEligibleModelsError(RouterError):
    """No model satisfies capability, privacy, health, and budget policy."""


class AllModelsFailedError(RouterError):
    """Every eligible model failed."""

    def __init__(self, message: str, attempts: tuple[object, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


class ProviderError(RouterError):
    retryable = False

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TransientProviderError(ProviderError):
    retryable = True


class ProviderTimeoutError(TransientProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    """Rate limits fail over immediately; retrying the same provider is wasteful."""


class ProviderQuotaExceededError(ProviderError):
    pass


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderPermanentError(ProviderError):
    pass
