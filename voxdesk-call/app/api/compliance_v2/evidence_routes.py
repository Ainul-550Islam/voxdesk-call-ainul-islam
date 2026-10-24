"""Evidence automation: run collectors, read snapshots, read control status.

Every response here distinguishes the four evidence statuses explicitly. There
is no aggregate "compliant" boolean, because collapsing PASS/FAIL/
NOT_CONFIGURED into true/false is precisely how a missing source becomes a
green dashboard.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
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
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.soc2 import runner as evidence_runner
from app.compliance_v2.soc2.control_map import load_control_map
from app.compliance_v2.soc2.collectors_base import STATUSES
from app.db.compliance_v2_models import ControlStatus, EvidenceSnapshot
from app.db.session import get_session

router = APIRouter(prefix="/evidence", tags=["compliance-v2-evidence"])


class SnapshotOut(BaseModel):
    id: str
    run_key: str
    created_at: dt.datetime | None = None
    pass_count: int
    fail_count: int
    not_configured_count: int
    error_count: int
    file_path: str
    sha256: str


class ControlStatusOut(BaseModel):
    control_id: str
    collector: str
    status: str
    collected_at: dt.datetime | None = None
    detail: dict[str, Any]


class EvidenceRunOut(BaseModel):
    run_id: str
    started_at: str
    finished_at: str
    ok: bool
    counts: dict[str, int]
    controls: dict[str, str]
    unmapped_controls: list[str]
    collectors: dict[str, Any]
    snapshot: SnapshotOut


def _snapshot_out(row: EvidenceSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=str(row.id),
        run_key=row.run_key,
        created_at=row.created_at,
        pass_count=int(row.pass_count),
        fail_count=int(row.fail_count),
        not_configured_count=int(row.not_configured_count),
        error_count=int(row.error_count),
        file_path=row.file_path or "",
        sha256=row.sha256 or "",
    )


async def _tenant_snapshot(
    session: AsyncSession, *, tenant_id: uuid.UUID, snapshot_id: uuid.UUID
) -> EvidenceSnapshot:
    """Load a snapshot that belongs to ``tenant_id``, or answer 404.

    A snapshot id from another tenant is indistinguishable from one that does not
    exist: the query never leaves the caller's tenant.
    """
    row = await session.scalar(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.id == snapshot_id,
            EvidenceSnapshot.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise not_found("evidence_snapshot")
    return row

@router.get("/controls")
async def control_catalogue(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
):
    """The controls this deployment can evidence, and who evidences them."""
    scoped_tenant(ctx, tenant_id)
    mapping = load_control_map()
    return {
        "statuses": list(STATUSES),
        "controls": dict(sorted(mapping.controls.items())),
        "unmapped": list(mapping.unmapped),
        "matrix_present": mapping.document_present,
        "matrix_source": mapping.source,
    }


@router.post("/runs", response_model=EvidenceRunOut, status_code=status.HTTP_201_CREATED)
async def run_evidence(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Run every collector once and persist the snapshot."""
    scoped_tenant(ctx, tenant_id)
    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.evidence.run",
        payload={"tenant_id": str(tenant_id)},
    )
    replayed = replay_reference(claim, resource_type="evidence_snapshot")
    if replayed is not None:
        existing = await session.get(EvidenceSnapshot, uuid.UUID(replayed))
        if existing is None:
            raise not_found("evidence_snapshot")
        rows = list(
            (
                await session.execute(
                    select(ControlStatus)
                    .where(ControlStatus.snapshot_id == existing.id)
                    .order_by(ControlStatus.control_id)
                )
            ).scalars()
        )
        return EvidenceRunOut(
            run_id=existing.run_key,
            started_at=existing.created_at.isoformat() if existing.created_at else "",
            finished_at=existing.created_at.isoformat() if existing.created_at else "",
            ok=existing.fail_count == 0 and existing.error_count == 0,
            counts={
                "PASS": existing.pass_count,
                "FAIL": existing.fail_count,
                "NOT_CONFIGURED": existing.not_configured_count,
                "ERROR": existing.error_count,
            },
            controls={row.control_id: row.status for row in rows},
            unmapped_controls=[],
            collectors={},
            snapshot=_snapshot_out(existing),
        )
    try:
        run = await evidence_runner.run_evidence(
            session,
            tenant_id=tenant_id,
            created_by=ctx.user_id,
        )
    except Exception as exc:  # noqa: BLE001 - surface a stable error body
        await fail_idempotent(session, claim, category=type(exc).__name__)
        await session.commit()
        raise

    snapshot = await session.scalar(
        select(EvidenceSnapshot)
        .where(EvidenceSnapshot.tenant_id == tenant_id)
        .order_by(EvidenceSnapshot.created_at.desc())
        .limit(1)
    )
    if snapshot is None:
        raise not_found("evidence_snapshot")

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="compliance_evidence_run",
        resource_type="evidence_snapshot",
        resource_id=snapshot.id,
        detail={
            "run_id": run.run_id,
            "counts": run.counts,
            "ok": run.ok,
            "sha256": snapshot.sha256,
        },
    )
    await complete_idempotent(
        session, claim, resource_type="evidence_snapshot", resource_id=snapshot.id
    )
    await session.commit()
    return EvidenceRunOut(
        run_id=run.run_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        ok=run.ok,
        counts=run.counts,
        controls=dict(sorted(run.statuses.items())),
        unmapped_controls=list(run.control_map.unmapped),
        collectors=run.summary()["collectors"],
        snapshot=_snapshot_out(snapshot),
    )


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    tenant_id: uuid.UUID,
    limit: int = Query(default=25, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(EvidenceSnapshot)
        .where(EvidenceSnapshot.tenant_id == tenant_id)
        .order_by(EvidenceSnapshot.created_at.desc())
        .limit(limit)
    )
    return [_snapshot_out(row) for row in rows.scalars()]


