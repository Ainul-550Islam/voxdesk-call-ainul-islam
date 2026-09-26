"""PostgreSQL-backed job persistence.

Claim uses ``FOR UPDATE SKIP LOCKED`` on PostgreSQL. Other dialects use a
compare-and-set update so two workers cannot both leave the row running.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DurableJob, JobAttempt
from app.jobs.models import JobView

_CLAIMABLE = ("queued", "retry_scheduled")
_MAX_REPLAYS = 3


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


def view_of(job: DurableJob) -> JobView:
    return JobView(
        id=job.id,
        organization_id=job.organization_id,
        tenant_id=job.tenant_id,
        environment_id=job.environment_id,
        job_type=job.job_type,
        payload=dict(job.payload or {}),
        status=job.status,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        worker_id=job.worker_id,
        idempotency_key=job.idempotency_key,
        available_at=job.available_at,
        leased_until=job.leased_until,
    )


async def create_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_type: str,
    idempotency_key: str,
    organization_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    payload: dict | None = None,
    max_attempts: int = 5,
    available_at: datetime | None = None,
) -> tuple[DurableJob, bool]:
    """Insert a job. A duplicate tenant idempotency key returns the existing row."""
    moment = _now(available_at)
    job = DurableJob(
        organization_id=organization_id,
        tenant_id=tenant_id,
        environment_id=environment_id,
        job_type=job_type,
        payload=payload or {},
        status="queued",
        max_attempts=max_attempts,
        available_at=moment,
        idempotency_key=idempotency_key,
    )
    try:
        async with session.begin_nested():
            session.add(job)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(DurableJob).where(
                    DurableJob.tenant_id == tenant_id,
                    DurableJob.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one()
        return found, False
    return job, True


async def get_job(
    session: AsyncSession, job_id: uuid.UUID, *, tenant_id: uuid.UUID | None = None
) -> DurableJob | None:
    job = await session.get(DurableJob, job_id)
    if job is None:
        return None
    if tenant_id is not None and job.tenant_id != tenant_id:
        return None
    return job


def _claim_filters(moment: datetime, tenant_id: uuid.UUID | None):
    filters = [
        DurableJob.status.in_(_CLAIMABLE),
        DurableJob.available_at <= moment,
        or_(DurableJob.leased_until.is_(None), DurableJob.leased_until < moment),
    ]
    if tenant_id is not None:
        filters.append(DurableJob.tenant_id == tenant_id)
    return filters


async def _record_attempt(
    session: AsyncSession, job: DurableJob, worker_id: str, moment: datetime, status: str
) -> None:
    session.add(
        JobAttempt(
            job_id=job.id,
            attempt_number=job.attempt_count,
            worker_id=worker_id,
            status=status,
            started_at=moment,
        )
    )
    await session.flush()


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 30,
    now: datetime | None = None,
    tenant_id: uuid.UUID | None = None,
) -> DurableJob | None:
    moment = _now(now)
    lease_until = moment + timedelta(seconds=lease_seconds)
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    filters = _claim_filters(moment, tenant_id)
    if dialect == "postgresql":
        job = (
            await session.execute(
                select(DurableJob)
                .where(*filters)
                .order_by(DurableJob.available_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.worker_id = worker_id
        job.leased_until = lease_until
        job.started_at = job.started_at or moment
        job.attempt_count += 1
        await _record_attempt(session, job, worker_id, moment, "started")
        return job

    candidate = (
        await session.execute(
            select(DurableJob.id).where(*filters).order_by(DurableJob.available_at).limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        return None
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == candidate,
            DurableJob.status.in_(_CLAIMABLE),
            or_(DurableJob.leased_until.is_(None), DurableJob.leased_until < moment),
        )
        .values(
            status="running",
            worker_id=worker_id,
            leased_until=lease_until,
            started_at=moment,
        )
    )
    if result.rowcount != 1:
        return None
    await session.flush()
    job = await session.get(DurableJob, candidate)
    if job is None:
        return None
    job.attempt_count += 1
    job.started_at = job.started_at or moment
    await _record_attempt(session, job, worker_id, moment, "started")
    return job


async def renew_lease(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    lease_seconds: int = 30,
    now: datetime | None = None,
) -> bool:
    moment = _now(now)
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.worker_id == worker_id,
            DurableJob.status == "running",
        )
        .values(leased_until=moment + timedelta(seconds=lease_seconds))
    )
    await session.flush()
    return result.rowcount == 1


async def mark_succeeded(
    session: AsyncSession, job: DurableJob, *, now: datetime | None = None
) -> DurableJob:
    moment = _now(now)
    job.status = "succeeded"
    job.completed_at = moment
    job.leased_until = None
    job.worker_id = ""
    await session.flush()
    return job


async def schedule_retry(
    session: AsyncSession,
    job: DurableJob,
    *,
    category: str,
    delay_seconds: int,
    now: datetime | None = None,
) -> DurableJob:
    moment = _now(now)
    job.status = "retry_scheduled"
    job.available_at = moment + timedelta(seconds=max(1, delay_seconds))
    job.leased_until = None
    job.worker_id = ""
    job.last_error_category = category[:64]
    job.last_error = category[:500]
    await session.flush()
    return job


async def move_to_dlq(
    session: AsyncSession,
    job: DurableJob,
    *,
    category: str,
    now: datetime | None = None,
) -> DurableJob:
    moment = _now(now)
    job.status = "dead_letter"
    job.completed_at = moment
    job.leased_until = None
    job.worker_id = ""
    job.last_error_category = category[:64]
    job.last_error = category[:500]
    await session.flush()
    return job


async def cancel_job(
    session: AsyncSession, job: DurableJob, *, now: datetime | None = None
) -> DurableJob:
    if job.status == "succeeded":
        return job
    job.status = "cancelled"
    job.completed_at = _now(now)
    job.leased_until = None
    job.worker_id = ""
    await session.flush()
    return job


async def recover_expired_jobs(session: AsyncSession, *, now: datetime | None = None) -> int:
    moment = _now(now)
    result = await session.execute(
        update(DurableJob)
        .where(DurableJob.status == "running", DurableJob.leased_until < moment)
        .values(status="queued", worker_id="", leased_until=None, available_at=moment)
    )
    await session.flush()
    return int(result.rowcount or 0)


async def replay_dead_letter(
    session: AsyncSession,
    job: DurableJob,
    *,
    now: datetime | None = None,
    extra_attempts: int = 5,
) -> DurableJob:
    if job.status != "dead_letter":
        raise ValueError("only a dead-letter job can be replayed")
    if job.replay_count >= _MAX_REPLAYS:
        raise ValueError("replay budget exhausted")
    moment = _now(now)
    job.replay_count += 1
    job.max_attempts = job.attempt_count + extra_attempts
    job.status = "queued"
    job.available_at = moment
    job.leased_until = None
    job.worker_id = ""
    job.completed_at = None
    await session.flush()
    return job
