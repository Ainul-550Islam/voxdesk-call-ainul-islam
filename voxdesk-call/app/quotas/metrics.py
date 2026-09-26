"""Quota decision metrics.

Labels are closed sets: a quota key and a decision. Tenant ids are not labels.
"""

from __future__ import annotations

from prometheus_client import Counter

from app.quotas.models import QuotaDecisionKind, QuotaKey

QUOTA_DECISIONS = Counter(
    "voxdesk_quota_decisions_total",
    "Quota evaluations by key and decision",
    ["quota_key", "decision"],
)

_KEYS = frozenset(item.value for item in QuotaKey)
_DECISIONS = frozenset(item.value for item in QuotaDecisionKind)


def record_decision(key: str, decision: str) -> None:
    """Bump the counter. Unknown labels are dropped, never added."""
    if key not in _KEYS or decision not in _DECISIONS:
        return
    QUOTA_DECISIONS.labels(quota_key=key, decision=decision).inc()
