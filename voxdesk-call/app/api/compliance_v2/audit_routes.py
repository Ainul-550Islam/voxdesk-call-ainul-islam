"""Chain verification and signed audit exports.

Three groups of endpoints:

* ``GET /audit/verify`` — walk the tamper-evident chain and report every break;
* ``GET /audit/exports``, ``POST /audit/exports``, ``GET /audit/exports/{id}/download``
  — build, list and fetch signed export bundles;
* ``GET /audit/signing-key`` — the published public key and rotation history, so
  a customer can verify a bundle they were sent years ago;
* ``POST /audit/verify-bundle`` — the same verification a customer runs offline,
  against an uploaded bundle, so a support engineer does not need the file.

An export is written once and never rewritten: a second POST for a new range
creates a second bundle, and an export whose file is missing reports ``failed``
rather than serving something else in its place.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
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
from app.compliance_v2.audit_integrity import chain_verifier, export_bundle, signing
from app.compliance_v2.settings import get_compliance_v2_settings
from app.db.compliance_v2_models import AuditExport
from app.db.session import get_session

router = APIRouter(prefix="/audit", tags=["compliance-v2-audit"])

#: Upper bound on one export range, so a request cannot ask for the whole chain
#: in one go (which would be a memory problem before it was a policy problem).
MAX_RANGE_DAYS = 366

#: Upload cap for a bundle under verification. A year of one tenant's audit
#: events compresses to a few megabytes; 64 MiB leaves room without letting a
#: single request pin the worker.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class ChainIssueOut(BaseModel):
    kind: str
    sequence: int
    detail: str


class ChainReportOut(BaseModel):
    tenant_id: str
    chain_scope: str
    verified: bool
    checked: int
    head_sequence: int | None
    head_hash: str
    first_broken_sequence: int | None
    issues: list[ChainIssueOut]
    started_at: str
    finished_at: str


class ExportCreate(BaseModel):
    range_start: dt.datetime
    range_end: dt.datetime
    note: str = Field(default="", max_length=500)


class ExportOut(BaseModel):
    id: str
    status: str
    range_start: dt.datetime
    range_end: dt.datetime
    event_count: int
    chain_head: str
    bundle_sha256: str
    file_present: bool
    signing_key_id: str
    error_message: str
    created_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None


class SigningKeyOut(BaseModel):
    algorithm: str
    key_id: str
    public_key_pem: str
    rotations: list[dict[str, Any]]


def _report_out(report: chain_verifier.ChainVerificationReport) -> ChainReportOut:
    return ChainReportOut(
        tenant_id=report.tenant_id,
        chain_scope=report.chain_scope,
        verified=report.ok,
        checked=report.checked,
        head_sequence=report.head_sequence,
        head_hash=report.head_hash,
        first_broken_sequence=report.first_broken_sequence,
        issues=[
            ChainIssueOut(kind=issue.kind, sequence=issue.sequence, detail=issue.detail)
            for issue in report.issues
        ],
        started_at=report.started_at,
        finished_at=report.finished_at,
    )


def _export_out(row: AuditExport) -> ExportOut:
    path = str(row.bundle_path or "")
    return ExportOut(
        id=str(row.id),
        status=row.status,
        range_start=row.range_start,
        range_end=row.range_end,
        event_count=int(row.event_count or 0),
        chain_head=row.chain_head or "",
        bundle_sha256=str((row.manifest or {}).get("bundle_sha256", "")),
        file_present=bool(path) and Path(path).exists(),
        signing_key_id=row.signing_key_id or "",
        error_message=row.error_message or "",
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


@router.get("/verify", response_model=ChainReportOut)
async def verify_chain(
    tenant_id: uuid.UUID,
    allow_empty: bool = Query(default=False, description="Treat an empty chain as verified"),
    max_issues: int = Query(default=100, ge=1, le=1000),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_AUDIT_VERIFY)),
    session: AsyncSession = Depends(get_session),
):
    """Verify the tenant's evidence chain end to end."""
    scoped_tenant(ctx, tenant_id)
    report = await chain_verifier.verify_chain(
        session,
        tenant_id=tenant_id,
        max_issues=max_issues,
        allow_empty=allow_empty,
    )
    return _report_out(report)


class BundleVerificationOut(BaseModel):
    ok: bool
    reason: str
    event_count: int
    head_hash: str
    key_id: str
    chain_ok: bool
    detail: dict[str, Any] = Field(default_factory=dict)


