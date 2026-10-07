"""Organization administration.

A normal tenant user can see and, with ``tenant:update``, maintain the
organization their own tenant already belongs to. Creating a new global
organization is ``TENANT_CREATE``, which is platform-only, and
``get_platform_admin`` denies every caller. That is the existing RBAC, not a
new permission.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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
from app.tenancy.isolation import (
    HierarchyError,
    client_ip,
    require_same_organization,
    to_http,
)

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class StatusBody(BaseModel):
    status: str = Field(min_length=1, max_length=32)


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


@router.get("", response_model=list[OrganizationOut])
async def list_organizations(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The caller's organization only. Query parameters cannot widen this."""
    if ctx.tenant.organization_id is None:
        return []
    row = await organization_service.get_organization(session, ctx.tenant.organization_id)
    if row is None:
        return []
    return [row]


@router.post("", response_model=OrganizationOut, status_code=201)
async def create_organization(
    payload: OrganizationCreate,
    request: Request,
    _: TenantContext = Depends(get_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    """Platform-only. ``get_platform_admin`` denies every current principal."""
    try:
        row = await organization_service.create_organization(
            session,
            name=payload.name,
            slug=payload.slug,
            actor=Actor(ip_address=client_ip(request)),
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.get("/{organization_id}", response_model=OrganizationOut)
async def get_organization(
    organization_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_organization(ctx.tenant, organization_id)
        row = await organization_service.require_organization(session, organization_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.patch("/{organization_id}", response_model=OrganizationOut)
async def update_organization(
    organization_id: uuid.UUID,
    payload: OrganizationUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        require_same_organization(ctx.tenant, organization_id)
        row = await organization_service.require_organization(session, organization_id)
        row = await organization_service.update_organization(
            session, row, name=payload.name, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


async def _transition(
    organization_id: uuid.UUID,
    target: str,
    request: Request,
    ctx: TenantContext,
    session: AsyncSession,
):
    try:
        require_same_organization(ctx.tenant, organization_id)
        row = await organization_service.require_organization(session, organization_id)
        row = await organization_service.transition_organization(
            session, row, target, actor=_actor(ctx, request)
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row


@router.post("/{organization_id}/suspend", response_model=OrganizationOut)
async def suspend_organization(
    organization_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(organization_id, "suspended", request, ctx, session)


@router.post("/{organization_id}/restore", response_model=OrganizationOut)
async def restore_organization(
    organization_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(organization_id, "active", request, ctx, session)


@router.post("/{organization_id}/read-only", response_model=OrganizationOut)
async def read_only_organization(
    organization_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(organization_id, "read_only", request, ctx, session)


@router.post("/{organization_id}/lifecycle", response_model=OrganizationOut)
async def organization_lifecycle(
    organization_id: uuid.UUID,
    payload: StatusBody,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    _: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(organization_id, payload.status, request, ctx, session)


@router.get("/{organization_id}/placement")
async def organization_placement(
    organization_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """Placement label for the caller's organization. Not a physical claim."""
    from app.tenancy.data_residency import placement_view

    try:
        require_same_organization(ctx.tenant, organization_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return placement_view(organization_id)
