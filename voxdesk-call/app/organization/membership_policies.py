"""Eligibility rules for organization membership.

Domain-based SSO enforcement stays in the identity layer. These rules only
decide whether a membership change is legal given the organization state, the
existing role policy, and the membership lifecycle.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import can_assign_role, can_manage_user, role_level
from app.db.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    User,
    UserRole,
)
from app.tenancy.isolation import Conflict, Forbidden, LifecycleDenied

_TRANSITIONS: dict[str, frozenset[str]] = {
    MembershipStatus.INVITED.value: frozenset({
        MembershipStatus.ACTIVE.value,
        MembershipStatus.REVOKED.value,
        MembershipStatus.EXPIRED.value,
    }),
    MembershipStatus.ACTIVE.value: frozenset({
        MembershipStatus.SUSPENDED.value,
        MembershipStatus.REVOKED.value,
    }),
    MembershipStatus.SUSPENDED.value: frozenset({
        MembershipStatus.ACTIVE.value,
        MembershipStatus.REVOKED.value,
    }),
    MembershipStatus.REVOKED.value: frozenset(),
    MembershipStatus.EXPIRED.value: frozenset(),
}

_OPEN_ORG = frozenset({"active"})
_BLOCK_NEW = frozenset({"suspended", "read_only", "deleted"})


def status_value(status: str | MembershipStatus) -> str:
    if isinstance(status, MembershipStatus):
        return status.value
    return str(status)


def can_transition(current: str, target: str) -> bool:
    return status_value(target) in _TRANSITIONS.get(status_value(current), frozenset())


def assert_transition(current: str, target: str) -> None:
    if status_value(current) == status_value(target):
        return
    if not can_transition(current, target):
        raise LifecycleDenied("That membership transition is not allowed")


def assert_organization_accepts_members(organization: Organization) -> None:
    """Deleted and suspended organizations do not take new members."""
    if organization.status == "deleted":
        raise LifecycleDenied("A deleted organization cannot accept members")
    if organization.status in _BLOCK_NEW:
        raise LifecycleDenied("This organization is not accepting members")


def assert_organization_allows_mutation(organization: Organization) -> None:
    if organization.status in ("suspended", "deleted", "read_only"):
        raise LifecycleDenied("This organization is not accepting membership changes")


def authorizing_status(status: str) -> bool:
    return status_value(status) == MembershipStatus.ACTIVE.value


async def count_active_owners(
    session: AsyncSession, organization_id: uuid.UUID
) -> int:
    return (
        await session.execute(
            select(func.count(OrganizationMembership.id)).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.role == UserRole.OWNER,
                OrganizationMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one()


async def assert_not_last_owner(
    session: AsyncSession,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    *,
    next_role: UserRole | None,
    next_status: str | None,
) -> None:
    """Refuse a change that would leave the organization with no active owner."""
    if membership.role is not UserRole.OWNER:
        return
    if membership.status != MembershipStatus.ACTIVE.value:
        return
    loses_owner = False
    if next_role is not None and next_role is not UserRole.OWNER:
        loses_owner = True
    if next_status is not None and next_status != MembershipStatus.ACTIVE.value:
        loses_owner = True
    if not loses_owner:
        return
    if await count_active_owners(session, organization_id) <= 1:
        raise Conflict("An organization must keep at least one active owner")


def assert_can_grant(actor: User, target_role: UserRole) -> None:
    if not can_assign_role(actor.role, target_role):
        raise Forbidden("You cannot grant that role")


def assert_can_touch(actor: User, target_role: UserRole, *, self_target: bool) -> None:
    if self_target:
        raise Forbidden("You cannot change your own membership")
    if not can_manage_user(actor.role, target_role):
        raise Forbidden("You cannot modify a member at or above your level")


def assert_not_self_escalation(
    actor: User, target_user_id: uuid.UUID, target_role: UserRole
) -> None:
    if actor.id != target_user_id:
        return
    if role_level(target_role) > role_level(actor.role):
        raise Forbidden("You cannot grant yourself a higher role")
    raise Forbidden("You cannot change your own membership")
