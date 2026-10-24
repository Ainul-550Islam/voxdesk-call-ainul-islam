"""Sink contract shared by every SIEM transport.

A sink answers exactly one question — "what happened to this batch" — with a
three-way answer, because a two-way one loses information the dispatcher needs:

* **accepted**: the destination took the events. The cursor may advance.
* **retryable**: the destination is unavailable or the request may succeed
  later (timeout, 429, 5xx, TLS handshake failure). The cursor must not
  advance; the dispatcher backs off.
* **permanent**: the destination rejected specific events (a 400 for a
  malformed body, an Elastic per-item mapping error). Retrying those events
  forever would wedge the pipeline behind a poison record, so they are
  dead-lettered individually and the cursor advances past them.

A sink that raises instead of returning a result is treated as retryable by the
dispatcher only when the exception is a transport error; any other exception is
a programming error and is surfaced as-is.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any, Iterable

from app.compliance_v2.siem.events import SiemEvent

#: Categories used for metrics, logs and dead-letter rows. Stable strings.
CATEGORY_TIMEOUT = "timeout"
CATEGORY_TRANSPORT = "transport"
CATEGORY_RATE_LIMITED = "rate_limited"
CATEGORY_SERVER = "server_error"
CATEGORY_AUTH = "auth_rejected"
CATEGORY_REJECTED = "rejected"
CATEGORY_MALFORMED = "malformed_event"
CATEGORY_NOT_CONFIGURED = "not_configured"

RETRYABLE_CATEGORIES = frozenset(
    {CATEGORY_TIMEOUT, CATEGORY_TRANSPORT, CATEGORY_RATE_LIMITED, CATEGORY_SERVER}
)


class SinkError(RuntimeError):
    """Base class for sink failures that are not a returned result."""

    category = CATEGORY_TRANSPORT

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        if category:
            self.category = category


class RetryableSinkError(SinkError):
    """A transient failure: the dispatcher backs off and tries again."""

    category = CATEGORY_TRANSPORT


class PermanentSinkError(SinkError):
    """A failure that will not improve on retry (bad credentials, bad URL)."""

    category = CATEGORY_AUTH


class SinkNotConfiguredError(PermanentSinkError):
    category = CATEGORY_NOT_CONFIGURED


@dataclasses.dataclass(frozen=True)
class SinkResult:
    """Outcome of one ``send_batch`` call."""

    accepted: int = 0
    retryable: bool = False
    category: str = ""
    message: str = ""
    #: Event ids the destination refused permanently; dead-lettered by the
    #: dispatcher while the rest of the batch still advances the cursor.
    permanent_event_ids: tuple[str, ...] = ()
    #: Free-form, non-secret transport detail (HTTP status, ack id, item counts).
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def success(cls, accepted: int, *, detail: dict[str, Any] | None = None) -> "SinkResult":
        return cls(accepted=accepted, category="accepted", detail=dict(detail or {}))

    @classmethod
    def retry(cls, category: str, message: str, *, detail: dict[str, Any] | None = None) -> "SinkResult":
        return cls(
            accepted=0,
            retryable=True,
            category=category,
            message=message[:500],
            detail=dict(detail or {}),
        )

    @classmethod
    def permanent(
        cls,
        category: str,
        message: str,
        *,
        accepted: int = 0,
        permanent_event_ids: Iterable[str] = (),
        detail: dict[str, Any] | None = None,
    ) -> "SinkResult":
        return cls(
            accepted=accepted,
            retryable=False,
            category=category,
            message=message[:500],
            permanent_event_ids=tuple(permanent_event_ids),
            detail=dict(detail or {}),
        )

    @property
    def delivered_all(self) -> bool:
        """True when nothing about this batch needs another attempt."""
        return not self.retryable and not self.permanent_event_ids


@dataclasses.dataclass(frozen=True)
class SinkHealth:
    """What a health probe learned about a destination."""

    ok: bool
    detail: str = ""
    checked_at: str = ""


class SiemSink(ABC):
    """One log destination.

    Implementations must be constructible from stored configuration alone, so
    the dispatcher can build a sink per tick without process-local state.
    """

    #: Matches ``compliance_siem_sinks.kind``.
    kind: str = ""

    @abstractmethod
    async def send_batch(self, events: list[SiemEvent]) -> SinkResult:
        """Deliver ``events`` (never empty) and report the outcome."""

    @abstractmethod
    async def health(self) -> SinkHealth:
        """Probe the destination without sending an event."""

    async def aclose(self) -> None:
        """Release any client held by the sink. Idempotent."""
        return None


__all__ = [
    "CATEGORY_AUTH",
    "CATEGORY_MALFORMED",
    "CATEGORY_NOT_CONFIGURED",
    "CATEGORY_RATE_LIMITED",
    "CATEGORY_REJECTED",
    "CATEGORY_SERVER",
    "CATEGORY_TIMEOUT",
    "CATEGORY_TRANSPORT",
    "RETRYABLE_CATEGORIES",
    "PermanentSinkError",
    "RetryableSinkError",
    "SiemSink",
    "SinkError",
    "SinkHealth",
    "SinkNotConfiguredError",
    "SinkResult",
]
