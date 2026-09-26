"""Automation retry classification and bounded backoff."""

from __future__ import annotations

import hashlib

PERMANENT = frozenset(
    {
        "validation_error",
        "authorization_error",
        "not_found",
        "environment_mismatch",
        "tenant_mismatch",
        "invalid_configuration",
    }
)
RETRYABLE = frozenset(
    {
        "connection_failure",
        "timeout",
        "provider_5xx",
        "rate_limit",
        "transient_database",
    }
)


def classify(category: str) -> str:
    if category in PERMANENT:
        return "permanent"
    if category in RETRYABLE:
        return "retryable"
    return "permanent"


def backoff_seconds(attempt: int, *, key: str = "", base: int = 30, cap: int = 3600) -> int:
    raw = min(cap, base * (2 ** max(0, attempt - 1)))
    if not key:
        return max(1, raw)
    fraction = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % 200 / 1000
    return max(1, int(raw * (1 - fraction)))
