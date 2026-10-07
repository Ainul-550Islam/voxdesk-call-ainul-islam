"""Tenant-scoped, versioned risk assessment API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, require_same_tenant, to_http

from app.governance import service
from app.governance.context import resolve_scope
from app.governance.models import RiskAssessment
from app.governance.schemas import RiskAssessmentCreate, RiskAssessmentOut

router = APIRouter(prefix="/api/tenants/{tenant_id}/governance/risks", tags=["risk"])


async def _scope(session, ctx, tenant_id, environment_id=None):
    require_same_tenant(ctx.tenant, tenant_id)
    return await resolve_scope(session, ctx, environment_id)


@router.get("", response_model=list[RiskAssessmentOut])
async def list_risks(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        return list(
            (
                await session.execute(
                    select(RiskAssessment)
                    .where(
                        RiskAssessment.tenant_id == scope.tenant_id,
                        RiskAssessment.organization_id == scope.organization_id,
                    )
                    .order_by(RiskAssessment.assessed_at.desc())
                )
            ).scalars()
        )
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("", response_model=RiskAssessmentOut, status_code=201)
async def create_risk(
    tenant_id: uuid.UUID,
    payload: RiskAssessmentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, payload.environment_id)
        row = await service.create_risk_assessment(
            session,
            scope,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            tier=payload.tier or payload.risk_tier or "low",
            factors=payload.factors,
            rationale=payload.rationale,
            actor_user_id=ctx.user_id,
            explicit_required_controls=payload.required_controls,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/{risk_id}/approve", response_model=RiskAssessmentOut)
async def approve_risk(
    tenant_id: uuid.UUID,
    risk_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.approve_risk(session, scope, risk_id, actor_user_id=ctx.user_id)
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None
