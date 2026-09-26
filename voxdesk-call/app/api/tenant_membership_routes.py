"""Tenant member administration.

The path tenant must belong to the caller's organization. A body tenant id is
ignored as an authorization source and rejected when it disagrees.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_human_session, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.organization.invitations import InvitationInvalid
from app.organization.service import Actor
from app.tenancy import invitations as tenant_invitations
from app.tenancy import membership_service
from app.tenancy.access import verify_operation
from app.tenancy.isolation import BoundaryDenied, Forbidden, HierarchyError, client_ip, to_http

router = APIRouter(prefix="/api/tenants", tags=["tenant-membership"])


class MemberOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    created_at: datetime


class RoleBody(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    user_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=64)
    tenant_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class AcceptBody(BaseModel):
    invitation_token: str = Field(min_length=10, max_length=200)
    email: str | None = None
    password: str | None = None
    organization_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class InvitationOut(BaseModel):
    id: uuid.UUID
    status: str
    expires_at: datetime
    scope: str
    invitation_token: str | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id, email=ctx.user.email, tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _reject_client_ids(body, tenant_id: uuid.UUID) -> None:
    if getattr(body, "tenant_id", None) not in (None, tenant_id):
        raise BoundaryDenied()


async def _gate(session, ctx, tenant_id, conceptual: str) -> None:
    decision = await verify_operation(
        session, ctx.user, tenant_id, conceptual,
        scopes=ctx.scopes, membership_status=ctx.membership_status,
    )
    if decision.boundary:
        raise BoundaryDenied()
    if not decision.allowed:
        raise Forbidden()


def _out(row) -> MemberOut:
    return MemberOut(
        id=row.id, tenant_id=row.tenant_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.get("/{tenant_id}/members", response_model=list[MemberOut])
async def list_members(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:read")
        rows = await membership_service.list_members(session, ctx.user, tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return [_out(row) for row in rows]


@router.post("/{tenant_id}/members", response_model=MemberOut)
async def add_member(
    tenant_id: uuid.UUID, body: RoleBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE) or body.user_id is None:
        raise to_http(Forbidden())
    try:
        _reject_client_ids(body, tenant_id)
        await _gate(session, ctx, tenant_id, "tenant:members:invite")
        row = await membership_service.add_member(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            target_user_id=body.user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


@router.get("/{tenant_id}/members/{user_id}", response_model=MemberOut)
async def get_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:read")
        row = await membership_service.get_membership(session, tenant_id, user_id)
        if row is None:
            raise BoundaryDenied()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


@router.patch("/{tenant_id}/members/{user_id}", response_model=MemberOut)
async def update_role(
    tenant_id: uuid.UUID, user_id: uuid.UUID, body: RoleBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_ROLE_CHANGE):
        raise to_http(Forbidden())
    try:
        _reject_client_ids(body, tenant_id)
        await _gate(session, ctx, tenant_id, "tenant:members:update")
        row = await membership_service.update_role(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            target_user_id=user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


async def _status(action, tenant_id, user_id, request, ctx, session, permission):
    if not ctx.can(permission):
        raise to_http(Forbidden())
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:update")
        row = await action(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            target_user_id=user_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


@router.post("/{tenant_id}/members/{user_id}/suspend", response_model=MemberOut)
async def suspend_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.suspend_member, tenant_id, user_id, request, ctx, session,
        Permission.USER_UPDATE,
    )


@router.post("/{tenant_id}/members/{user_id}/reactivate", response_model=MemberOut)
async def reactivate_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.reactivate_member, tenant_id, user_id, request, ctx, session,
        Permission.USER_UPDATE,
    )


@router.post("/{tenant_id}/members/{user_id}/revoke", response_model=MemberOut)
async def revoke_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.revoke_member, tenant_id, user_id, request, ctx, session,
        Permission.USER_DELETE,
    )


@router.post("/{tenant_id}/invitations", response_model=InvitationOut)
async def invite(
    tenant_id: uuid.UUID, body: InviteBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:invite")
        row, token = await tenant_invitations.create(
            session, actor_user=ctx.user, tenant_id=tenant_id, email=body.email,
            role_name=body.role, actor=_actor(ctx, request),
            client_organization_id=body.organization_id, client_tenant_id=body.tenant_id,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at,
        scope=row.scope, invitation_token=token,
    )


@router.post("/{tenant_id}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    tenant_id: uuid.UUID, invitation_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_DELETE):
        raise to_http(Forbidden())
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:remove")
        row = await tenant_invitations.revoke(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            invitation_id=invitation_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return InvitationOut(id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope)


@router.post("/{tenant_id}/invitations/accept", response_model=InvitationOut)
async def accept_invitation(
    tenant_id: uuid.UUID, body: AcceptBody, request: Request,
    session: AsyncSession = Depends(get_session),
):
    claimed_org = body.organization_id
    claimed_tenant = body.tenant_id or tenant_id
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "invitation_invalid", "message": "Invitation is not valid."},
        )
    try:
        row = await tenant_invitations.accept(
            session, raw_token=body.invitation_token, tenant_id=claimed_tenant,
            organization_id=claimed_org, email=body.email, password=body.password,
            actor=Actor(ip_address=client_ip(request)),
        )
    except InvitationInvalid as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invitation_invalid", "message": exc.message},
        ) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return InvitationOut(id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope)
