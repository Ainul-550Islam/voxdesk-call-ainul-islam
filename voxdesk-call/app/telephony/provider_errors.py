"""Canonical telephony errors.

Messages are safe to return to an operator. They never include a credential,
an Authorization header, or a raw provider body.
"""

from __future__ import annotations


class TelephonyError(Exception):
    """Base class. Routes map ``status_code`` and ``as_dict``; they do not parse text."""

    code = "telephony_error"
    status_code = 502

    def __init__(self, message: str, *, provider: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "retryable": self.retryable,
        }


class ProviderAuthenticationError(TelephonyError):
    code = "authentication"
    status_code = 401


class ProviderAuthorizationError(TelephonyError):
    code = "authorization"
    status_code = 403


class ProviderValidationError(TelephonyError):
    code = "validation"
    status_code = 422


class ProviderRateLimitedError(TelephonyError):
    code = "rate_limited"
    status_code = 429

    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=True)


class ProviderUnavailableError(TelephonyError):
    code = "unavailable"
    status_code = 503

    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=True)


class ProviderTimeoutError(TelephonyError):
    code = "timeout"
    status_code = 504

    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=True)


class ProviderConflictError(TelephonyError):
    code = "conflict"
    status_code = 409


class ProviderNotFoundError(TelephonyError):
    code = "not_found"
    status_code = 404


class UnsupportedCapability(TelephonyError):
    code = "unsupported_capability"
    status_code = 422


class ProviderStateError(TelephonyError):
    code = "provider_state"
    status_code = 409


class ProviderConfigurationError(TelephonyError):
    """Credentials or required configuration are absent. Not a successful no-op."""

    code = "configuration"
    status_code = 503


def from_http_status(status: int, *, provider: str) -> TelephonyError:
    """Map a provider HTTP status to a typed error. Never treats 2xx as an error."""
    if status == 401:
        return ProviderAuthenticationError("Provider rejected the credentials", provider=provider)
    if status == 403:
        return ProviderAuthorizationError("Provider refused the operation", provider=provider)
    if status == 404:
        return ProviderNotFoundError("Provider resource was not found", provider=provider)
    if status == 409:
        return ProviderConflictError("Provider reported a conflict", provider=provider)
    if status == 422 or status == 400:
        return ProviderValidationError("Provider rejected the request", provider=provider)
    if status == 429:
        return ProviderRateLimitedError("Provider rate limit", provider=provider)
    if status == 408:
        return ProviderTimeoutError("Provider timed out", provider=provider)
    if status >= 500:
        return ProviderUnavailableError("Provider is unavailable", provider=provider)
    return ProviderUnavailableError(f"Provider returned status {status}", provider=provider)
