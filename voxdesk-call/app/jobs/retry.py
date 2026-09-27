"""Retry classification and backoff with bounded jitter (Batch 07).

One policy module for the durable job platform — the outbox delegates here
and to ``app.webhooks.retry`` for HTTP delivery classes; nothing in this
batch builds a second retry framework.

Classification contract
-----------------------
Permanent (never retried; straight to the DLQ):

* authorization failure (``Forbidden`` / ``ResourceUnauthorized``)
* tenant/environment boundary failure (``BoundaryDenied`` and its parent ``NotFound``)
* invalid payload / schema validation failure (``ValidationFailed`` and its
  subclasses, including ``JobPayloadRejected``)
* business-state conflicts (``LifecycleDenied`` — a retry cannot heal an
  illegal transition)
* data/code errors (``ValueError``, ``KeyError``, ``TypeError``,
  ``AttributeError``): a retry would loop on the same bug
* anything a handler explicitly marks via ``PermanentJobError``
* unsupported job type and cancelled jobs are decided by the worker before
  classification is ever consulted

Transient (retried with bounded exponential backoff until ``max_attempts``):

* ``RetryableJobError`` from the handler
* timeouts and network transport errors (asyncio/stdlib/httpx)
* database connectivity errors (``OperationalError``, ``InterfaceError``)
* every *unexpected* exception, as category ``unhandled`` — at-least-once
  means an unknown failure gets the bounded benefit of the doubt, and the
  attempt cap turns a poison job into a DLQ row instead of a loop

Backoff
-------
``base * 2**(attempt-1)`` capped at ``max_delay`` (30s, 60s, 120s … 3600s —
the same shape ``app.jobs.worker._delay`` and ``app.webhooks.retry`` already
use), then spread *downward* by a deterministic, bounded jitter fraction
derived from ``sha256(key:attempt)``. Deterministic jitter means two workers
(or a test and a reaper) compute the same schedule for the same key, and the
cap keeps the spread inside ``[delay * (1 - jitter_cap), delay]`` — bounded
in both directions, never zero-thundering-herd-synced, never unbounded.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import DatabaseError, InterfaceError, OperationalError

from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.types import FailureClass, RetryDecision
from app.tenancy.isolation import (
    BoundaryDenied,
    Forbidden,
    LifecycleDenied,
    NotFound,
    ValidationFailed,
)

#: Permanent-by-classification exceptions. Order matters only for readability;
#: ``isinstance`` decides. (``BoundaryDenied`` subclasses ``NotFound``.)
_PERMANENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    PermanentJobError,
    Forbidden,
    NotFound,
    LifecycleDenied,
    ValidationFailed,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
)

_TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RetryableJobError,
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
    OSError,
    OperationalError,
    InterfaceError,
)


class RetryCategory(str, enum.Enum):
    """Stable ``last_error_category`` values produced by this module."""

    UNHANDLED = "unhandled"
    TIMEOUT = "timeout"
    NETWORK = "network"
    DATABASE = "database"
    POLICY_DENIED = "policy_denied"
    INVALID_STATE = "invalid_state"
    EXHAUSTED = "attempts_exhausted"


@dataclass(frozen=True)
class RetryPolicy:
    """Delay shape. The attempt *budget* lives on the row (``max_attempts``).

    Per-row budgets win so existing callers (``create_job(max_attempts=1)``
    in the telephony DLQ, ``max_attempts=2`` in tests) keep their semantics.
    """

    base_delay_seconds: int = 30
    max_delay_seconds: int = 3600
    #: Jitter spread cap: the delay is reduced by at most this fraction.
    jitter_cap: float = 0.2


DEFAULT_POLICY = RetryPolicy()


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


def classify_exception(exc: BaseException) -> tuple[FailureClass, str]:
    """Map an exception to ``(FailureClass, category)``.

    ``PermanentJobError`` / ``RetryableJobError`` carry their own category so
    a domain handler stays the authority on its own failures.
    """
    if isinstance(exc, (PermanentJobError, RetryableJobError)):
        failure = (
            FailureClass.PERMANENT if isinstance(exc, PermanentJobError) else FailureClass.TRANSIENT
        )
        return failure, (exc.category or "handler_error")[:64]
    if isinstance(exc, (OperationalError, InterfaceError, DatabaseError)):
        return FailureClass.TRANSIENT, RetryCategory.DATABASE.value
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return FailureClass.TRANSIENT, RetryCategory.TIMEOUT.value
    if isinstance(exc, (ConnectionError, OSError)):
        return FailureClass.TRANSIENT, RetryCategory.NETWORK.value
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a pinned dependency
        httpx = None  # type: ignore[assignment]
    if httpx is not None and isinstance(exc, httpx.TimeoutException):
        return FailureClass.TRANSIENT, RetryCategory.TIMEOUT.value
    if httpx is not None and isinstance(exc, httpx.TransportError):
        return FailureClass.TRANSIENT, RetryCategory.NETWORK.value
    if isinstance(exc, (Forbidden, NotFound, BoundaryDenied)):
        return FailureClass.PERMANENT, RetryCategory.POLICY_DENIED.value
    if isinstance(exc, (LifecycleDenied, ValidationFailed)):
        return FailureClass.PERMANENT, RetryCategory.POLICY_DENIED.value
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
        return FailureClass.PERMANENT, RetryCategory.INVALID_STATE.value
    if isinstance(exc, _PERMANENT_EXCEPTIONS):  # pragma: no cover - defensive
        return FailureClass.PERMANENT, RetryCategory.INVALID_STATE.value
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):  # pragma: no cover - defensive
        return FailureClass.TRANSIENT, RetryCategory.NETWORK.value
    return FailureClass.TRANSIENT, RetryCategory.UNHANDLED.value


def backoff_seconds(
    attempt: int, *, policy: RetryPolicy = DEFAULT_POLICY, key: str = ""
) -> int:
    """Exponential delay with deterministic bounded jitter, in seconds.

    ``attempt`` is the number of attempts already made (1 after the first
    failure). The result lies in ``[raw * (1 - jitter_cap), raw]`` where
    ``raw = min(max_delay, base * 2**(attempt-1))``.
    """
    exponent = max(0, int(attempt) - 1)
    raw = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**exponent))
    raw = max(1, raw)
    if key:
        digest = hashlib.sha256(f"{key}:{attempt}".encode()).hexdigest()
        fraction = (int(digest[:4], 16) % 1000) / 1000 * policy.jitter_cap
        raw = int(raw * (1 - fraction))
    return max(1, raw)


def next_attempt_at(
    attempt: int,
    *,
    now: datetime | None = None,
    policy: RetryPolicy = DEFAULT_POLICY,
    key: str = "",
) -> datetime:
    """When the next attempt may be claimed: ``now + backoff``."""
    moment = _now(now)
    return moment + timedelta(seconds=backoff_seconds(attempt, policy=policy, key=key))


def should_retry(
    *,
    attempt_count: int,
    max_attempts: int,
    failure_class: FailureClass,
) -> bool:
    """Budget + class decide. ``False`` here always means DLQ, never a loop."""
    if failure_class is FailureClass.PERMANENT:
        return False
    return attempt_count < max(1, int(max_attempts))


def decide_retry(
    *,
    attempt_count: int,
    max_attempts: int,
    failure_class: FailureClass,
    category: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    key: str = "",
) -> RetryDecision:
    """Full decision for one failed attempt.

    The caller anchors ``next_attempt_at`` on its own clock via
    ``next_attempt_at(attempt_count, now=..., key=...)``; this decision only
    carries the delay so the scheduling instant stays a single-source value.
    """
    if failure_class is FailureClass.PERMANENT:
        return RetryDecision(
            retry=False, failure_class=failure_class, category=category[:64]
        )
    if attempt_count >= max(1, int(max_attempts)):
        return RetryDecision(
            retry=False,
            failure_class=failure_class,
            category=category[:64],
            exhausted=True,
        )
    delay = backoff_seconds(attempt_count, policy=policy, key=key)
    return RetryDecision(
        retry=True, failure_class=failure_class, category=category[:64], delay_seconds=delay
    )
