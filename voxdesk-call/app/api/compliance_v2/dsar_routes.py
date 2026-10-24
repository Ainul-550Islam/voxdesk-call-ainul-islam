"""Data subject access and erasure requests over HTTP.

Flow, in the order the endpoints are meant to be used:

1. ``POST /dsar/requests`` — intake, which sets the due date from
   ``DSAR_DUE_DAYS`` and records nothing about the subject beyond the matching
   key;
2. ``POST /dsar/requests/{id}/verify`` — a person confirms the requester's
   identity; execution is refused until this has happened;
3. ``POST /dsar/requests/{id}/execute`` — build the bundle (export) or apply the
   erasure, writing a signed ZIP and a per-table ledger;
4. ``GET /dsar/requests/{id}/bundle`` — download the signed artifact;
5. ``GET /dsar/registry`` — show what a request covers, including the check that
   the registry still matches the models.

Legal hold is an explicit switch: while it is on, erasure is refused with the
reason recorded on the request instead of half-executing.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    require_recent_auth_for,
    audit_action,
    claim_idempotent,
    complete_idempotent,
    http_error,
    not_found,
    replay_reference,
    require_human,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.audit_integrity.signing import load_signer
from app.compliance_v2.dsar import service as dsar_service
from app.compliance_v2.dsar.registry import check_registry, registry_document
from app.compliance_v2.settings import get_compliance_v2_settings
from app.db.compliance_v2_models import DsarRequest
from app.db.models import Base
from app.db.session import get_session

router = APIRouter(prefix="/dsar", tags=["compliance-v2-dsar"])


class RequestCreate(BaseModel):
    kind: str
    subject_type: str = "contact"
    subject_key: str = Field(min_length=3, max_length=320)
    note: str = Field(default="", max_length=500)


class VerifyIn(BaseModel):
    evidence: str = Field(default="", max_length=200)


class HoldIn(BaseModel):
    hold: bool
    reason: str = Field(default="", max_length=200)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ExecuteIn(BaseModel):
    confirm_kind: str = Field(description="Echo the request kind to confirm the action")


async def _load(session: AsyncSession, tenant_id: uuid.UUID, request_id: uuid.UUID) -> DsarRequest:
    row = await session.scalar(
        select(DsarRequest).where(
            DsarRequest.id == request_id, DsarRequest.tenant_id == tenant_id
        )
    )
    if row is None:
        raise not_found("dsar_request")
    return row


@router.get("/registry")
async def pii_registry(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_READ)),
):
    """The tables and fields a request covers, plus the live drift check."""
    scoped_tenant(ctx, tenant_id)
    problems = check_registry(Base.metadata)
    return {
        "registry": registry_document(),
        "consistent": not problems,
        "problems": problems,
    }


@router.get("/requests")
async def list_requests(
    tenant_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await dsar_service.list_requests(
        session, tenant_id=tenant_id, status=status_filter, kind=kind, limit=limit
    )
    return [dsar_service.as_dict(row) for row in rows]


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def create_request(
    tenant_id: uuid.UUID,
    payload: RequestCreate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.dsar.create",
        payload={
            "kind": payload.kind,
            "subject_type": payload.subject_type,
            "subject_key": payload.subject_key.strip().lower(),
        },
    )
    replayed = replay_reference(claim, resource_type="dsar_request")
    if replayed is not None:
        existing = await dsar_service.get_request(
            session, tenant_id=tenant_id, request_id=uuid.UUID(replayed)
        )
        return dsar_service.as_dict(existing)
    try:
        row = await dsar_service.create_request(
            session,
            tenant_id=tenant_id,
            kind=payload.kind,
            subject_type=payload.subject_type,
            subject_key=payload.subject_key,
            requested_by=ctx.user_id,
            note=payload.note,
        )
    except dsar_service.DsarServiceError as exc:
        raise http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "dsar_invalid", str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - export.build_subject raises DsarError
        raise http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "dsar_invalid", str(exc)) from exc

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_created",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"kind": row.kind, "subject_type": row.subject_type},
    )
    await complete_idempotent(session, claim, resource_type="dsar_request", resource_id=row.id)
    await session.commit()
    await session.refresh(row)
    return dsar_service.as_dict(row)


@router.get("/requests/{request_id}")
async def get_request(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    row = await _load(session, tenant_id, request_id)
    ledger = await dsar_service.ledger_for(session, tenant_id=tenant_id, request_id=request_id)
    return {
        "request": dsar_service.as_dict(row),
        "ledger": [
            {
                "table": entry.table_name,
                "action": entry.action,
                "rows_affected": entry.rows_affected,
                "detail": dict(entry.detail or {}),
            }
            for entry in ledger
        ],
    }


@router.post("/requests/{request_id}/verify")
async def verify_identity(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: VerifyIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    try:
        row = await dsar_service.mark_verified(
            session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_user_id=ctx.user_id,
            evidence=payload.evidence,
        )
    except dsar_service.RequestNotFound:
        raise not_found("dsar_request") from None
    except dsar_service.InvalidTransition as exc:
        raise http_error(status.HTTP_409_CONFLICT, "dsar_invalid_transition", str(exc)) from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_verified",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"evidence": payload.evidence[:100]},
    )
    await session.commit()
    await session.refresh(row)
    return dsar_service.as_dict(row)


@router.post("/requests/{request_id}/legal-hold")
async def set_legal_hold(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: HoldIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    try:
        row = await dsar_service.set_legal_hold(
            session,
            tenant_id=tenant_id,
            request_id=request_id,
            hold=payload.hold,
            actor_user_id=ctx.user_id,
            reason=payload.reason,
        )
    except dsar_service.RequestNotFound:
        raise not_found("dsar_request") from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_legal_hold",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"hold": payload.hold, "reason": payload.reason[:100]},
    )
    await session.commit()
    await session.refresh(row)
    return dsar_service.as_dict(row)


@router.post("/requests/{request_id}/execute")
async def execute_request(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: ExecuteIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
    _reauth: None = Depends(require_recent_auth_for("dsar.erasure")),
):
    """Run the request. An erasure must be confirmed by echoing its kind."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    row = await _load(session, tenant_id, request_id)
    if payload.confirm_kind.strip().lower() != row.kind:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "confirmation_mismatch",
            f"confirm_kind must echo the request kind ({row.kind!r})",
        )

    try:
        result = await dsar_service.execute(
            session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_user_id=ctx.user_id,
            role=ctx.role,
            settings=get_compliance_v2_settings(),
            signer=load_signer(settings=get_compliance_v2_settings()),
        )
    except dsar_service.IdentityNotVerified as exc:
        raise http_error(status.HTTP_409_CONFLICT, "identity_not_verified", str(exc)) from None
    except dsar_service.RequestNotFound:
        raise not_found("dsar_request") from None
    except dsar_service.DsarServiceError as exc:
        raise http_error(status.HTTP_400_BAD_REQUEST, "dsar_error", str(exc)) from None

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_executed",
        resource_type="dsar_request",
        resource_id=request_id,
        detail={
            "kind": row.kind,
            "bundle_id": result.bundle_id,
            "bundle_sha256": result.bundle_sha256,
            "counts": result.counts,
            "blocked_by_legal_hold": not result.bundle_id and row.legal_hold,
        },
    )
    await session.commit()
    return result.as_dict()


