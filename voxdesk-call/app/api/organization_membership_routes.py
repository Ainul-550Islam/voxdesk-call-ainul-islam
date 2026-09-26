"""Organization member administration.

The organization id in the path is checked against the principal. A body field
with the same name is not a grant. Cross-organization requests are 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    TenantContext,
    get_optional_context,
    require_human_session,
    require_permission,
)
from app.auth.permissions import Permission
from app.db.session import get_session
from app.organization import invitations as invitation_service
from app.organization import membership_service
from app.organization.access import (
    can_manage_members,
    can_read_organization,
)
from app.organization.invitations import InvitationInvalid
from app.organization.membership_policies import assert_can_grant
from app.organization.roles import resolve_organization_role
from app.organization.service import Actor
from app.tenancy.isolation import (
    BoundaryDenied,
    Forbidden,
    HierarchyError,
    client_ip,
    to_http,
)

router = APIRouter(prefix="/api/organizations", tags=["organization-membership"])


class MemberOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class RoleBody(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    user_id: uuid.UUID | None = None


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=64)


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
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _http(exc: HierarchyError):
    raise to_http(exc)


async def _require_read(session, ctx, organization_id: uuid.UUID) -> None:
    decision = await can_read_organization(
        session, ctx.user, organization_id,
        scopes=ctx.scopes, membership_status=ctx.membership_status,
    )
    if decision.boundary:
        raise BoundaryDenied()
    if not decision.allowed:
        raise Forbidden()


async def _require_manage(session, ctx, organization_id: uuid.UUID) -> None:
    decision = await can_manage_members(
        session, ctx.user, organization_id,
        scopes=ctx.scopes, membership_status=ctx.membership_status,
    )
    if decision.boundary:
        raise BoundaryDenied()
    if not decision.allowed:
        raise Forbidden()


@router.get("/{organization_id}/members", response_model=list[MemberOut])
async def list_members(
    organization_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _require_read(session, ctx, organization_id)
        rows = await membership_service.list_members(session, ctx.user, organization_id)
    except HierarchyError as exc:
        _http(exc)
    return [
        MemberOut(
            id=row.id, organization_id=row.organization_id, user_id=row.user_id,
            role=row.role.value, status=row.status, created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/{organization_id}/members", response_model=MemberOut)
async def add_member(
    organization_id: uuid.UUID,
    body: RoleBody,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    if body.user_id is None:
        raise to_http(Forbidden("A user id is required"))
    try:
        await _require_manage(session, ctx, organization_id)
        row = await membership_service.add_member(
            session, actor_user=ctx.user, organization_id=organization_id,
            target_user_id=body.user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.get("/{organization_id}/members/{user_id}", response_model=MemberOut)
async def get_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _require_read(session, ctx, organization_id)
        row = await membership_service.require_membership(session, organization_id, user_id)
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.patch("/{organization_id}/members/{user_id}", response_model=MemberOut)
async def update_member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: RoleBody,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_ROLE_CHANGE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row = await membership_service.update_role(
            session, actor_user=ctx.user, organization_id=organization_id,
            target_user_id=user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


async def _status(
    action, organization_id, user_id, request, ctx, session,
):
    if not ctx.can(Permission.USER_UPDATE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row = await action(
            session, actor_user=ctx.user, organization_id=organization_id,
            target_user_id=user_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.post("/{organization_id}/members/{user_id}/suspend", response_model=MemberOut)
async def suspend_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.suspend_member, organization_id, user_id, request, ctx, session,
    )


@router.post("/{organization_id}/members/{user_id}/reactivate", response_model=MemberOut)
async def reactivate_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.reactivate_member, organization_id, user_id, request, ctx, session,
    )


@router.post("/{organization_id}/members/{user_id}/revoke", response_model=MemberOut)
async def revoke_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.revoke_member, organization_id, user_id, request, ctx, session,
    )


@router.post("/{organization_id}/invitations", response_model=InvitationOut)
async def invite_member(
    organization_id: uuid.UUID,
    body: InviteBody,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        role = resolve_organization_role(body.role)
        assert_can_grant(ctx.user, role)
        row, token = await invitation_service.create_invitation(
            session,
            actor_user=ctx.user,
            scope="organization",
            organization_id=organization_id,
            tenant_id=None,
            home_tenant_id=ctx.tenant_id,
            email=body.email,
            role=role,
            actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at,
        scope=row.scope, invitation_token=token,
    )


@router.post("/{organization_id}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_DELETE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row = await invitation_service.revoke_invitation(
            session, actor_user=ctx.user, invitation_id=invitation_id,
            organization_id=organization_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope,
    )


@router.post("/{organization_id}/invitations/{invitation_id}/resend", response_model=InvitationOut)
async def resend_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row, token = await invitation_service.resend_invitation(
            session, actor_user=ctx.user, invitation_id=invitation_id,
            organization_id=organization_id, actor=_actor(ctx, request),
        )
    except InvitationInvalid as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400, detail={"code": "invitation_invalid", "message": exc.message},
        ) from None
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at,
        scope=row.scope, invitation_token=token,
    )


@router.post("/{organization_id}/invitations/accept", response_model=InvitationOut)
async def accept_invitation(
    organization_id: uuid.UUID,
    body: AcceptBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: TenantContext | None = Depends(get_optional_context),
):
    """Accept binds the invitation row. A disagreeing client id is a bad token."""
    if body.organization_id is not None and body.organization_id != organization_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail={"code": "invitation_invalid", "message": "Invitation is not valid."},
        )
    try:
        row = await invitation_service.accept_invitation(
            session,
            raw_token=body.invitation_token,
            organization_id=organization_id,
            tenant_id=body.tenant_id,
            authenticated=ctx.user if ctx is not None else None,
            email=body.email,
            password=body.password,
            actor=_actor(ctx, request) if ctx is not None else None,
        )
    except InvitationInvalid as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400, detail={"code": "invitation_invalid", "message": exc.message},
        ) from None
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope,
    )
