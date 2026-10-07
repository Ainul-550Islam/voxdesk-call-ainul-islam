"""Tenant membership lifecycle.

Every mutation checks the actor's organization, then the tenant's parent, then
the existing role policy. A client cannot name a tenant in another organization
and have the row created there.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.auth.rbac import can_assign_role, can_manage_user, role_level
from app.db.models import (
    AuditAction,
    MembershipStatus,
    OrganizationMembership,
    Tenant,
    TenantMembership,
    User,
    UserRole,
)
from app.organization.membership_policies import assert_transition
from app.organization.membership_service import organization_for_user
from app.organization.service import Actor
from app.tenancy.isolation import BoundaryDenied, Conflict, Forbidden, LifecycleDenied, NotFound
from app.tenancy.roles import resolve_tenant_role


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor(actor: Actor | None) -> Actor:
    return actor or Actor()


async def require_tenant_in_actor_org(
    session: AsyncSession, actor: User, tenant_id: uuid.UUID
) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    actor_org = await organization_for_user(session, actor)
    if tenant is None or actor_org is None or tenant.organization_id != actor_org.id:
        raise BoundaryDenied()
    return tenant


async def get_membership(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> TenantMembership | None:
    return (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def effective_tenant_role(
    session: AsyncSession, user: User, tenant: Tenant
) -> UserRole | None:
    """Active tenant role, else an active organization owner/admin of the parent.

    A viewer in the organization does not inherit into a tenant they do not
    belong to. A revoked row is not papered over by a higher organization role
    when the row is for this same user and tenant — explicit deny wins.
    """
    row = await get_membership(session, tenant.id, user.id)
    if row is not None:
        if row.status in (MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value):
            return None
        return row.role
    if user.tenant_id == tenant.id:
        return user.role
    org_row = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == tenant.organization_id,
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if org_row is None:
        return None
    if org_row.role in (UserRole.OWNER, UserRole.ADMIN):
        return org_row.role
    return None


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: Actor,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    detail: dict,
) -> None:
    await emit(
        session,
        action,
        tenant_id=tenant_id,
        actor_user_id=actor.user_id,
        target_user_id=target_user_id,
        actor_email=actor.email,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        detail={"tenant_id": str(tenant_id), "scope": "tenant", **detail},
        commit=False,
    )


async def list_members(
    session: AsyncSession, actor: User, tenant_id: uuid.UUID
) -> list[TenantMembership]:
    tenant = await require_tenant_in_actor_org(session, actor, tenant_id)
    role = await effective_tenant_role(session, actor, tenant)
    if role is None:
        raise BoundaryDenied()
    rows = (
        (
            await session.execute(
                select(TenantMembership)
                .where(TenantMembership.tenant_id == tenant_id)
                .order_by(TenantMembership.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def add_member(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> TenantMembership:
    tenant = await require_tenant_in_actor_org(session, actor_user, tenant_id)
    if tenant.lifecycle_status in ("suspended", "deleted", "read_only"):
        raise LifecycleDenied("This tenant is not accepting members")
    actor_role = await effective_tenant_role(session, actor_user, tenant)
    if actor_role is None:
        raise BoundaryDenied()
    role = resolve_tenant_role(role_name)
    if actor_user.id == target_user_id:
        raise Forbidden("You cannot change your own membership")
    if not can_assign_role(actor_role, role):
        raise Forbidden("You cannot grant that role")
    target = await session.get(User, target_user_id)
    if target is None or target.tenant_id != tenant.id:
        raise BoundaryDenied()
    existing = await get_membership(session, tenant.id, target.id)
    if existing is not None and existing.status == MembershipStatus.REVOKED.value:
        raise Conflict("A revoked membership cannot be restored this way")
    if existing is not None and existing.status == MembershipStatus.ACTIVE.value:
        raise Conflict("That person is already a member")
    now = _now()
    if existing is None:
        existing = TenantMembership(
            tenant_id=tenant.id,
            user_id=target.id,
            role=role,
            status=MembershipStatus.ACTIVE.value,
            accepted_at=now,
        )
        session.add(existing)
    else:
        if not can_manage_user(actor_role, existing.role):
            raise Forbidden("You cannot modify a member at or above your level")
        existing.role = role
        existing.status = MembershipStatus.ACTIVE.value
        existing.accepted_at = now
        existing.suspended_at = None
        existing.updated_at = now
    if target.tenant_id == tenant.id:
        target.role = role
        target.token_version += 1
    await _audit(
        session,
        AuditAction.MEMBERSHIP_CREATED,
        actor=_actor(actor),
        tenant_id=tenant.id,
        target_user_id=target.id,
        detail={"role": role.value, "status": "active"},
    )
    await session.commit()
    await session.refresh(existing)
    return existing


async def _count_owners(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    rows = (
        (
            await session.execute(
                select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.role == UserRole.OWNER,
                    TenantMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def update_role(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> TenantMembership:
    tenant = await require_tenant_in_actor_org(session, actor_user, tenant_id)
    actor_role = await effective_tenant_role(session, actor_user, tenant)
    if actor_role is None:
        raise BoundaryDenied()
    row = await get_membership(session, tenant.id, target_user_id)
    if row is None:
        raise NotFound()
    role = resolve_tenant_role(role_name)
    if actor_user.id == target_user_id or role_level(role) > role_level(actor_role):
        raise Forbidden("You cannot grant that role")
    if not can_manage_user(actor_role, row.role) or not can_assign_role(actor_role, role):
        raise Forbidden("You cannot modify a member at or above your level")
    if (
        row.role is UserRole.OWNER
        and role is not UserRole.OWNER
        and row.status == MembershipStatus.ACTIVE.value
        and await _count_owners(session, tenant.id) <= 1
    ):
        raise Conflict("A tenant must keep at least one active owner")
    previous = row.role
    row.role = role
    row.updated_at = _now()
    target = await session.get(User, target_user_id)
    if target is not None and target.tenant_id == tenant.id:
        target.role = role
        target.token_version += 1
    await _audit(
        session,
        AuditAction.MEMBERSHIP_UPDATED,
        actor=_actor(actor),
        tenant_id=tenant.id,
        target_user_id=target_user_id,
        detail={"from": previous.value, "to": role.value},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def _set_status(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    status: str,
    action: AuditAction,
    actor: Actor | None,
) -> TenantMembership:
    tenant = await require_tenant_in_actor_org(session, actor_user, tenant_id)
    actor_role = await effective_tenant_role(session, actor_user, tenant)
    if actor_role is None:
        raise BoundaryDenied()
    row = await get_membership(session, tenant.id, target_user_id)
    if row is None:
        raise NotFound()
    if actor_user.id == target_user_id:
        raise Forbidden("You cannot change your own membership")
    if not can_manage_user(actor_role, row.role):
        raise Forbidden("You cannot modify a member at or above your level")
    if row.status == status:
        return row
    if status == MembershipStatus.ACTIVE.value and row.status == MembershipStatus.REVOKED.value:
        raise LifecycleDenied("A revoked membership cannot be restored this way")
    assert_transition(row.status, status)
    if (
        row.role is UserRole.OWNER
        and row.status == MembershipStatus.ACTIVE.value
        and status != MembershipStatus.ACTIVE.value
        and await _count_owners(session, tenant.id) <= 1
    ):
        raise Conflict("A tenant must keep at least one active owner")
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
        tenant_id=tenant.id,
        target_user_id=target_user_id,
        detail={"status": status, "role": row.role.value},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def suspend_member(session, *, actor_user, tenant_id, target_user_id, actor=None):
    return await _set_status(
        session,
        actor_user=actor_user,
        tenant_id=tenant_id,
        target_user_id=target_user_id,
        status=MembershipStatus.SUSPENDED.value,
        action=AuditAction.MEMBERSHIP_SUSPENDED,
        actor=actor,
    )


async def reactivate_member(session, *, actor_user, tenant_id, target_user_id, actor=None):
    return await _set_status(
        session,
        actor_user=actor_user,
        tenant_id=tenant_id,
        target_user_id=target_user_id,
        status=MembershipStatus.ACTIVE.value,
        action=AuditAction.MEMBERSHIP_RESTORED,
        actor=actor,
    )


async def revoke_member(session, *, actor_user, tenant_id, target_user_id, actor=None):
    return await _set_status(
        session,
        actor_user=actor_user,
        tenant_id=tenant_id,
        target_user_id=target_user_id,
        status=MembershipStatus.REVOKED.value,
        action=AuditAction.MEMBERSHIP_REVOKED,
        actor=actor,
    )
