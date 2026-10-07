"""Tenant-derived governance administration aliases.

The tenant path APIs remain available for explicit resource URLs. These aliases
use the authenticated TenantContext and never accept a tenant id from the
request body or query string.
"""

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
from app.governance.models import GovernancePolicy
from app.governance.schemas import PolicyCreate, PolicyDecisionOut, PolicyDecisionRequest, PolicyOut

router = APIRouter(prefix="/api/governance", tags=["governance"])


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        return await service.list_policies(session, scope)
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/policies/{policy_id}", response_model=PolicyOut)
async def get_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await session.scalar(
            select(GovernancePolicy).where(
                GovernancePolicy.id == policy_id,
                GovernancePolicy.tenant_id == scope.tenant_id,
                GovernancePolicy.organization_id == scope.organization_id,
            )
        )
        if row is None:
            from app.governance.exceptions import GovernanceNotFound

            raise GovernanceNotFound()
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy(
    payload: PolicyCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, payload.environment_id)
        if payload.organization_id is not None and payload.organization_id != scope.organization_id:
            from app.tenancy.isolation import BoundaryDenied

            raise BoundaryDenied()
        row = await service.create_policy(
            session,
            scope,
            name=payload.name,
            policy_type=payload.policy_type,
            rules=payload.rules,
            rationale=payload.rationale,
            actor_user_id=ctx.user_id,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/policies/{policy_id}/publish", response_model=PolicyOut)
async def publish_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await service.publish_policy(session, scope, policy_id, actor_user_id=ctx.user_id)
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/policies/{policy_id}/retire", response_model=PolicyOut)
async def retire_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await service.retire_policy(session, scope, policy_id, actor_user_id=ctx.user_id)
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/evaluate", response_model=PolicyDecisionOut)
async def evaluate_policy(
    payload: PolicyDecisionRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, payload.environment_id)
        row = await service.decide(
            session,
            scope,
            policy_type=payload.policy_type,
            input_payload=payload.input_payload,
            principal_id=ctx.user_id,
            correlation_id=payload.correlation_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/posture")
async def governance_posture(
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = await resolve_scope(session, ctx)
    return {
        "tenant_id": str(scope.tenant_id),
        "organization_id": str(scope.organization_id),
        "environment_id": None,
        "governance_configured": True,
        "physical_residency_proven": False,
    }
