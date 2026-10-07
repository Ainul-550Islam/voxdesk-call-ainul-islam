"""Dead-letter handling for provider callbacks.

The job row is a ``DurableJob`` of type ``telephony.callback``. This module
does not create a second worker. The payload stores the event id, provider,
type, attempt count and error class. It does not store secrets or media URLs.
"""

from __future__ import annotations

from app.jobs.repository import create_job, move_to_dlq
from app.telephony.call_events import TelephonyCallbackEvent, scrub
from app.telephony.provider_errors import TelephonyError

_MAX_ATTEMPTS = 5
JOB_TYPE = "telephony.callback"


def safe_payload(event: TelephonyCallbackEvent) -> dict:
    return scrub(
        {
            "event_id": str(event.id),
            "provider": event.provider,
            "event_type": event.event_type,
            "attempt_count": event.attempt_count,
            "error_class": event.error_class,
        }
    )


async def note_failure(session, event: TelephonyCallbackEvent, exc: Exception) -> dict:
    """Count a failure. Exhaustion moves the existing job, or creates one, to the DLQ."""
    if event.tenant_id is None:
        event.status = "unmatched"
        event.error_class = "tenant_missing"
        await session.flush()
        return {"status": event.status, "outcome": "unmatched"}
    event.attempt_count += 1
    event.error_class = _class_of(exc)[:64]
    if event.attempt_count < _MAX_ATTEMPTS and _retryable(exc):
        event.status = "retry_scheduled"
        job, _created = await create_job(
            session,
            tenant_id=event.tenant_id,
            job_type=JOB_TYPE,
            idempotency_key=f"telephony-retry:{event.idempotency_key}:{event.attempt_count}",
            payload=safe_payload(event),
            max_attempts=1,
        )
        event.job_id = job.id
        await session.flush()
        return {"status": event.status, "outcome": "accepted_for_retry", "job_id": str(job.id)}
    job, _created = await create_job(
        session,
        tenant_id=event.tenant_id,
        job_type=JOB_TYPE,
        idempotency_key=f"telephony-dlq:{event.idempotency_key}",
        payload=safe_payload(event),
        max_attempts=1,
    )
    await move_to_dlq(session, job, category=event.error_class or "callback_failed")
    event.job_id = job.id
    event.status = "dead_letter"
    await session.flush()
    return {"status": event.status, "outcome": "dead_letter", "job_id": str(job.id)}


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, TelephonyError):
        return exc.retryable
    return False


def _class_of(exc: Exception) -> str:
    if isinstance(exc, TelephonyError):
        return exc.code
    return type(exc).__name__
