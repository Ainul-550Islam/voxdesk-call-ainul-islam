"""Tenant administration inside the caller's organization.

Path tenant ids are untrusted. They must match the tenant on the principal or
the response is 404. Body fields named ``tenant_id`` or ``organization_id`` are
not part of the schema and are ignored.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    TenantContext,
    get_platform_admin,
    require_human_session,
    require_permission,
)
from app.auth.permissions import Permission
from app.db.session import get_session
from app.organization import service as organization_service
from app.organization.service import Actor
from app.tenancy import service as tenancy_service
from app.tenancy.context import HierarchyContext, load_hierarchy
from app.tenancy.isolation import (
    HierarchyError,
    client_ip,
    require_same_tenant,
    to_http,
)

router = APIRouter(prefix="/api", tags=["tenant-admin"])


class TenantAdminOut(BaseModel):
    id: uuid.UUID
    name: str
    organization_id: uuid.UUID
    lifecycle_status: str
    is_active: bool
    twilio_number: str

    class Config:
        from_attributes = True


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    twilio_number: str = Field(min_length=1, max_length=32)
    industry: str = "general"


class TenantProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class StatusBody(BaseModel):
    status: str = Field(min_length=1, max_length=32)


class HierarchyOut(BaseModel):
    organization_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    organization_status: str
    tenant_status: str
    auth_method: str
    is_machine: bool
    environment_id: uuid.UUID | None = None
    environment_kind: str | None = None
    environment_status: str | None = None
    default_environment_id: uuid.UUID | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _hierarchy_out(hierarchy: HierarchyContext) -> HierarchyOut:
    return HierarchyOut(
        organization_id=hierarchy.organization_id,
        tenant_id=hierarchy.tenant_id,
        user_id=hierarchy.user_id,
        organization_status=hierarchy.organization_status,
        tenant_status=hierarchy.tenant_status,
        auth_method=hierarchy.auth_method,
        is_machine=hierarchy.is_machine,
        environment_id=hierarchy.environment_id,
        environment_kind=hierarchy.environment_kind,
        environment_status=hierarchy.environment_status,
        default_environment_id=hierarchy.default_environment_id,
    )


@router.get("/organizations/{organization_id}/tenants", response_model=list[TenantAdminOut])
async def list_organization_tenants(
    organization_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    try:
        rows = tenancy_service.visible_tenants(ctx.tenant, organization_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return rows


@router.post(
    "/organizations/{organization_id}/tenants",
    response_model=TenantAdminOut,
    status_code=201,
)
async def create_organization_tenant(
    organization_id: uuid.UUID,
    payload: TenantCreate,
    request: Request,
    _: TenantContext = Depends(get_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    """Creating a business remains platform-only, matching ``POST /api/tenants``."""
    try:
        organization = await organization_service.require_organization(session, organization_id)
        tenant = await tenancy_service.create_tenant_under_organization(
            session,
            organization,
            name=payload.name,
            twilio_number=payload.twilio_number,
            industry=payload.industry,
            actor=Actor(ip_address=client_ip(request)),
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return tenant


@router.get("/tenants/{tenant_id}/hierarchy", response_model=HierarchyOut)
async def tenant_hierarchy(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """``environment_id`` is optional and checked. It is not an authority."""
    from app.tenancy.context import reject_client_override

    try:
        reject_client_override(ctx, claimed_tenant_id)
        require_same_tenant(ctx.tenant, tenant_id)
        hierarchy = await load_hierarchy(session, ctx, environment_id=environment_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _hierarchy_out(hierarchy)


@router.patch("/tenants/{tenant_id}/profile", response_model=TenantAdminOut)
async def update_tenant_profile(
    tenant_id: uuid.UUID,
    payload: TenantProfileUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        tenant = await tenancy_service.update_profile(
            session, ctx.tenant, name=payload.name, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return tenant


async def _tenant_status(
    tenant_id: uuid.UUID,
    target: str,
    request: Request,
    ctx: TenantContext,
    session: AsyncSession,
):
    try:
        require_same_tenant(ctx.tenant, tenant_id)
        tenant = await tenancy_service.transition_tenant(
            session, ctx.tenant, target, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return tenant


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantAdminOut)
async def suspend_tenant(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _tenant_status(tenant_id, "suspended", request, ctx, session)


@router.post("/tenants/{tenant_id}/restore", response_model=TenantAdminOut)
async def restore_tenant(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _tenant_status(tenant_id, "active", request, ctx, session)


@router.post("/tenants/{tenant_id}/read-only", response_model=TenantAdminOut)
async def read_only_tenant(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _tenant_status(tenant_id, "read_only", request, ctx, session)


@router.post("/tenants/{tenant_id}/lifecycle", response_model=TenantAdminOut)
async def tenant_lifecycle(
    tenant_id: uuid.UUID,
    payload: StatusBody,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _tenant_status(tenant_id, payload.status, request, ctx, session)
