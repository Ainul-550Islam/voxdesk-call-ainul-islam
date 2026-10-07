"""Authorized operator APIs for the durable job platform (Batch 07).

Inspection is ``AUDIT_READ`` (admin/owner — job rows are operational audit
data). Mutation is ``CAMPAIGN_RUN``, exactly the permission the existing
automation-DLQ replay (``app.automation.dlq.operator_may_replay``) and
webhook replay already require, so one operator vocabulary governs every
replay surface in the product.

Identity rules (unchanged from the rest of the API): ``tenant_id`` comes
from the authenticated ``TenantContext`` and never from the client; another
tenant's job does not exist (404, no detail); replay accepts no new scope —
the row keeps its persisted tenant and environment. Request bodies are
strict (``extra="forbid"``): there is no field through which a client could
name a handler, a module, a worker or a tenant.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import DurableJob
from app.db.session import get_session
from app.jobs import dead_letter, queue, replay
from app.jobs.concurrency import DEFAULT_LIMITS, running_count
from app.jobs.scheduler import count_due
from app.tenancy.isolation import BoundaryDenied, HierarchyError, to_http

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobOut(_Strict):
    id: uuid.UUID
    organization_id: uuid.UUID | None
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None
    job_type: str
    status: str
    priority: int
    attempt_count: int
    max_attempts: int
    replay_count: int
    cancel_requested: bool
    worker_id: str
    idempotency_key: str
    payload: dict
    available_at: object
    leased_until: object | None
    created_at: object
    started_at: object | None
    completed_at: object | None
    last_error_category: str
    last_error: str


class AttemptOut(_Strict):
    attempt_number: int
    worker_id: str
    status: str
    error_category: str
    started_at: object
    finished_at: object | None


class JobDetailOut(JobOut):
    attempts: list[AttemptOut] = []


def _job_out(job: DurableJob) -> JobOut:
    return JobOut.model_validate(job, from_attributes=True)


@router.get("")
async def list_jobs(
    ctx: TenantContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None, max_length=32),
    job_type: str | None = Query(None, max_length=64),
    environment_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Tenant-scoped job listing, newest first. Read-only."""
    jobs = await queue.list_jobs(
        session,
        tenant_id=ctx.tenant_id,
        status=status,
        job_type=job_type,
        environment_id=environment_id,
        limit=limit,
        offset=offset,
    )
    return {"jobs": [_job_out(job).model_dump(mode="json") for job in jobs], "count": len(jobs)}


@router.get("/dlq")
async def list_dlq(
    ctx: TenantContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None, pattern="^(dead_letter|quarantined)$"),
    job_type: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """The tenant's dead-letter/quarantine rows — poison jobs parked safely."""
    statuses = (status,) if status else dead_letter.DLQ_STATUSES
    jobs = await dead_letter.list_dead_letters(
        session,
        tenant_id=ctx.tenant_id,
        statuses=statuses,
        job_type=job_type,
        limit=limit,
        offset=offset,
    )
    return {"jobs": [_job_out(job).model_dump(mode="json") for job in jobs], "count": len(jobs)}


@router.get("/metrics")
async def job_metrics(
    ctx: TenantContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Queue health for this tenant, counted from the table (never memory)."""
    rows = (
        await session.execute(
            select(DurableJob.status, func.count())
            .where(DurableJob.tenant_id == ctx.tenant_id)
            .group_by(DurableJob.status)
        )
    ).all()
    by_status = {str(status): int(count) for status, count in rows}
    oldest = (
        await session.execute(
            select(func.min(DurableJob.available_at)).where(
                DurableJob.tenant_id == ctx.tenant_id,
                DurableJob.status.in_(("queued", "retry_scheduled")),
            )
        )
    ).scalar_one_or_none()
    return {
        "by_status": by_status,
        "running": await running_count(session, tenant_id=ctx.tenant_id),
        "dead_letter": by_status.get("dead_letter", 0) + by_status.get("quarantined", 0),
        "oldest_waiting_since": oldest.isoformat() if oldest is not None else None,
        "due_now": await count_due(session, tenant_id=ctx.tenant_id),
        "limits": {
            "global": DEFAULT_LIMITS.global_limit,
            "tenant": DEFAULT_LIMITS.tenant_limit,
            "type": dict(DEFAULT_LIMITS.type_limits),
        },
    }


@router.get("/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """One job with its full attempt provenance. 404 across the tenant line."""
    inspected = await dead_letter.inspect_job(session, tenant_id=ctx.tenant_id, job_id=job_id)
    if inspected is None:
        raise to_http(BoundaryDenied())
    job, attempts = inspected
    detail = JobDetailOut.model_validate(job, from_attributes=True)
    return {
        "job": detail.model_dump(mode="json"),
        "attempts": [
            AttemptOut.model_validate(row, from_attributes=True).model_dump(mode="json")
            for row in attempts
        ],
    }


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Durable cancellation: immediate for claimable rows, flag for running.

    A running job is never yanked mid-handler: ``cancel_requested`` is
    persisted now, the worker's guarded ack lets the cancel beat a late
    success, and the reaper finalises it if the worker dies first.
    """
    try:
        outcome = await queue.cancel(session, tenant_id=ctx.tenant_id, job_id=job_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    if outcome is None:
        raise to_http(BoundaryDenied())
    await session.commit()
    return {"job_id": str(job_id), "status": outcome}


@router.post("/{job_id}/retry")
async def retry_job(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Resume a cancelled job or run a retry-scheduled one now.

    Dead-letter rows must use replay (budget + provenance); running or
    already-queued rows are conflicts, not retries.
    """
    existing = await queue.get(session, job_id, tenant_id=ctx.tenant_id)
    if existing is None:
        raise to_http(BoundaryDenied())
    try:
        outcome = await queue.retry_job(session, tenant_id=ctx.tenant_id, job_id=job_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    await session.commit()
    return {"job_id": str(job_id), "status": outcome}


@router.post("/{job_id}/replay")
async def replay_job(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Operator replay of one DLQ row: guarded, budgeted, scope-preserving."""
    try:
        job = await replay.replay_job(
            session, tenant_id=ctx.tenant_id, job_id=job_id, role=ctx.user.role
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    await session.commit()
    return {
        "job_id": str(job.id),
        "status": job.status,
        "replay_count": job.replay_count,
        "max_attempts": job.max_attempts,
    }


@router.post("/{job_id}/quarantine")
async def quarantine_job(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Operator hold on a dead-letter job (stays out of replay until released)."""
    try:
        job = await dead_letter.quarantine_job(session, tenant_id=ctx.tenant_id, job_id=job_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    await session.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.post("/{job_id}/release")
async def release_job(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Release a quarantined job back to dead-letter (replayable again)."""
    try:
        job = await dead_letter.release_job(session, tenant_id=ctx.tenant_id, job_id=job_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    await session.commit()
    return {"job_id": str(job.id), "status": job.status}
