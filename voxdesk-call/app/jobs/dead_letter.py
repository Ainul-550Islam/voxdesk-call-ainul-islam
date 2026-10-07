"""Durable dead-letter quarantine (Batch 07).

One DLQ, on the one job table: ``dead_letter`` is the exhausted/terminal
failure state the existing repository already writes, and Batch 07 adds the
operator-held ``quarantined`` state beside it plus the safe-metadata rules.

* A poison job parks in ``dead_letter``/``quarantined`` and is invisible to
  claiming forever after — it can never block unrelated jobs, because the
  claimable set is ``queued``/``retry_scheduled`` only.
* Failure metadata is stored *sanitized*: before a job parks in the DLQ its
  payload passes through the existing ``app.telephony.call_events.scrub``
  (drops credential-shaped keys, truncates strings), so an operator can
  inspect a dead job without the row becoming a secret store. Enqueue-time
  validation (``app.jobs.types.ensure_payload_safe``) already rejects
  secrets; this is the defence in depth behind it.
* Quarantine/release/replay are guarded conditional updates: two operators
  acting at once produce exactly one state change, and the loser sees the
  row's real state rather than corrupting it.
* Replay itself lives in ``app.jobs.replay`` (authorization + budget +
  provenance); this module owns the states replay acts on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DurableJob, JobAttempt
from app.jobs.models import AttemptState
from app.jobs.repository import get_job
from app.jobs.repository import move_to_dlq as _repository_move_to_dlq
from app.telephony.call_events import scrub
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied

#: Operator-facing DLQ states. ``dead_letter`` is exhausted-or-permanent;
#: ``quarantined`` is an operator hold on top of it.
DLQ_STATUSES = ("dead_letter", "quarantined")


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


async def close_open_attempt(session: AsyncSession, job: DurableJob, moment: datetime) -> None:
    await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.job_id == job.id,
            JobAttempt.status == AttemptState.STARTED.value,
        )
        .values(status=AttemptState.FAILED.value, finished_at=moment), execution_options={"synchronize_session": False})


async def move_to_dlq(
    session: AsyncSession,
    job: DurableJob,
    *,
    category: str,
    now: datetime | None = None,
) -> DurableJob:
    """Park an exhausted/permanently-failed job in the DLQ, sanitized.

    Wraps the existing repository transition (one implementation of the
    status change) and adds the two DLQ-specific duties: payload scrubbing
    and closing the open attempt row as ``failed``.
    """
    moment = _now(now)
    job.payload = scrub(dict(job.payload or {}))
    await close_open_attempt(session, job, moment)
    return await _repository_move_to_dlq(session, job, category=category, now=moment)


async def quarantine_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    now: datetime | None = None,
) -> DurableJob:
    """Operator hold: ``dead_letter`` → ``quarantined``.

    Guarded, tenant-scoped, and idempotent-by-state: quarantining an already
    quarantined job is a no-op success, anything else that is not a
    dead-letter row is a conflict, and another tenant's row does not exist.
    """
    moment = _now(now)
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None:
        raise BoundaryDenied()
    if job.status == "quarantined":
        return job
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.tenant_id == tenant_id,
            DurableJob.status == "dead_letter",
        )
        .values(status="quarantined", completed_at=DurableJob.completed_at or moment), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        await session.refresh(job)
        raise LifecycleDenied("only a dead-letter job can be quarantined")
    await session.refresh(job)
    return job


async def release_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    now: datetime | None = None,
) -> DurableJob:
    """Operator un-hold: ``quarantined`` → ``dead_letter`` (replayable)."""
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None:
        raise BoundaryDenied()
    if job.status == "dead_letter":
        return job
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.tenant_id == tenant_id,
            DurableJob.status == "quarantined",
        )
        .values(status="dead_letter"), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        await session.refresh(job)
        raise LifecycleDenied("only a quarantined job can be released")
    await session.refresh(job)
    return job


async def list_dead_letters(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    statuses: tuple[str, ...] = DLQ_STATUSES,
    job_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DurableJob]:
    """Tenant-scoped DLQ listing, most recently parked first."""
    stmt = (
        select(DurableJob)
        .where(DurableJob.tenant_id == tenant_id, DurableJob.status.in_(statuses))
        .order_by(DurableJob.completed_at.desc(), DurableJob.created_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .offset(max(0, int(offset)))
    )
    if job_type:
        stmt = stmt.where(DurableJob.job_type == job_type)
    return list((await session.execute(stmt)).scalars())


async def inspect_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[DurableJob, list[JobAttempt]] | None:
    """One DLQ (or any) job with its full attempt provenance, tenant-scoped.

    ``None`` for a missing row *and* for another tenant's row — the operator
    API renders both as 404 so existence is never leaked across tenants.
    """
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None:
        return None
    attempts = list(
        (
            await session.execute(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.attempt_number, JobAttempt.started_at)
            )
        ).scalars()
    )
    return job, attempts
