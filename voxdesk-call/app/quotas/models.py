"""Quota keys and the normalized result of a resolution.

Missing limits are ``unknown``. They are not zero and they are not unlimited.
A malformed stored value is ``malformed`` and must fail closed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class QuotaKey(str, enum.Enum):
    """Keys the current product can actually resolve.

    ``users`` maps to the billing ``team_members`` entitlement.
    ``knowledge_documents`` maps to ``rag_documents``.
    ``active_calls`` maps to ``concurrent_calls``.
    ``monthly_call_minutes`` and ``ai_tokens`` map to the plan's included
    voice minutes and LLM tokens. The last two have no billing feature, so
    they stay unknown until a hierarchy row sets them.
    """

    USERS = "users"
    ENVIRONMENTS = "environments"
    ACTIVE_CALLS = "active_calls"
    MONTHLY_CALL_MINUTES = "monthly_call_minutes"
    AI_TOKENS = "ai_tokens"
    KNOWLEDGE_DOCUMENTS = "knowledge_documents"
    STORAGE_BYTES = "storage_bytes"
    WEBHOOK_EVENTS_PER_MINUTE = "webhook_events_per_minute"


class QuotaMode(str, enum.Enum):
    HARD = "hard"
    SOFT = "soft"
    UNLIMITED = "unlimited"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"


class QuotaDecisionKind(str, enum.Enum):
    ALLOW = "allow"
    WARN = "warn"
    DENY = "deny"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class QuotaDecision:
    key: str
    mode: str
    limit: int | None
    used: int | None
    source: str
    decision: str
    reason: str
    allowed: bool

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "mode": self.mode,
            "limit": self.limit,
            "used": self.used,
            "source": self.source,
            "decision": self.decision,
            "reason": self.reason,
            "allowed": self.allowed,
        }


class QuotaError(Exception):
    code = "quota_exceeded"

    def __init__(self, message: str, *, decision: QuotaDecision | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.decision = decision


class QuotaConfigurationError(QuotaError):
    code = "quota_configuration"
