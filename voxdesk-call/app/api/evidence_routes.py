"""Tenant-scoped evidence verification and export API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, require_same_tenant, to_http

from app.governance import attestation, packages
from app.governance.context import resolve_scope
from app.governance.evidence import append_event
from app.governance.models import EvidenceEvent, EvidencePackage
from app.governance.schemas import EvidenceEventOut, EvidencePackageOut, EvidencePackageRequest, EvidenceQuery

router = APIRouter(prefix="/api/tenants/{tenant_id}/governance/evidence", tags=["evidence"])


async def _scope(session, ctx, tenant_id, environment_id=None):
    require_same_tenant(ctx.tenant, tenant_id)
    return await resolve_scope(session, ctx, environment_id)


@router.get("", response_model=list[EvidenceEventOut])
async def list_evidence(
    tenant_id: uuid.UUID,
    query: EvidenceQuery = Depends(),
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, query.environment_id)
        statement = (
            select(EvidenceEvent)
            .where(
                EvidenceEvent.tenant_id == scope.tenant_id,
                EvidenceEvent.organization_id == scope.organization_id,
            )
            .order_by(EvidenceEvent.sequence.asc())
            .limit(query.limit)
        )
        if query.from_sequence is not None:
            statement = statement.where(EvidenceEvent.sequence >= query.from_sequence)
        if query.to_sequence is not None:
            statement = statement.where(EvidenceEvent.sequence <= query.to_sequence)
        if scope.environment_id is not None:
            statement = statement.where(EvidenceEvent.environment_id == scope.environment_id)
        rows = list((await session.execute(statement)).scalars())
        if scope.environment_id is None:
            # Verify the complete tenant chain, not just a caller-selected page.
            rows = await attestation.current_evidence(session, scope)
            if query.from_sequence is not None:
                rows = [row for row in rows if row.sequence >= query.from_sequence]
            if query.to_sequence is not None:
                rows = [row for row in rows if row.sequence <= query.to_sequence]
            rows = rows[: query.limit]
        return rows
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/packages", response_model=EvidencePackageOut, status_code=201)
async def create_evidence_package(
    tenant_id: uuid.UUID,
    payload: EvidencePackageRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_EVIDENCE_EXPORT)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, payload.environment_id)
        row = await packages.create_package(
            session,
            scope,
            from_sequence=payload.from_sequence,
            to_sequence=payload.to_sequence,
            actor_user_id=ctx.user_id,
        )
        await append_event(
            session,
            scope,
            event_type="evidence_package_created",
            payload={"package_id": str(row.id), "package_hash": row.package_hash},
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/packages/{package_id}", response_model=EvidencePackageOut)
async def get_evidence_package(
    tenant_id: uuid.UUID,
    package_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_EVIDENCE_EXPORT)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await session.scalar(
            select(EvidencePackage).where(
                EvidencePackage.id == package_id,
                EvidencePackage.tenant_id == scope.tenant_id,
                EvidencePackage.organization_id == scope.organization_id,
            )
        )
        if row is None:
            from app.governance.exceptions import GovernanceNotFound

            raise GovernanceNotFound()
        packages.verify_package(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None