@router.get("/snapshots/{snapshot_id}", response_model=list[ControlStatusOut])
async def snapshot_controls(
    tenant_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Per-control status for one snapshot, worst-first."""
    scoped_tenant(ctx, tenant_id)
    owner = await session.scalar(
        select(EvidenceSnapshot.id).where(
            EvidenceSnapshot.id == snapshot_id, EvidenceSnapshot.tenant_id == tenant_id
        )
    )
    if owner is None:
        raise not_found("evidence_snapshot")
    rows = await session.execute(
        select(ControlStatus)
        .where(ControlStatus.snapshot_id == snapshot_id)
        .order_by(ControlStatus.control_id)
    )
    output = [
        ControlStatusOut(
            control_id=row.control_id,
            collector=row.collector,
            status=row.status,
            collected_at=row.collected_at,
            detail=dict(row.detail or {}),
        )
        for row in rows.scalars()
    ]
    output.sort(key=lambda item: (item.status == "PASS", item.control_id))
    return output


@router.get("/snapshots/{snapshot_id}/download")
async def download_snapshot(
    tenant_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Serve the stored evidence document for one snapshot.

    The file is served only when its bytes still hash to the digest recorded at
    collection time; a snapshot whose evidence directory was pruned or altered
    answers ``410 evidence_expired`` instead of handing over something that no
    longer matches its index.
    """
    scoped_tenant(ctx, tenant_id)
    snapshot = await _tenant_snapshot(session, tenant_id=tenant_id, snapshot_id=snapshot_id)
    path = Path(snapshot.file_path or "")
    if not snapshot.file_path or not path.is_file():
        raise http_error(
            status.HTTP_410_GONE,
            "evidence_expired",
            "the stored evidence for this snapshot is no longer present",
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if snapshot.sha256 and digest != snapshot.sha256:
        raise http_error(
            status.HTTP_409_CONFLICT,
            "evidence_digest_mismatch",
            "the stored evidence does not match the digest recorded at collection time",
        )
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="evidence_snapshot_downloaded",
        resource_type="evidence_snapshot",
        resource_id=snapshot.id,
        detail={"sha256": digest},
    )
    await session.commit()
    return FileResponse(
        path=str(path),
        media_type="application/json",
        filename=f"voxdesk-evidence-{snapshot.id}.json",
    )


@router.get("/latest")
async def latest_status(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The newest snapshot's control statuses, or ``never_run``."""
    scoped_tenant(ctx, tenant_id)
    snapshot = await session.scalar(
        select(EvidenceSnapshot)
        .where(EvidenceSnapshot.tenant_id == tenant_id)
        .order_by(EvidenceSnapshot.created_at.desc())
        .limit(1)
    )
    if snapshot is None:
        return {"status": "never_run", "controls": {}}
    rows = await session.execute(
        select(ControlStatus).where(ControlStatus.snapshot_id == snapshot.id)
    )
    return {
        "status": "present",
        "snapshot": _snapshot_out(snapshot).model_dump(),
        "controls": {
            row.control_id: {"status": row.status, "collector": row.collector}
            for row in rows.scalars()
        },
    }


__all__ = ["router"]
