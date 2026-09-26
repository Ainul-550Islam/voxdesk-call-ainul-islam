"""Environment administration for the caller's own tenant.

The path tenant id is checked against the principal. A body cannot move an
environment to another tenant: ``tenant_id`` is not a field on any schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    TenantContext,
    require_human_session,
    require_permission,
)
from app.auth.permissions import Permission
from app.db.session import get_session
from app.environments import service as environment_service
from app.organization.service import Actor
from app.tenancy.isolation import HierarchyError, client_ip, require_same_tenant, to_http
from app.tenancy.service import organization_for

router = APIRouter(prefix="/api/tenants/{tenant_id}/environments", tags=["environments"])


class EnvironmentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    kind: str
    status: str
    is_default: bool
    release_version: str
    deployed_at: datetime | None
    deployment_status: str
    deployment_source: str
    health_state: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    slug: str | None = None


class EnvironmentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DeploymentIn(BaseModel):
    release_version: str = Field(min_length=1, max_length=64)
    source: str = ""
    status: str = "recorded"
    health_state: str = "unknown"


class DeploymentOut(BaseModel):
    release_version: str
    deployed_at: datetime | None
    status: str
    source: str
    health_state: str
    executed: bool


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


@router.get("", response_model=list[EnvironmentOut])
async def list_environments(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        rows = await environment_service.list_environments(session, ctx.tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return rows


@router.post("", response_model=EnvironmentOut, status_code=201)
async def create_environment(
    tenant_id: uuid.UUID,
    payload: EnvironmentCreate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        organization = await organization_for(session, ctx.tenant)
        row = await environment_service.create_environment(
            session,
            ctx.tenant,
            organization,
            name=payload.name,
            kind=payload.kind,
            slug=payload.slug,
            actor=_actor(ctx, request),
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.get("/{environment_id}", response_model=EnvironmentOut)
async def get_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        row = await environment_service.require_environment(session, environment_id, ctx.tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.patch("/{environment_id}", response_model=EnvironmentOut)
async def update_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    payload: EnvironmentUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        row = await environment_service.require_environment(session, environment_id, ctx.tenant_id)
        row = await environment_service.update_environment(
            session, row, ctx.tenant, name=payload.name, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


async def _status(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    target: str,
    request: Request,
    ctx: TenantContext,
    session: AsyncSession,
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        row = await environment_service.require_environment(session, environment_id, ctx.tenant_id)
        row = await environment_service.transition_environment(
            session, row, ctx.tenant, target, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.post("/{environment_id}/suspend", response_model=EnvironmentOut)
async def suspend_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(tenant_id, environment_id, "suspended", request, ctx, session)


@router.post("/{environment_id}/restore", response_model=EnvironmentOut)
async def restore_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(tenant_id, environment_id, "active", request, ctx, session)


@router.post("/{environment_id}/archive", response_model=EnvironmentOut)
async def archive_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(tenant_id, environment_id, "archived", request, ctx, session)


@router.post("/{environment_id}/default", response_model=EnvironmentOut)
async def default_environment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        row = await environment_service.require_environment(session, environment_id, ctx.tenant_id)
        row = await environment_service.set_default(
            session, row, ctx.tenant, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.post("/{environment_id}/deployment", response_model=DeploymentOut)
async def record_deployment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    payload: DeploymentIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        row = await environment_service.require_environment(session, environment_id, ctx.tenant_id)
        state = await environment_service.record_deployment(
            session,
            row,
            ctx.tenant,
            release_version=payload.release_version,
            source=payload.source,
            status=payload.status,
            health_state=payload.health_state,
            actor=_actor(ctx, request),
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return DeploymentOut(
        release_version=state.release_version,
        deployed_at=state.deployed_at,
        status=state.status,
        source=state.source,
        health_state=state.health_state,
        executed=state.executed,
    )


@router.get("/{environment_id}/deployment", response_model=DeploymentOut)
async def read_deployment(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        row = await environment_service.require_environment(session, environment_id, ctx.tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    state = environment_service.deployment_of(row)
    return DeploymentOut(
        release_version=state.release_version,
        deployed_at=state.deployed_at,
        status=state.status,
        source=state.source,
        health_state=state.health_state,
        executed=False,
    )


class PromotionIn(BaseModel):
    target_kind: str = Field(min_length=1, max_length=32)
    target_tenant_id: uuid.UUID | None = None


class PromotionOut(BaseModel):
    source_kind: str
    target_kind: str
    copies_secrets: bool
    copies_configuration: bool
    overwrites_production: bool
    executes_deployment: bool
    withheld_fields: list[str]


@router.post("/{environment_id}/promotion-plan", response_model=PromotionOut)
async def promotion_plan(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    payload: PromotionIn,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """A plan only. No configuration is copied and no deployment is executed."""
    from app.tenancy.environment import assert_same_tenant, plan_promotion
    from app.tenancy.policy import authorize_environment

    try:
        if payload.target_tenant_id is not None:
            assert_same_tenant(ctx.tenant_id, payload.target_tenant_id)
        row = await authorize_environment(
            session,
            ctx,
            tenant_id,
            environment_id,
            Permission.TENANT_UPDATE,
            claimed_tenant_id=payload.target_tenant_id,
        )
        plan = plan_promotion(row.kind, payload.target_kind)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return plan.as_dict()
