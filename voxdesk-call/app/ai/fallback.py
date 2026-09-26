"""Fallback that cannot leave the tenant allowlist.

Retryable failures may move to the next allowed provider. Policy rejection,
bad credentials, and an unsupported model do not.
"""

from __future__ import annotations

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderInvalidRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedProviderFeatureError,
)
from app.agent.llm_factory import FALLBACK_ORDER, LLMChoice
from app.ai.models import PolicyDenied, PolicyView
from app.ai.routing import assert_allowed, fallback_choice

RETRYABLE = (
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderRateLimitedError,
)
NON_RETRYABLE = (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderInvalidRequestError,
    UnsupportedProviderFeatureError,
    PolicyDenied,
)


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RETRYABLE) and not isinstance(exc, NON_RETRYABLE)


def next_allowed(
    failed: LLMChoice,
    policy: PolicyView,
    *,
    environment_kind: str,
    policy_denied: bool = False,
) -> LLMChoice | None:
    """Policy denial does not become another provider. Retryable misses stay allowlisted."""
    if policy_denied:
        return None
    return next_candidate(failed, policy, environment_kind=environment_kind)


def next_candidate(
    failed: LLMChoice,
    policy: PolicyView,
    *,
    environment_kind: str,
) -> LLMChoice | None:
    """The next allowed provider in the existing order, or None.

    The failed provider is skipped. Every other supported provider is considered,
    including one that appears earlier in the order, so the last provider can
    still fall back. A disallowed or disabled provider is never selected.
    """
    for provider in FALLBACK_ORDER:
        if provider == failed.provider:
            continue
        try:
            choice = fallback_choice(provider)
            assert_allowed(choice, policy, environment_kind=environment_kind)
        except PolicyDenied:
            continue
        return choice
    return None
