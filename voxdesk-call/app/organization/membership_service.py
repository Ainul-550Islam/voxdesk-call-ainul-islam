"""Organization membership lifecycle.

Membership coexists with ``User.tenant_id``. It does not move a user between
organizations and it does not invent a role the existing RBAC policy would
refuse. A missing row is legacy behaviour; a revoked row is an explicit deny.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import (
    AuditAction,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    Tenant,
    TenantMembership,
    User,
    UserRole,
)
from app.organization.membership_policies import (
    assert_can_grant,
    assert_can_touch,
    assert_not_last_owner,
    assert_not_self_escalation,
    assert_organization_accepts_members,
    assert_organization_allows_mutation,
    assert_transition,
)
from app.organization.roles import resolve_organization_role
from app.organization.service import Actor
from app.tenancy.isolation import BoundaryDenied, Conflict, NotFound

_NOW = datetime.now


def _now() -> datetime:
    return _NOW(timezone.utc).replace(tzinfo=None)


def _actor(actor: Actor | None) -> Actor:
    return actor or Actor()


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: Actor,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID | None,
    detail: dict,
) -> None:
    await emit(
        session,
        action,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        target_user_id=target_user_id,
        actor_email=actor.email,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        detail={"organization_id": str(organization_id), **detail},
        commit=False,
    )


async def organization_for_user(
    session: AsyncSession, user: User
) -> Organization | None:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or tenant.organization_id is None:
        return None
    return await session.get(Organization, tenant.organization_id)


async def require_same_organization(
    session: AsyncSession, actor: User, organization_id: uuid.UUID
) -> Organization:
    """404 when the actor is outside this organization, including a missing id."""
    current = await organization_for_user(session, actor)
    if current is None or current.id != organization_id:
        raise BoundaryDenied()
    return current


async def get_membership(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> OrganizationMembership | None:
    return (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def require_membership(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> OrganizationMembership:
    row = await get_membership(session, organization_id, user_id)
    if row is None:
        raise NotFound()
    return row


async def effective_role(
    session: AsyncSession, user: User, organization_id: uuid.UUID
) -> UserRole | None:
    """The stored membership role, or the legacy user role when no row exists."""
    row = await get_membership(session, organization_id, user.id)
    if row is None:
        current = await organization_for_user(session, user)
        if current is None or current.id != organization_id:
            return None
        return user.role
    if row.status in (MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value):
        return None
    return row.role


async def list_members(
    session: AsyncSession, actor: User, organization_id: uuid.UUID
) -> list[OrganizationMembership]:
    await require_same_organization(session, actor, organization_id)
    rows = (
        await session.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(OrganizationMembership.created_at)
        )
    ).scalars().all()
    return list(rows)


async def _sync_user_role(session: AsyncSession, user: User, role: UserRole) -> None:
    """Keep the existing RBAC column aligned with the home-tenant membership."""
    if user.role is role:
        return
    user.role = role
    user.token_version += 1
    await session.execute(
        update(TenantMembership)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == user.tenant_id,
        )
        .values(role=role, updated_at=_now())
    )


async def sync_home_role(session: AsyncSession, user: User) -> None:
    """Copy ``User.role`` onto the membership rows the insert hook created.

    Called by the existing team role-change path so the two stores cannot
    diverge. It does not grant a role the caller did not already set.
    """
    now = _now()
    organization = await organization_for_user(session, user)
    if organization is not None:
        await session.execute(
            update(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.user_id == user.id,
            )
            .values(role=user.role, updated_at=now)
        )
    await session.execute(
        update(TenantMembership)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == user.tenant_id,
        )
        .values(role=user.role, updated_at=now)
    )


async def add_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> OrganizationMembership:
    organization = await require_same_organization(session, actor_user, organization_id)
    assert_organization_accepts_members(organization)
    role = resolve_organization_role(role_name)
    assert_not_self_escalation(actor_user, target_user_id, role)
    assert_can_grant(actor_user, role)
    target = await session.get(User, target_user_id)
    if target is None:
        raise NotFound()
    target_org = await organization_for_user(session, target)
    if target_org is None or target_org.id != organization_id:
        raise BoundaryDenied()
    existing = await get_membership(session, organization_id, target.id)
    if existing is not None and existing.status == MembershipStatus.REVOKED.value:
        raise Conflict("A revoked membership cannot be restored this way")
    if existing is not None and existing.status == MembershipStatus.ACTIVE.value:
        raise Conflict("That person is already a member")
    now = _now()
    if existing is None:
        existing = OrganizationMembership(
            organization_id=organization_id,
            user_id=target.id,
            role=role,
            status=MembershipStatus.ACTIVE.value,
            invited_at=None,
            accepted_at=now,
        )
        session.add(existing)
    else:
        assert_can_touch(actor_user, existing.role, self_target=False)
        existing.role = role
        existing.status = MembershipStatus.ACTIVE.value
        existing.accepted_at = now
        existing.suspended_at = None
        existing.updated_at = now
    if target.tenant_id == actor_user.tenant_id:
        await _sync_user_role(session, target, role)
    who = _actor(actor)
    await _audit(
        session,
        AuditAction.MEMBERSHIP_CREATED,
        actor=who,
        organization_id=organization_id,
        target_user_id=target.id,
        detail={"scope": "organization", "role": role.value, "status": "active"},
    )
    await session.commit()
    await session.refresh(existing)
    return existing


async def update_role(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> OrganizationMembership:
    organization = await require_same_organization(session, actor_user, organization_id)
    assert_organization_allows_mutation(organization)
    role = resolve_organization_role(role_name)
    row = await require_membership(session, organization_id, target_user_id)
    if actor_user.id == target_user_id:
        from app.tenancy.isolation import Forbidden
        raise Forbidden("You cannot change your own membership")
    assert_can_touch(actor_user, row.role, self_target=False)
    assert_can_grant(actor_user, role)
    await assert_not_last_owner(
        session, organization_id, row, next_role=role, next_status=None
    )
    previous = row.role
    row.role = role
    row.updated_at = _now()
    target = await session.get(User, target_user_id)
    if target is not None and target.tenant_id == actor_user.tenant_id:
        await _sync_user_role(session, target, role)
    await _audit(
        session,
        AuditAction.MEMBERSHIP_UPDATED,
        actor=_actor(actor),
        organization_id=organization_id,
        target_user_id=target_user_id,
        detail={
            "scope": "organization",
            "from": previous.value,
            "to": role.value,
        },
    )
    await session.commit()
    await session.refresh(row)
    return row


async def _set_status(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    status: str,
    action: AuditAction,
    actor: Actor | None,
) -> OrganizationMembership:
    organization = await require_same_organization(session, actor_user, organization_id)
    if status != MembershipStatus.ACTIVE.value:
        assert_organization_allows_mutation(organization)
    row = await require_membership(session, organization_id, target_user_id)
    if actor_user.id == target_user_id:
        from app.tenancy.isolation import Forbidden
        raise Forbidden("You cannot change your own membership")
    assert_can_touch(actor_user, row.role, self_target=False)
    if row.status == status:
        return row
    assert_transition(row.status, status)
    await assert_not_last_owner(
        session, organization_id, row, next_role=None, next_status=status
    )
    now = _now()
    row.status = status
    row.updated_at = now
    if status == MembershipStatus.SUSPENDED.value:
        row.suspended_at = now
    if status == MembershipStatus.REVOKED.value:
        row.revoked_at = now
        target = await session.get(User, target_user_id)
        if target is not None:
            target.token_version += 1
    if status == MembershipStatus.ACTIVE.value:
        row.suspended_at = None
    await _audit(
        session,
        action,
        actor=_actor(actor),
        organization_id=organization_id,
        target_user_id=target_user_id,
        detail={"scope": "organization", "status": status, "role": row.role.value},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def suspend_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor: Actor | None = None,
) -> OrganizationMembership:
    return await _set_status(
        session,
        actor_user=actor_user,
        organization_id=organization_id,
        target_user_id=target_user_id,
        status=MembershipStatus.SUSPENDED.value,
        action=AuditAction.MEMBERSHIP_SUSPENDED,
        actor=actor,
    )


async def reactivate_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor: Actor | None = None,
) -> OrganizationMembership:
    row = await require_membership(session, organization_id, target_user_id)
    if row.status == MembershipStatus.REVOKED.value:
        from app.tenancy.isolation import LifecycleDenied
        raise LifecycleDenied("A revoked membership cannot be restored this way")
    return await _set_status(
        session,
        actor_user=actor_user,
        organization_id=organization_id,
        target_user_id=target_user_id,
        status=MembershipStatus.ACTIVE.value,
        action=AuditAction.MEMBERSHIP_RESTORED,
        actor=actor,
    )


async def revoke_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor: Actor | None = None,
) -> OrganizationMembership:
    return await _set_status(
        session,
        actor_user=actor_user,
        organization_id=organization_id,
        target_user_id=target_user_id,
        status=MembershipStatus.REVOKED.value,
        action=AuditAction.MEMBERSHIP_REVOKED,
        actor=actor,
    )
