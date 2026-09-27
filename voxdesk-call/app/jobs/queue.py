"""Public durable-queue operations against the existing ``DurableJob`` store.

This module is the Batch 07 contract surface; persistence stays where it
already lives (``app.jobs.repository`` — one job table, one claim
implementation, ``FOR UPDATE SKIP LOCKED`` on PostgreSQL and a
compare-and-set update elsewhere). What this layer adds:

* **Enqueue** with payload safety (no secrets, bounded size), known job
  types only, priority and durable scheduling — inside the caller's
  transaction, so a business mutation and its job commit or roll back
  together.
* **Claim** scoped to registered job types and gated by database-counted
  concurrency admission, ordered by priority with age promotion.
* **Guarded terminal writes.** ``ack``/``fail`` are conditional updates
  requiring ``worker_id`` match, ``status='running'`` and an unexpired
  lease. A worker whose lease lapsed — whose job was reaped and re-claimed
  by someone else — gets ``rowcount == 0`` and its stale success or failure
  is refused. The newer worker's valid result can never be overwritten.
* **Durable cancellation.** A claimable job cancels immediately; a running
  job records ``cancel_requested`` and is finalised at the next safe point
  (the guarded ack checks the flag, so a cancel that lands mid-handler beats
  the handler's late success; the reaper finalises it if the worker died).

All operations are tenant-scoped where an identity is given, and every one
of them is a database write: nothing here keeps state in process memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import DurableJob, JobAttempt
from app.jobs.concurrency import DEFAULT_LIMITS, ConcurrencyLimits, admission_conditions
from app.jobs.dead_letter import close_open_attempt
from app.jobs.models import AttemptState
from app.jobs.repository import _CLAIMABLE, create_job, get_job
from app.jobs.repository import claim_next_job as _repository_claim_next
from app.jobs.retry import DEFAULT_POLICY, RetryPolicy, decide_retry, next_attempt_at
from app.jobs.types import (
    FailureClass,
    JobPayloadRejected,
    JobPriority,
    JobType,
    ensure_payload_safe,
)
from app.telephony.call_events import scrub
from app.tenancy.isolation import LifecycleDenied

#: Terminal states an operator can no longer cancel or retry.
_TERMINAL = ("succeeded", "cancelled", "dead_letter", "quarantined")


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_type: str,
    idempotency_key: str,
    payload: dict | None = None,
    priority: JobPriority | int = JobPriority.NORMAL,
    environment_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    max_attempts: int = 5,
    available_at: datetime | None = None,
) -> tuple[DurableJob, bool]:
    """Durably enqueue work inside the caller's transaction.

    Duplicate ``(tenant_id, idempotency_key)`` returns the existing row with
    ``created=False`` — the same logical operation can be submitted by a
    retry, a second replica or a replayed webhook without duplicating work.
    Flushes; the caller commits (transaction ownership is unchanged).
    """
    if job_type not in JobType.ALL:
        # Server-side vocabulary only. A client-chosen "type" that is not a
        # known string never reaches the table, and no payload can ever name
        # a module/function instead.
        raise JobPayloadRejected(f"unknown job type: {str(job_type)[:64]}")
    ensure_payload_safe(payload)
    return await create_job(
        session,
        tenant_id=tenant_id,
        job_type=job_type,
        idempotency_key=idempotency_key,
        organization_id=organization_id,
        environment_id=environment_id,
        payload=payload,
        max_attempts=max_attempts,
        available_at=available_at,
        priority=int(priority),
    )


async def claim_next(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 30,
    now: datetime | None = None,
    tenant_id: uuid.UUID | None = None,
    job_types: tuple[str, ...] | None = None,
    limits: ConcurrencyLimits | None = DEFAULT_LIMITS,
) -> DurableJob | None:
    """Atomically claim the next due job for this worker.

    One winner is guaranteed by the store: PostgreSQL takes the row
    ``FOR UPDATE SKIP LOCKED``; other dialects run a compare-and-set update
    whose ``rowcount != 1`` means the candidate was lost and ``None`` is
    returned. Concurrency admission rides inside both, so the limits and the
    claim are decided by one atomic statement.
    """
    extra = admission_conditions(limits) if limits is not None else ()
    return await _repository_claim_next(
        session,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=now,
        tenant_id=tenant_id,
        job_types=job_types,
        extra_filters=extra,
    )


def _lease_guard(job: DurableJob, worker_id: str, moment: datetime):
    """Conditions under which this worker may still write the row's fate."""
    return [
        DurableJob.id == job.id,
        DurableJob.worker_id == worker_id,
        DurableJob.status == "running",
        DurableJob.leased_until.is_not(None),
        DurableJob.leased_until > moment,
    ]


