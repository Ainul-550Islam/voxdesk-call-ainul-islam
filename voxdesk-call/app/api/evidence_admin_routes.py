"""Authenticated tenant-derived evidence aliases."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, to_http

from app.governance import attestation, packages
from app.governance.context import resolve_scope
from app.governance.models import EvidenceEvent
from app.governance.schemas import EvidenceEventOut, EvidencePackageOut, EvidencePackageRequest, EvidenceQuery

router = APIRouter(prefix="/api/governance/evidence", tags=["evidence"])


@router.get("", response_model=list[EvidenceEventOut])
async def list_events(
    query: EvidenceQuery = Depends(),
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, query.environment_id)
        rows = await attestation.current_evidence(session, scope)
        if query.from_sequence is not None:
            rows = [row for row in rows if row.sequence >= query.from_sequence]
        if query.to_sequence is not None:
            rows = [row for row in rows if row.sequence <= query.to_sequence]
        return rows[: query.limit]
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{event_id}", response_model=EvidenceEventOut)
async def get_event(
    event_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await session.scalar(
            select(EvidenceEvent).where(
                EvidenceEvent.id == event_id,
                EvidenceEvent.tenant_id == scope.tenant_id,
                EvidenceEvent.organization_id == scope.organization_id,
            )
        )
        if row is None:
            from app.governance.exceptions import GovernanceNotFound

            raise GovernanceNotFound()
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/verify")
async def verify_events(
    query: EvidenceQuery | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, query.environment_id if query else None)
        rows = await attestation.current_evidence(session, scope)
        return {"valid": True, "event_count": len(rows), "last_sequence": rows[-1].sequence}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/package", response_model=EvidencePackageOut, status_code=201)
async def package_events(
    payload: EvidencePackageRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_EVIDENCE_EXPORT)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, payload.environment_id)
        row = await packages.create_package(
            session,
            scope,
            from_sequence=payload.from_sequence,
            to_sequence=payload.to_sequence,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None
