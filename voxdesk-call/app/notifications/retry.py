"""Notification retry classification. Permanent 4xx is not retried forever."""

from __future__ import annotations

import hashlib

RETRYABLE = frozenset({"timeout", "network", "rate_limit", "provider_5xx"})
PERMANENT = frozenset(
    {
        "invalid_recipient",
        "invalid_configuration",
        "unauthorized_provider",
        "unsupported_channel",
        "provider_4xx",
    }
)
MAX_ATTEMPTS = 5


def classify(category: str) -> str:
    if category in RETRYABLE:
        return "retryable"
    return "permanent"


def next_delay(attempt: int, *, key: str = "") -> int:
    raw = min(3600, 30 * (2 ** max(0, attempt - 1)))
    if not key:
        return max(1, raw)
    fraction = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % 200 / 1000
    return max(1, int(raw * (1 - fraction)))


def should_retry(category: str, attempt: int) -> bool:
    return classify(category) == "retryable" and attempt < MAX_ATTEMPTS