@router.get("/requests/{request_id}/bundle")
async def download_bundle(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Serve the signed bundle for a completed request."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    row = await _load(session, tenant_id, request_id)
    if row.status != dsar_service.STATUS_COMPLETED:
        raise http_error(
            status.HTTP_409_CONFLICT,
            "bundle_not_ready",
            f"the request is in state {row.status!r}; execute it first",
        )
    path = Path(row.bundle_path or "")
    if not path.is_file():
        raise http_error(
            status.HTTP_410_GONE, "bundle_missing", "the stored bundle is no longer present"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if row.bundle_sha256 and digest != row.bundle_sha256:
        # A bundle that no longer matches its recorded digest is served as a
        # failure, never as the artifact.
        raise http_error(
            status.HTTP_409_CONFLICT,
            "bundle_digest_mismatch",
            "the stored bundle no longer matches the digest recorded at creation",
        )
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_bundle_downloaded",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"bundle_id": row.bundle_id, "sha256": digest},
    )
    await session.commit()
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=f"{row.bundle_id or row.id}.zip",
    )


@router.post("/requests/{request_id}/reject")
async def reject_request(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: RejectIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    try:
        row = await dsar_service.reject(
            session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_user_id=ctx.user_id,
            reason=payload.reason,
        )
    except dsar_service.RequestNotFound:
        raise not_found("dsar_request") from None
    except dsar_service.InvalidTransition as exc:
        raise http_error(status.HTTP_409_CONFLICT, "dsar_invalid_transition", str(exc)) from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_rejected",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"reason": payload.reason[:200]},
    )
    await session.commit()
    await session.refresh(row)
    return dsar_service.as_dict(row)


@router.post("/requests/tick")
async def run_tick(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Flag overdue requests now, outside the scheduler cadence."""
    scoped_tenant(ctx, tenant_id)
    outcome = await dsar_service.tick(session, tenant_id=tenant_id)
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="dsar_tick",
        resource_type="dsar_request",
        detail=dict(outcome),
    )
    await session.commit()
    return outcome


@router.get("/requests/{request_id}/verify-bundle")
async def verify_bundle(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_DSAR_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Re-verify a stored bundle: member hashes and the manifest signature."""
    scoped_tenant(ctx, tenant_id)
    row = await _load(session, tenant_id, request_id)
    if not row.bundle_path:
        raise http_error(
            status.HTTP_409_CONFLICT, "bundle_not_ready", "this request has no bundle"
        )
    cfg = get_compliance_v2_settings()
    signer = load_signer(settings=cfg)
    return dsar_service.verify_bundle(row.bundle_path, public_key_pem=signer.public_key_pem)


__all__ = ["router"]
