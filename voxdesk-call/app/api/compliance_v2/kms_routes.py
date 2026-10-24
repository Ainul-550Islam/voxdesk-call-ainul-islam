"""Envelope-encryption key management: health, rekey plans, rekey runs.

The rekey endpoint is deliberately two-phase. A POST with ``dry_run=true``
(default) reports exactly how many values would be rewritten, without writing
ciphertext or a checkpoint; a second POST with ``dry_run=false`` performs the
work. That gives an operator a chance to see the blast radius before a job
touches stored credentials, which is the difference between a migration and an
outage.

Jobs are bounded by ``max_rows`` so one request cannot run for an unbounded
time; a partial run leaves a resumable checkpoint on the job row.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    require_recent_auth_for,
    audit_action,
    claim_idempotent,
    complete_idempotent,
    fail_idempotent,
    http_error,
    not_found,
    replay_reference,
    require_human,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.kms import algorithms as kms_algorithms
from app.compliance_v2.kms import get_kms, provider_name
from app.compliance_v2.kms.rekey_job import (
    TARGETS,
    create_job,
    job_status,
    run_rekey,
    select_targets,
)
from app.compliance_v2.settings import get_compliance_v2_settings
from app.db.compliance_v2_models import KmsRekeyJob
from app.db.session import get_session

router = APIRouter(prefix="/kms", tags=["compliance-v2-kms"])

MAX_ROWS_PER_RUN = 5000


class RekeyIn(BaseModel):
    dry_run: bool = True
    targets: list[str] = Field(
        default_factory=list,
        description="table.column keys; empty means every registered target",
    )
    max_rows: int = Field(default=500, ge=1, le=MAX_ROWS_PER_RUN)
    batch_size: int = Field(default=100, ge=1, le=1000)


class JobOut(BaseModel):
    id: str
    status: str
    dry_run: bool
    target_tables: list[str]
    target_key_id: str
    rows_scanned: int
    rows_rekeyed: int
    rows_skipped: int
    rows_failed: int
    cursor_table: str
    cursor_id: str
    error_message: str
    created_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


def _job_out(row: KmsRekeyJob) -> JobOut:
    return JobOut(
        id=str(row.id),
        status=row.status,
        dry_run=bool(row.dry_run),
        target_tables=list(row.target_tables or []),
        target_key_id=row.target_key_id or "",
        rows_scanned=int(row.rows_scanned),
        rows_rekeyed=int(row.rows_rekeyed),
        rows_skipped=int(row.rows_skipped),
        rows_failed=int(row.rows_failed),
        cursor_table=row.cursor_table or "",
        cursor_id=row.cursor_id or "",
        error_message=row.error_message or "",
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


@router.get("/status")
@router.get("/health")
async def kms_health(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
):
    """Report whether the configured key is usable, and which provider it is."""
    scoped_tenant(ctx, tenant_id)
    cfg = get_compliance_v2_settings()
    try:
        health = await get_kms(cfg).health()
    except Exception as exc:  # noqa: BLE001 - an unusable KMS is the answer
        return {
            "provider": provider_name(cfg),
            "ok": False,
            "detail": {"error": f"{type(exc).__name__}: {exc}"[:300]},
            "development_provider": cfg.kms_provider == "local_dev",
            "algorithm_policy": kms_algorithms.status(),
        }
    return {
        "provider": provider_name(cfg),
        "ok": bool(health.ok),
        "key_id": health.key_id,
        "detail": dict(health.detail or {}),
        "development_provider": cfg.kms_provider == "local_dev",
        "algorithm_policy": kms_algorithms.status(),
    }


@router.get("/targets")
async def rekey_targets(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
):
    """The sealed-value columns this deployment knows how to re-encrypt."""
    scoped_tenant(ctx, tenant_id)
    return {
        "targets": [target.as_dict() for target in TARGETS],
        "provider": provider_name(),
    }


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    tenant_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(KmsRekeyJob)
        .where((KmsRekeyJob.tenant_id == tenant_id) | (KmsRekeyJob.tenant_id.is_(None)))
        .order_by(KmsRekeyJob.created_at.desc())
        .limit(limit)
    )
    return [_job_out(row) for row in rows.scalars()]


@router.post("/rekey", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def start_rekey(
    tenant_id: uuid.UUID,
    payload: RekeyIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_KMS_REKEY)),
    session: AsyncSession = Depends(get_session),
    _reauth: None = Depends(require_recent_auth_for("kms.rekey")),
):
    """Plan (dry run) or perform a re-encryption sweep for this tenant."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    try:
        targets = select_targets(payload.targets or None)
    except ValueError as exc:
        raise http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_target", str(exc)) from None

    cfg = get_compliance_v2_settings()
    provider = get_kms(cfg)
    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.kms.rekey",
        payload={
            "dry_run": payload.dry_run,
            "targets": [target.key for target in targets],
            "max_rows": payload.max_rows,
        },
    )
    replayed = replay_reference(claim, resource_type="kms_rekey_job")
    if replayed is not None:
        existing = await job_status(session, uuid.UUID(replayed))
        if existing is None:
            raise not_found("kms_rekey_job")
        return _job_out(existing)
    job = await create_job(
        session,
        tenant_id=tenant_id,
        requested_by=ctx.user_id,
        dry_run=payload.dry_run,
        target_keys=[target.key for target in targets],
        provider=provider,
    )
    try:
        await run_rekey(
            session,
            provider,
            job=job,
            batch_size=payload.batch_size,
            max_rows=payload.max_rows,
            settings=cfg,
        )
    except Exception as exc:  # noqa: BLE001 - the job row carries the failure
        await fail_idempotent(session, claim, category=type(exc).__name__)
        await audit_action(
            session,
            ctx=ctx,
            request=request,
            event_type="kms_rekey_failed",
            resource_type="kms_rekey_job",
            resource_id=job.id,
            result="failure",
            detail={"error": type(exc).__name__},
        )
        await session.commit()
        raise http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "rekey_failed", row_message(job)
        ) from exc

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="kms_rekey_completed",
        resource_type="kms_rekey_job",
        resource_id=job.id,
        detail={
            "dry_run": job.dry_run,
            "rows_scanned": job.rows_scanned,
            "rows_rekeyed": job.rows_rekeyed,
            "rows_skipped": job.rows_skipped,
            "rows_failed": job.rows_failed,
            "target_key_id": job.target_key_id,
        },
    )
    await complete_idempotent(session, claim, resource_type="kms_rekey_job", resource_id=job.id)
    await session.commit()
    await session.refresh(job)
    return _job_out(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    row = await job_status(session, job_id)
    if row is None or (row.tenant_id not in (None, tenant_id)):
        raise not_found("kms_rekey_job")
    return _job_out(row)


def row_message(job: KmsRekeyJob) -> str:
    return job.error_message or "rekey failed"


__all__ = ["router"]
