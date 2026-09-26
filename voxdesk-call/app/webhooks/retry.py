"""Webhook retry policy. Permanent 4xx is not retried. 429 and 5xx are bounded."""

from __future__ import annotations

import hashlib

MAX_ATTEMPTS = 8
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def classify_status(status: int) -> str:
    if status in _RETRY_STATUS or status >= 500:
        return "retryable" if status != 501 else "permanent"
    if 400 <= status < 500:
        return "permanent"
    if 200 <= status < 300:
        return "success"
    if 300 <= status < 400:
        return "permanent"
    return "permanent"


def classify_network(category: str) -> str:
    if category in {"timeout", "network", "connection_failure"}:
        return "retryable"
    return "permanent"


def next_delay(attempt: int, *, key: str = "") -> int:
    raw = min(3600, 30 * (2 ** max(0, attempt - 1)))
    fraction = int(hashlib.sha256(key.encode()).hexdigest()[:4], 16) % 200 / 1000 if key else 0
    return max(1, int(raw * (1 - fraction)))


def should_retry(kind: str, attempt: int) -> bool:
    return kind == "retryable" and attempt < MAX_ATTEMPTS
