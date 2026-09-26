"""Normalized AI failures.

Customer text is a fixed sentence. It never includes a provider payload, an
API key, a prompt, or the caller's raw exception string.
"""

from __future__ import annotations

from app.agent.errors import ProviderError
from app.ai.models import GovernanceError
from app.tenancy.isolation import BoundaryDenied, Conflict, LifecycleDenied

OUTAGE_SENTENCE = (
    "Sorry, I'm having trouble right now. "
    "Someone from our team will get back to you shortly."
)
GOVERNANCE_SENTENCE = (
    "I can't complete that request right now. Please contact the office."
)

_GOVERNANCE_TYPES = (GovernanceError, BoundaryDenied, LifecycleDenied, Conflict)


class RuntimeFailure(GovernanceError):
    """A governed call stopped before a provider, or usage could not be recorded."""

    def __init__(self, code: str, safe_message: str = GOVERNANCE_SENTENCE) -> None:
        super().__init__(safe_message, code=code, status_code=409)
        self.safe_message = safe_message


def is_governance(exc: BaseException) -> bool:
    return isinstance(exc, _GOVERNANCE_TYPES)


def is_provider_outage(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError)


def customer_text(exc: BaseException) -> str:
    """What a waiting customer may see. Governance and outage stay distinct."""
    if is_provider_outage(exc):
        return OUTAGE_SENTENCE
    if is_governance(exc):
        return GOVERNANCE_SENTENCE
    return OUTAGE_SENTENCE


def failure_kind(exc: BaseException) -> str:
    if is_provider_outage(exc):
        return "provider"
    if is_governance(exc):
        return "governance"
    return "contract"


def normalize(exc: BaseException) -> dict:
    """Safe fields for a log line. No exception text, no payload."""
    code = getattr(exc, "code", None) or type(exc).__name__
    return {
        "kind": failure_kind(exc),
        "code": str(code),
        "error_type": type(exc).__name__,
        "retryable": bool(getattr(exc, "retryable", False)),
        "provider": getattr(exc, "provider", "") or "",
    }