@router.post("/verify-bundle", response_model=BundleVerificationOut)
async def verify_uploaded_bundle(
    tenant_id: uuid.UUID,
    request: Request,
    bundle: UploadFile = File(..., description="a ZIP produced by POST /audit/exports"),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_AUDIT_VERIFY)),
    session: AsyncSession = Depends(get_session),
):
    """Verify an uploaded export bundle: hashes, signature and chain links.

    The upload is capped at ``MAX_UPLOAD_BYTES`` and never written to disk — a
    bundle is verified from memory and discarded. A verification failure is a
    normal answer (``ok: false`` with a reason), not a 4xx: the caller is asking
    a question, and "this bundle is broken" is the answer.
    """
    scoped_tenant(ctx, tenant_id)
    cfg = get_compliance_v2_settings()
    payload = await bundle.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise http_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "bundle_too_large",
            f"the bundle exceeds {MAX_UPLOAD_BYTES} bytes",
        )
    signer = signing.load_signer(settings=cfg)
    result = export_bundle.verify_bundle_bytes(
        payload, public_key_pem=signer.public_key_pem
    )
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="audit_bundle_verified",
        resource_type="audit_export",
        resource_id=None,
        detail={
            "ok": result.ok,
            "reason": result.reason,
            "bytes": len(payload),
            "filename": (bundle.filename or "")[:200],
        },
    )
    await session.commit()
    return BundleVerificationOut(
        ok=result.ok,
        reason=result.reason,
        event_count=result.event_count,
        head_hash=result.head_hash,
        key_id=result.key_id,
        chain_ok=result.chain_ok,
        detail=dict(result.detail),
    )


@router.get("/signing-key", response_model=SigningKeyOut)
async def signing_key(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_AUDIT_VERIFY)),
):
    """Publish the key a customer needs to verify their exports offline."""
    scoped_tenant(ctx, tenant_id)
    cfg = get_compliance_v2_settings()
    published = signing.published_public_key(cfg)
    return SigningKeyOut(
        algorithm=published["algorithm"],
        key_id=published["key_id"],
        public_key_pem=published["public_key_pem"],
        rotations=signing.rotation_history(settings=cfg),
    )


@router.post("/signing-key/rotate")
async def rotate_signing_key(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_AUDIT_EXPORT)),
    session: AsyncSession = Depends(get_session),
):
    """Rotate the export signing key, archiving the previous one.

    Owner-only in practice (the permission is not in the admin bundle) because
    rotation invalidates nothing but does change what a customer must trust.
    """
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    entry = signing.rotate_signing_key(settings=get_compliance_v2_settings())
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="audit_signing_key_rotated",
        resource_type="audit_signing_key",
        resource_id=entry.get("new_key_id", ""),
        detail={
            "previous_key_id": entry.get("previous_key_id", ""),
            "rotated_at": entry.get("rotated_at", ""),
        },
    )
    await session.commit()
    return entry


@router.get("/exports", response_model=list[ExportOut])
async def list_exports(
    tenant_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(AuditExport)
        .where(AuditExport.tenant_id == tenant_id)
        .order_by(AuditExport.created_at.desc())
        .limit(limit)
    )
    return [_export_out(row) for row in rows.scalars()]


