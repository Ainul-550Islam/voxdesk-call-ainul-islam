"""PostgreSQL-backed job persistence.

Claim uses ``FOR UPDATE SKIP LOCKED`` on PostgreSQL. Other dialects use a
compare-and-set update so two workers cannot both leave the row running.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DurableJob, JobAttempt
from app.jobs.models import JobView

_CLAIMABLE = ("queued", "retry_scheduled")
_MAX_REPLAYS = 3

#: Batch 07 fairness: a claimable row older than this is promoted to the
#: front of the order regardless of priority, so a stream of high-priority
#: work can never starve low-priority rows forever.
DEFAULT_AGING_SECONDS = 900


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
    priority: int = 1,
) -> tuple[DurableJob, bool]:
    """Insert a job. A duplicate tenant idempotency key returns the existing row.

    ``priority`` (Batch 07) orders claiming: 0=high, 1=normal, 2=low, with
    age-based promotion applied at claim time so ``low`` cannot starve.
    """
    moment = _now(available_at)
    job = DurableJob(
        organization_id=organization_id,
        tenant_id=tenant_id,
        environment_id=environment_id,
        job_type=job_type,
        payload=payload or {},
        status="queued",
        priority=int(priority),
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


def _claim_filters(
    moment: datetime,
    tenant_id: uuid.UUID | None,
    job_types: Sequence[str] | None = None,
    extra_filters: Sequence = (),
):
    filters = [
        DurableJob.status.in_(_CLAIMABLE),
        DurableJob.available_at <= moment,
        or_(DurableJob.leased_until.is_(None), DurableJob.leased_until < moment),
        # Batch 07: a row with durable cancellation intent is never claimed;
        # the reaper finalises it. (server_default false keeps legacy rows
        # claimable, so existing behaviour is unchanged.)
        DurableJob.cancel_requested.is_(False),
    ]
    if tenant_id is not None:
        filters.append(DurableJob.tenant_id == tenant_id)
    if job_types:
        filters.append(DurableJob.job_type.in_(tuple(job_types)))
    filters.extend(extra_filters)
    return filters


def _claim_order(moment: datetime, aging_seconds: int | None):
    """Fair ordering: aged rows first, then priority, then FIFO by due time.

    With every row at the default priority and nothing older than the aging
    window this collapses to the historical ``ORDER BY available_at``, so
    pre-Batch-07 callers see identical claim order.
    """
    if aging_seconds and aging_seconds > 0:
        cutoff = moment - timedelta(seconds=aging_seconds)
        effective = case(
            (DurableJob.created_at <= cutoff, 0),
            else_=DurableJob.priority,
        )
    else:
        effective = DurableJob.priority
    return [effective, DurableJob.available_at, DurableJob.created_at]


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
    job_types: Sequence[str] | None = None,
    extra_filters: Sequence = (),
    aging_seconds: int | None = DEFAULT_AGING_SECONDS,
) -> DurableJob | None:
    """Claim the next due job for this worker.

    Batch 07 extensions, all default-off so pre-existing callers keep their
    exact behaviour: ``job_types`` restricts the claim to registered types
    (the registry-scoped worker path — unknown/legacy receipt rows are never
    dragged into execution), ``extra_filters`` carries the concurrency
    admission predicates (re-checked inside the compare-and-set so the limit
    and the write are one atomic step), and ``aging_seconds`` drives the
    starvation-proof claim order.
    """
    moment = _now(now)
    lease_until = moment + timedelta(seconds=lease_seconds)
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    filters = _claim_filters(moment, tenant_id, job_types=job_types, extra_filters=extra_filters)
    order = _claim_order(moment, aging_seconds)
    if dialect == "postgresql":
        job = (
            await session.execute(
                select(DurableJob)
                .where(*filters)
                .order_by(*order)
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
            select(DurableJob.id).where(*filters).order_by(*order).limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        return None
    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == candidate,
            DurableJob.status.in_(_CLAIMABLE),
            DurableJob.cancel_requested.is_(False),
            or_(DurableJob.leased_until.is_(None), DurableJob.leased_until < moment),
            *extra_filters,
        )
        .values(
            status="running",
            worker_id=worker_id,
            leased_until=lease_until,
            started_at=moment,
        ),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        return None
    await session.flush()
    job = await session.get(DurableJob, candidate)
    if job is None:
        return None
    if job.status != "running" or job.worker_id != worker_id:
        # The identity map held a stale instance (this row was claimed in an
        # earlier cycle of the same session). The database row is what the
        # compare-and-set just wrote; reload so the returned object agrees.
        await session.refresh(job)
    if job.leased_until is None or job.leased_until.tzinfo is None:
        # SQLite round-trips drop tzinfo. Restore the exact aware value this
        # claim stored, so later in-session comparisons (guarded acks,
        # heartbeats) stay aware-vs-aware. On PostgreSQL the column is
        # timestamptz and this is already true.
        job.leased_until = lease_until
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
        .values(status="queued", worker_id="", leased_until=None, available_at=moment),
        execution_options={"synchronize_session": False},
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
