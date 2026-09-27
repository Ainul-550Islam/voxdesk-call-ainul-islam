"""Durable job package.

PostgreSQL is the queue. Process memory is not a source of truth. Processing is
at-least-once; side effects are protected by database idempotency keys.

Batch 07 public surface, kept import-light on purpose: this module exposes
the *contracts* (statuses, priorities, failure classes, results, the handler
registry) and the queue operations. It deliberately does not import the
worker loop, the heartbeat task machinery or any provider SDK — the
scheduler entrypoint wires those, and a route or service that only enqueues
work never pays for them.

Import map for callers:

* enqueue / claim / ack / fail / cancel / retry / list → :mod:`app.jobs.queue`
  (re-exported below)
* scheduling + due discovery + tick → :mod:`app.jobs.scheduler`
* retry policy internals → :mod:`app.jobs.retry`
* DLQ operations → :mod:`app.jobs.dead_letter`; replay → :mod:`app.jobs.replay`
* lease/heartbeat internals → :mod:`app.jobs.heartbeat`
* concurrency limits → :mod:`app.jobs.concurrency`
* business idempotency keys → :mod:`app.jobs.idempotency`
* the worker → :mod:`app.jobs.worker` (scheduler-only)
"""

from app.jobs.models import (
    AttemptState,
    JobState,
    JobView,
    PermanentJobError,
    RetryableJobError,
)
from app.jobs.queue import (
    ack,
    cancel,
    claim_next,
    enqueue,
    fail,
    get,
    list_jobs,
    reschedule,
    retry_job,
)
from app.jobs.types import (
    ExecutionResult,
    FailureClass,
    JobHandler,
    JobPayloadRejected,
    JobPriority,
    JobStatus,
    JobType,
    Lease,
    RetryDecision,
    ensure_payload_safe,
    handler_for,
    register_handler,
    register_handler_function,
    registered_job_types,
)

__all__ = [
    "AttemptState",
    "ExecutionResult",
    "FailureClass",
    "JobHandler",
    "JobPayloadRejected",
    "JobPriority",
    "JobState",
    "JobStatus",
    "JobType",
    "JobView",
    "Lease",
    "PermanentJobError",
    "RetryDecision",
    "RetryableJobError",
    "ack",
    "cancel",
    "claim_next",
    "enqueue",
    "ensure_payload_safe",
    "fail",
    "get",
    "handler_for",
    "list_jobs",
    "register_handler",
    "register_handler_function",
    "registered_job_types",
    "reschedule",
    "retry_job",
]