@router.post("/exports", response_model=ExportOut, status_code=status.HTTP_201_CREATED)
async def create_export(
    tenant_id: uuid.UUID,
    payload: ExportCreate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_AUDIT_EXPORT)),
    session: AsyncSession = Depends(get_session),
):
    """Build a signed export bundle over a time range and store it."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    start, end = payload.range_start, payload.range_end
    if end <= start:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_range",
            "range_end must be after range_start",
        )
    if (end - start) > dt.timedelta(days=MAX_RANGE_DAYS):
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "range_too_long",
            f"an export range may not exceed {MAX_RANGE_DAYS} days",
        )

    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.audit_export.create",
        payload={"range_start": start.isoformat(), "range_end": end.isoformat()},
    )
    replayed = replay_reference(claim, resource_type="audit_export")
    if replayed is not None:
        existing = await session.get(AuditExport, uuid.UUID(replayed))
        if existing is None:
            raise not_found("audit_export")
        return _export_out(existing)
    row = AuditExport(
        tenant_id=tenant_id,
        status="running",
        range_start=start,
        range_end=end,
        requested_by=ctx.user_id,
        manifest={"note": payload.note},
    )
    session.add(row)
    await session.flush()

    try:
        report = await chain_verifier.verify_chain(session, tenant_id=tenant_id, max_issues=50)
        rows = [
            event
            async for event in chain_verifier.iter_chain(session, tenant_id=tenant_id)
            if _in_range(event.occurred_at, start, end)
        ]
        cfg = get_compliance_v2_settings()
        signer = signing.load_signer(settings=cfg)
        bundle = export_bundle.build_bundle(
            request=export_bundle.BundleRequest(
                tenant_id=tenant_id,
                range_start=start,
                range_end=end,
                export_id=str(row.id),
                requested_by=str(ctx.user_id),
                chain_scope=report.chain_scope,
            ),
            events=rows,
            signer=signer,
            chain_report=report.as_dict(),
        )
        directory = cfg.evidence_path / "audit-exports" / str(tenant_id)
        target = directory / f"{row.id}.zip"
        written = export_bundle.write_bundle(target, bundle)
    except Exception as exc:  # noqa: BLE001 - the failure is recorded on the row
        row.status = "failed"
        row.error_message = f"{type(exc).__name__}: {exc}"[:500]
        await fail_idempotent(session, claim, category=type(exc).__name__)
        await audit_action(
            session,
            ctx=ctx,
            request=request,
            event_type="audit_export_failed",
            resource_type="audit_export",
            resource_id=row.id,
            result="failure",
            detail={"error": type(exc).__name__},
        )
        await session.commit()
        raise http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "audit_export_failed", row.error_message
        ) from exc

    row.status = "succeeded"
    row.event_count = len(rows)
    row.chain_head = report.head_hash
    row.bundle_path = written["path"]
    row.signing_key_id = signer.key_id
    row.completed_at = dt.datetime.now(dt.timezone.utc)
    row.manifest = {
        "note": payload.note,
        "bundle_sha256": written["sha256"],
        "chain_verified": report.ok,
        "chain_issue_count": len(report.issues),
        "first_sequence": (rows[0].sequence if rows else None),
        "last_sequence": (rows[-1].sequence if rows else None),
    }
    await session.flush()
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="audit_export_created",
        resource_type="audit_export",
        resource_id=row.id,
        detail={
            "event_count": row.event_count,
            "bundle_sha256": written["sha256"],
            "chain_verified": report.ok,
            "signing_key_id": signer.key_id,
        },
    )
    await complete_idempotent(session, claim, resource_type="audit_export", resource_id=row.id)
    await session.commit()
    await session.refresh(row)
    return _export_out(row)


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(
    tenant_id: uuid.UUID,
    export_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """One export, including its status and bundle digest.

    A bundle that is still being built answers here with ``queued``/``building``
    rather than 404, so a caller can poll one URL and learn why there is no file
    yet instead of guessing.
    """
    scoped_tenant(ctx, tenant_id)
    row = await session.scalar(
        select(AuditExport).where(
            AuditExport.id == export_id, AuditExport.tenant_id == tenant_id
        )
    )
    if row is None:
        raise not_found("audit_export")
    return _export_out(row)


@router.get("/exports/{export_id}/download")
async def download_export(
    tenant_id: uuid.UUID,
    export_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_AUDIT_EXPORT)),
    session: AsyncSession = Depends(get_session),
):
    """Serve a previously built bundle, byte for byte."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    row = await session.scalar(
        select(AuditExport).where(
            AuditExport.id == export_id, AuditExport.tenant_id == tenant_id
        )
    )
    if row is None:
        raise not_found("audit_export")
    path = Path(str(row.bundle_path or ""))
    if not path.is_file():
        raise http_error(
            status.HTTP_410_GONE,
            "bundle_missing",
            "the stored bundle for this export is no longer present",
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="audit_export_downloaded",
        resource_type="audit_export",
        resource_id=row.id,
        detail={"bundle_sha256": digest},
    )
    await session.commit()
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=f"voxdesk-audit-{row.id}.zip",
    )


def _in_range(moment: dt.datetime | None, start: dt.datetime, end: dt.datetime) -> bool:
    if moment is None:
        return False
    value = moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)
    return start <= value <= end


__all__ = ["router"]
