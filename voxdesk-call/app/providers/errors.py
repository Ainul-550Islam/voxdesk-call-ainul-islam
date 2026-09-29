"""Typed provider/runtime failures preserving the voice layer's safe taxonomy."""
from __future__ import annotations

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderInvalidRequestError,
    ProviderRateLimitedError,
    ProviderRuntimeError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedProviderFeatureError,
)


class ProviderCapabilityError(UnsupportedProviderFeatureError):
    """An installed provider surface lacks a required capability."""


class ProviderVersionMismatch(ProviderCapabilityError):
    """The installed SDK version does not expose the application's API contract."""


class ProviderCompatibilityError(ProviderVersionMismatch):
    """Compatibility alias used by existing provider builders."""


class ProviderDependencyMissingError(ProviderConfigurationError):
    """A declared optional integration dependency is not installed."""


# Explicit public spellings requested by provider-neutral callers.
ProviderUnsupported = UnsupportedProviderFeatureError
ProviderUnavailable = ProviderUnavailableError
ProviderTimeout = ProviderTimeoutError
ProviderRateLimited = ProviderRateLimitedError
ProviderAuthentication = ProviderAuthenticationError
ProviderConfiguration = ProviderConfigurationError

__all__ = [
    "ProviderAuthentication", "ProviderAuthenticationError", "ProviderAuthorizationError",
    "ProviderCapabilityError", "ProviderCompatibilityError", "ProviderConfiguration",
    "ProviderConfigurationError", "ProviderDependencyMissingError", "ProviderError",
    "ProviderInvalidRequestError", "ProviderRateLimited", "ProviderRateLimitedError",
    "ProviderRuntimeError", "ProviderTimeout", "ProviderTimeoutError", "ProviderUnavailable",
    "ProviderUnavailableError", "ProviderUnsupported", "ProviderVersionMismatch",
    "UnsupportedProviderFeatureError",
]
