"""Authenticated tenant-derived risk aliases."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, to_http

from app.governance import service
from app.governance.context import resolve_scope
from app.governance.models import RiskAssessment
from app.governance.schemas import RiskAssessmentCreate, RiskAssessmentOut

router = APIRouter(prefix="/api/governance/risk", tags=["risk"])


@router.get("", response_model=list[RiskAssessmentOut])
async def list_risk(
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        result = await session.execute(
            select(RiskAssessment)
            .where(
                RiskAssessment.tenant_id == scope.tenant_id,
                RiskAssessment.organization_id == scope.organization_id,
            )
            .order_by(RiskAssessment.assessed_at.desc())
        )
        return list(result.scalars())
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/assess", response_model=RiskAssessmentOut, status_code=201)
async def assess_risk(
    payload: RiskAssessmentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, payload.environment_id)
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


@router.post("/{assessment_id}/approve", response_model=RiskAssessmentOut)
async def approve_risk(
    assessment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await service.approve_risk(session, scope, assessment_id, actor_user_id=ctx.user_id)
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None