async def finalize_cancel(job: DurableJob, session: AsyncSession, worker_id: str, moment) -> bool:
    """Cancel-wins finalisation at ack/fail time. True when it applied."""
    result = await session.execute(
        update(DurableJob)
        .where(*_lease_guard(job, worker_id, moment), DurableJob.cancel_requested.is_(True))
        .values(status="cancelled", completed_at=moment, worker_id="", leased_until=None), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        return False
    await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.job_id == job.id,
            JobAttempt.status == AttemptState.STARTED.value,
        )
        .values(
            status=AttemptState.FAILED.value,
            error_category="cancelled",
            finished_at=moment,
        ), execution_options={"synchronize_session": False})
    job.status = "cancelled"
    job.completed_at = moment
    job.worker_id = ""
    job.leased_until = None
    await session.flush()
    return True


async def ack(
    session: AsyncSession,
    job: DurableJob,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> bool:
    """Guarded success acknowledgement.

    Returns ``True`` only when *this* worker's terminal write landed —
    either the job succeeded or a durable cancellation took precedence over
    the success (check ``job.status``). ``False`` means the lease was lost
    (reaped, re-claimed, expired): the row is left exactly as the newer
    holder has it, and the caller must not treat its execution as recorded.
    """
    moment = _now(now)
    if await finalize_cancel(job, session, worker_id, moment):
        log.info("jobs.cancel_wins_ack", job_id=str(job.id), worker_id=worker_id)
        return True
    result = await session.execute(
        update(DurableJob)
        .where(*_lease_guard(job, worker_id, moment), DurableJob.cancel_requested.is_(False))
        .values(status="succeeded", completed_at=moment, worker_id="", leased_until=None), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        await session.flush()
        log.info("jobs.stale_ack_rejected", job_id=str(job.id), worker_id=worker_id)
        return False
    await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.job_id == job.id,
            JobAttempt.status == AttemptState.STARTED.value,
        )
        .values(status=AttemptState.SUCCEEDED.value, finished_at=moment), execution_options={"synchronize_session": False})
    job.status = "succeeded"
    job.completed_at = moment
    job.worker_id = ""
    job.leased_until = None
    await session.flush()
    return True


async def fail(
    session: AsyncSession,
    job: DurableJob,
    *,
    worker_id: str,
    category: str,
    failure_class: FailureClass,
    message: str = "",
    now: datetime | None = None,
    policy: RetryPolicy = DEFAULT_POLICY,
) -> str:
    """Guarded failure handling: retry with backoff, DLQ, or cancel-wins.

    Returns ``'retry_scheduled'``, ``'dead_letter'``, ``'cancelled'`` or
    ``'stale'`` (lease lost — nothing was written). Retry uses the bounded,
    deterministically-jittered backoff from ``app.jobs.retry``; exhaustion
    (``attempt_count >= max_attempts``) and every permanent classification
    park the job in the DLQ with a scrubbed payload.
    """
    moment = _now(now)
    if await finalize_cancel(job, session, worker_id, moment):
        return "cancelled"
    decision = decide_retry(
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        failure_class=failure_class,
        category=category,
        policy=policy,
        key=str(job.id),
    )
    if decision.retry:
        available = next_attempt_at(
            job.attempt_count, now=moment, policy=policy, key=str(job.id)
        )
        result = await session.execute(
            update(DurableJob)
            .where(*_lease_guard(job, worker_id, moment), DurableJob.cancel_requested.is_(False))
            .values(
                status="retry_scheduled",
                available_at=available,
                last_error_category=decision.category,
                last_error=(message or decision.category)[:500],
                worker_id="",
                leased_until=None,
            ), execution_options={"synchronize_session": False})
        outcome = "retry_scheduled"
        if result.rowcount == 1:
            job.status = "retry_scheduled"
            job.available_at = available
            job.last_error_category = decision.category
            job.last_error = (message or decision.category)[:500]
            job.worker_id = ""
            job.leased_until = None
    else:
        dlq_category = decision.category or category
        result = await session.execute(
            update(DurableJob)
            .where(*_lease_guard(job, worker_id, moment), DurableJob.cancel_requested.is_(False))
            .values(
                status="dead_letter",
                completed_at=moment,
                last_error_category=dlq_category[:64],
                last_error=(message or dlq_category)[:500],
                worker_id="",
                leased_until=None,
                payload=scrub(dict(job.payload or {})),
            ), execution_options={"synchronize_session": False})
        outcome = "dead_letter"
        if result.rowcount == 1:
            job.status = "dead_letter"
            job.completed_at = moment
            job.last_error_category = dlq_category[:64]
            job.last_error = (message or dlq_category)[:500]
            job.worker_id = ""
            job.leased_until = None
            job.payload = scrub(dict(job.payload or {}))
    if result.rowcount != 1:
        await session.flush()
        log.info("jobs.stale_fail_rejected", job_id=str(job.id), worker_id=worker_id)
        return "stale"
    await close_open_attempt(session, job, moment)
    await session.flush()
    return outcome


