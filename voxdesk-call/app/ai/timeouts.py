"""Deadlines. A provider wait cannot outlive the parent request.

Voice gets one short budget and no extra retry. Asynchronous work may use a
larger budget only when the caller names that channel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.ai.models import DeadlineExceeded

VOICE_BUDGET_MS = 1500
TEXT_BUDGET_MS = 8000
ASYNC_BUDGET_MS = 20000
MIN_ATTEMPT_MS = 200


@dataclass(frozen=True)
class Deadline:
    budget_ms: int
    started: float
    channel: str

    def remaining_ms(self) -> int:
        elapsed = int((time.perf_counter() - self.started) * 1000)
        return self.budget_ms - elapsed


def deadline_for(channel: str) -> Deadline:
    if channel == "voice":
        budget = VOICE_BUDGET_MS
    elif channel == "async":
        budget = ASYNC_BUDGET_MS
    else:
        budget = TEXT_BUDGET_MS
    return Deadline(budget_ms=budget, started=time.perf_counter(), channel=channel)


def provider_timeout_ms(deadline: Deadline, requested_ms: int) -> int:
    """Cap the provider wait at the time still left. Never widen it."""
    remaining = deadline.remaining_ms()
    if remaining <= 0:
        raise DeadlineExceeded("Parent deadline is already exhausted")
    if requested_ms < 1:
        raise DeadlineExceeded("Provider timeout must be positive")
    return min(requested_ms, remaining)


def assert_open(deadline: Deadline) -> None:
    if deadline.remaining_ms() < MIN_ATTEMPT_MS:
        raise DeadlineExceeded("Not enough time left for another provider attempt")


def max_attempts(channel: str) -> int:
    """Voice does not grow a retry loop. Async may try one fallback."""
    if channel == "voice":
        return 1
    return 2


def invocation_budget(channel: str) -> dict:
    """Connect, provider, tool and overall budgets. The overall cap is never widened."""
    overall = deadline_for(channel).budget_ms
    if channel == "voice":
        provider_ms = 800
    elif channel == "async":
        provider_ms = 8000
    else:
        provider_ms = 5000
    return {
        "connect_ms": min(1000, overall),
        "provider_ms": min(provider_ms, overall),
        "tool_ms": min(2000, overall),
        "overall_ms": overall,
        "max_attempts": max_attempts(channel),
    }
