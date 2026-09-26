"""Tenant invitation lifecycle.

An invitation created for one organization and one tenant cannot be accepted
as another pair. Client-supplied ids are compared and then discarded; the row
is the binding.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InvitationScope, MembershipInvitation, User
from app.organization import invitations as invitations_core
from app.organization.invitations import InvitationInvalid
from app.organization.membership_policies import assert_can_grant
from app.organization.service import Actor
from app.tenancy.access import load_tenant_in_organization
from app.tenancy.isolation import BoundaryDenied, Forbidden
from app.tenancy.roles import resolve_tenant_role

__all__ = ["InvitationInvalid", "accept", "create", "resend", "revoke"]


async def create(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    email: str,
    role_name: str,
    actor: Actor | None = None,
    client_organization_id: uuid.UUID | None = None,
    client_tenant_id: uuid.UUID | None = None,
) -> tuple[MembershipInvitation, str]:
    tenant = await load_tenant_in_organization(session, actor_user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    if client_tenant_id is not None and client_tenant_id != tenant.id:
        raise BoundaryDenied()
    if client_organization_id is not None and client_organization_id != tenant.organization_id:
        raise BoundaryDenied()
    role = resolve_tenant_role(role_name)
    try:
        assert_can_grant(actor_user, role)
    except Forbidden:
        raise
    return await invitations_core.create_invitation(
        session,
        actor_user=actor_user,
        scope=InvitationScope.TENANT.value,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        home_tenant_id=tenant.id,
        email=email,
        role=role,
        actor=actor,
    )


async def accept(
    session: AsyncSession,
    *,
    raw_token: str,
    tenant_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
    authenticated: User | None = None,
    email: str | None = None,
    password: str | None = None,
    actor: Actor | None = None,
) -> MembershipInvitation:
    """Accept only if the token's tenant is the tenant in the path."""
    return await invitations_core.accept_invitation(
        session,
        raw_token=raw_token,
        organization_id=organization_id,
        tenant_id=tenant_id,
        authenticated=authenticated,
        email=email,
        password=password,
        actor=actor,
    )


async def revoke(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    actor: Actor | None = None,
) -> MembershipInvitation:
    tenant = await load_tenant_in_organization(session, actor_user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    from app.db.models import MembershipInvitation

    existing = await session.get(MembershipInvitation, invitation_id)
    if existing is None or existing.tenant_id != tenant.id:
        raise BoundaryDenied()
    row = await invitations_core.revoke_invitation(
        session,
        actor_user=actor_user,
        invitation_id=invitation_id,
        organization_id=tenant.organization_id,
        actor=actor,
    )
    if row.tenant_id != tenant.id:
        raise BoundaryDenied()
    return row


async def resend(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    actor: Actor | None = None,
) -> tuple[MembershipInvitation, str]:
    tenant = await load_tenant_in_organization(session, actor_user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    from app.db.models import MembershipInvitation

    existing = await session.get(MembershipInvitation, invitation_id)
    if existing is None or existing.tenant_id != tenant.id:
        raise BoundaryDenied()
    row, raw = await invitations_core.resend_invitation(
        session,
        actor_user=actor_user,
        invitation_id=invitation_id,
        organization_id=tenant.organization_id,
        actor=actor,
    )
    if row.tenant_id != tenant.id:
        raise InvitationInvalid()
    return row, raw