async def cancel(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    now: datetime | None = None,
) -> str | None:
    """Durable, race-safe cancellation.

    * claimable row → ``'cancelled'`` now (guarded on the claimable states,
      so a concurrent claim cannot be overwritten by a late cancel);
    * running row → ``'cancel_requested'``: the flag is durable, the worker
      finalises it at its guarded ack (cancel beats success) and the reaper
      finalises it if the worker dies;
    * terminal row → ``'already:<status>'`` (cancelling twice is a no-op);
    * missing row or another tenant's row → ``None`` (rendered 404).
    """
    moment = _now(now)
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None:
        return None
    if job.status in _TERMINAL:
        return f"already:{job.status}"
    if job.status in _CLAIMABLE:
        result = await session.execute(
            update(DurableJob)
            .where(
                DurableJob.id == job_id,
                DurableJob.tenant_id == tenant_id,
                DurableJob.status.in_(_CLAIMABLE),
            )
            .values(status="cancelled", completed_at=moment, worker_id="", leased_until=None), execution_options={"synchronize_session": False})
        if result.rowcount == 1:
            job.status = "cancelled"
            job.completed_at = moment
            job.worker_id = ""
            job.leased_until = None
            await session.flush()
            return "cancelled"
        await session.refresh(job)  # lost the race with a claim; fall through
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.tenant_id == tenant_id,
            DurableJob.status == "running",
        )
        .values(cancel_requested=True), execution_options={"synchronize_session": False})
    if result.rowcount == 1:
        job.cancel_requested = True
        await session.flush()
        return "cancel_requested"
    await session.refresh(job)
    return f"already:{job.status}"


async def retry_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    now: datetime | None = None,
) -> str:
    """Operator retry of a non-DLQ job: cancelled resumes, retry_scheduled runs now.

    Dead-letter/quarantined rows must go through ``replay`` (budget +
    provenance); a running or already-queued job is a conflict, not a retry.
    """
    moment = _now(now)
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None:
        raise LifecycleDenied("job not found")
    if job.status in ("dead_letter", "quarantined"):
        raise LifecycleDenied("use replay for dead-letter jobs")
    if job.status in ("running", "queued"):
        raise LifecycleDenied(f"job is {job.status}")
    if job.status == "succeeded":
        raise LifecycleDenied("job already succeeded")
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.tenant_id == tenant_id,
            DurableJob.status.in_(("cancelled", "retry_scheduled")),
        )
        .values(
            status="queued",
            cancel_requested=False,
            completed_at=None,
            available_at=moment,
            worker_id="",
            leased_until=None,
        ), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        await session.refresh(job)
        raise LifecycleDenied(f"job is {job.status}")
    job.status = "queued"
    job.cancel_requested = False
    job.completed_at = None
    job.available_at = moment
    job.worker_id = ""
    job.leased_until = None
    await session.flush()
    return "queued"


async def reschedule(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    available_at: datetime,
) -> bool:
    """Move a claimable job's due time. Guarded: terminal rows never move."""
    moment = _now(available_at)
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.tenant_id == tenant_id,
            DurableJob.status.in_(_CLAIMABLE),
        )
        .values(available_at=moment), execution_options={"synchronize_session": False})
    await session.flush()
    return result.rowcount == 1


async def get(
    session: AsyncSession, job_id: uuid.UUID, *, tenant_id: uuid.UUID | None = None
) -> DurableJob | None:
    """One job by id, tenant-scoped (``None`` hides other tenants' rows)."""
    return await get_job(session, job_id, tenant_id=tenant_id)


async def list_jobs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
    job_type: str | None = None,
    environment_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DurableJob]:
    """Tenant-scoped inspection listing, newest first. Read-only."""
    stmt = select(DurableJob).where(DurableJob.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(DurableJob.status == status)
    if job_type:
        stmt = stmt.where(DurableJob.job_type == job_type)
    if environment_id is not None:
        stmt = stmt.where(DurableJob.environment_id == environment_id)
    stmt = (
        stmt.order_by(DurableJob.created_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .offset(max(0, int(offset)))
    )
    return list((await session.execute(stmt)).scalars())
