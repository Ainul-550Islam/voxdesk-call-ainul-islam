"""Stable durable-job contracts (Batch 07).

Everything a caller, a worker or an operator API needs to agree on lives
here: the job-type strings already persisted in the ``jobs`` table, the
status vocabulary (reused from ``app.jobs.models.JobState`` — one enum, not
a second), priorities, failure classification, execution results and leases.

Handler registry
----------------
The registry is the *only* path from a persisted ``job_type`` string to
executable code. A job payload names a registered type; it can never name an
import path, a module or a callable. That is what keeps the public job APIs
from becoming arbitrary-code-execution endpoints:

    Forbidden by design: {"module": "os", "function": "system", ...}
    Supported:           job_type="outbox.delivery" → registered handler

Registration happens at import time of the owning domain module (the outbox
dispatcher registers ``outbox.delivery`` when ``app.outbox.dispatcher`` is
imported, which the scheduler entrypoint does explicitly). Importing this
module pulls no worker loop and no provider SDK.

Payload safety
--------------
``ensure_payload_safe`` is the write-time guard behind "no secrets in job
payloads": any key whose name smells like a credential rejects the payload
(fail closed — silent redaction would hide business bugs), and serialized
payloads are bounded. The same validator backs the transactional outbox.
"""

from __future__ import annotations

import enum
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.db.models import DurableJob
from app.jobs.models import JobState
from app.tenancy.isolation import ValidationFailed

#: Canonical status vocabulary. ``JobState`` already carries every persisted
#: value; Batch 07 adds ``QUARANTINED`` (operator-held DLQ state) to it in
#: ``app.jobs.models`` and re-exports it here under the contract name.
JobStatus = JobState

#: Bound on serialized job/outbox payload size. Jobs carry *references*
#: (ids, event keys), never transcripts, recordings or bulk rows.
PAYLOAD_MAX_BYTES = 16_384

#: Key-name fragments that must never appear in a durable payload. Mirrors
#: the drop list ``app.telephony.call_events.scrub`` applies to callback
#: payloads — here a match *rejects* instead of silently dropping, because a
#: silently missing business field is worse than a loud 422.
_SECRET_KEY_PARTS = (
    "secret",
    "token",
    "authorization",
    "password",
    "api_key",
    "apikey",
    "credential",
    "signature",
)


class JobType:
    """Persisted ``jobs.job_type`` values.

    The first five already exist in the table (Batch 02+ callers). Batch 07
    adds ``OUTBOX_DELIVERY``, executed by the registered dispatcher handler.
    Unknown types are never executed: the registry-scoped claim does not see
    them and an explicitly-claimed unknown type dead-letters without retry.
    """

    AUTOMATION = "automation"
    TELEPHONY_CALLBACK = "telephony.callback"
    ACD_CALLBACK = "acd_callback"
    LEAD_IMPORT = "lead_import"
    QA_AUTO_REVIEW = "qa_auto_review"
    OUTBOX_DELIVERY = "outbox.delivery"

    #: Every type this codebase knows. Not the registry: a type can be known
    #: (validated at enqueue) before a domain wires its execution handler.
    ALL = frozenset(
        {
            AUTOMATION,
            TELEPHONY_CALLBACK,
            ACD_CALLBACK,
            LEAD_IMPORT,
            QA_AUTO_REVIEW,
            OUTBOX_DELIVERY,
        }
    )


class JobPriority(enum.IntEnum):
    """Claim ordering. Lower claims first; aging protects ``LOW``."""

    HIGH = 0
    NORMAL = 1
    LOW = 2


class FailureClass(str, enum.Enum):
    """Retry classification. Anything permanent goes to the DLQ, not a loop."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class RetryDecision:
    """What the worker does after a failed attempt."""

    retry: bool
    failure_class: FailureClass
    category: str
    delay_seconds: int = 0
    exhausted: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    """Optional structured handler outcome.

    Handlers may also simply return ``None`` (success) or raise
    ``PermanentJobError`` / ``RetryableJobError`` — the worker normalizes all
    three shapes. A result can never claim success for a job whose lease was
    lost: acknowledgement is a guarded update, not a field on this object.
    """

    success: bool
    failure_class: FailureClass | None = None
    category: str = ""
    message: str = ""

    @classmethod
    def ok(cls, message: str = "") -> "ExecutionResult":
        return cls(success=True, message=message[:500])

    @classmethod
    def transient(cls, category: str, message: str = "") -> "ExecutionResult":
        return cls(
            success=False,
            failure_class=FailureClass.TRANSIENT,
            category=category[:64],
            message=message[:500],
        )

    @classmethod
    def permanent(cls, category: str, message: str = "") -> "ExecutionResult":
        return cls(
            success=False,
            failure_class=FailureClass.PERMANENT,
            category=category[:64],
            message=message[:500],
        )


@dataclass(frozen=True)
class Lease:
    """A worker's hold on one job. The row is the truth; this is the view."""

    job_id: uuid.UUID
    worker_id: str
    leased_until: datetime

    def active_at(self, moment: datetime) -> bool:
        return self.leased_until > moment


class JobPayloadRejected(ValidationFailed):
    """A payload carried a credential-shaped key or exceeded the bound."""

    def __init__(self, message: str = "Job payload rejected") -> None:
        super().__init__(message)
        self.code = "job_payload_rejected"


def ensure_payload_safe(payload: dict | None) -> None:
    """Reject credential-shaped keys and oversize payloads. Fail closed.

    Applied at enqueue and at outbox publication. Nested dicts and lists are
    scanned; a violation raises rather than redacting, because a silently
    stripped field would corrupt the business event it belonged to.
    """

    def _scan(node: object, depth: int) -> None:
        if depth > 8:
            raise JobPayloadRejected("job payload nests too deeply")
        if isinstance(node, dict):
            for key, value in node.items():
                name = str(key).lower()
                if any(part in name for part in _SECRET_KEY_PARTS):
                    raise JobPayloadRejected(f"job payload key rejected: {str(key)[:64]}")
                _scan(value, depth + 1)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _scan(item, depth + 1)

    _scan(payload or {}, 0)
    try:
        serialized = json.dumps(payload or {}, default=str, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise JobPayloadRejected("job payload is not serializable") from exc
    if len(serialized.encode("utf-8")) > PAYLOAD_MAX_BYTES:
        raise JobPayloadRejected(
            f"job payload exceeds {PAYLOAD_MAX_BYTES} bytes; store a reference instead"
        )


# ---------------------------------------------------------------------------
# Server-side handler registry
# ---------------------------------------------------------------------------

#: A handler receives the claimed row and returns ``None`` (success) or an
#: :class:`ExecutionResult`; it signals failure by raising the two existing
#: job error classes. It never receives a session it did not open: handlers
#: that touch the database open their own session, which keeps the worker's
#: claim/ack session single-threaded.
JobHandler = Callable[[DurableJob], Awaitable[object | None]]

_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Decorator: bind a persisted job-type string to server-side code."""

    def decorator(handler: JobHandler) -> JobHandler:
        register_handler_function(job_type, handler)
        return handler

    return decorator


def register_handler_function(job_type: str, handler: JobHandler) -> None:
    if not job_type or not isinstance(job_type, str):
        raise ValueError("job_type must be a non-empty string")
    if not callable(handler):
        raise ValueError("handler must be callable")
    _HANDLERS[job_type] = handler


def handler_for(job_type: str) -> JobHandler | None:
    """The only lookup path from a row to code. No import strings, ever."""
    return _HANDLERS.get(job_type)


def registered_job_types() -> frozenset[str]:
    return frozenset(_HANDLERS)
