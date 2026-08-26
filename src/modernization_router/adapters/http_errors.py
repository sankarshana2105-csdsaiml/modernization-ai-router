from __future__ import annotations

from collections.abc import Mapping

from ..exceptions import (
    ProviderAuthenticationError,
    ProviderPermanentError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    TransientProviderError,
)


def retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def raise_for_provider_status(
    status_code: int,
    *,
    headers: Mapping[str, str],
    body: str,
) -> None:
    safe_message = f"Provider returned HTTP {status_code}"
    lower_body = body.lower()
    if status_code in {401, 403}:
        raise ProviderAuthenticationError(safe_message)
    if status_code == 429:
        delay = retry_after(headers)
        if any(word in lower_body for word in ("quota", "credit", "balance", "billing")):
            raise ProviderQuotaExceededError(safe_message, retry_after_seconds=delay)
        raise ProviderRateLimitError(safe_message, retry_after_seconds=delay)
    if status_code in {408, 409, 425} or status_code >= 500:
        raise TransientProviderError(
            safe_message,
            retry_after_seconds=retry_after(headers),
        )
    if status_code >= 400:
        raise ProviderPermanentError(safe_message)
