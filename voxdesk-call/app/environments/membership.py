"""Environment access bindings and inheritance.

Precedence, highest first:

1. An explicit revoked, expired or suspended binding denies. Suspended denies
   privileged environment actions; revoked denies all of them.
2. An explicit active environment membership is the role, capped so it cannot
   outrank the tenant role.
3. An active tenant membership is inherited when no explicit binding exists.
4. An active organization owner or admin is inherited when the tenant belongs
   to that organization and the user has no tenant row.
5. Otherwise the caller is unauthenticated for this environment.

"Most permissions wins" is intentionally not the rule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import role_level
from app.db.models import (
    Environment,
    EnvironmentMembership,
    MembershipStatus,
    Tenant,
    User,
    UserRole,
)
from app.tenancy.membership_service import effective_tenant_role, get_membership


@dataclass(frozen=True)
class EnvironmentAccess:
    allowed: bool
    role: UserRole | None
    via: str
    status: str
    reason: str


def _deny(reason: str, via: str = "denied") -> EnvironmentAccess:
    return EnvironmentAccess(False, None, via, "denied", reason)


def _cap(explicit: UserRole, ceiling: UserRole) -> UserRole:
    """An explicit binding may be stricter. It may not escalate."""
    if role_level(explicit) <= role_level(ceiling):
        return explicit
    return ceiling


async def binding_for(
    session: AsyncSession, environment_id: uuid.UUID, user_id: uuid.UUID
) -> EnvironmentMembership | None:
    return (
        await session.execute(
            select(EnvironmentMembership).where(
                EnvironmentMembership.environment_id == environment_id,
                EnvironmentMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def resolve(
    session: AsyncSession, user: User | None, environment: Environment, tenant: Tenant
) -> EnvironmentAccess:
    if user is None:
        return _deny("unauthenticated", "unauthenticated")
    if environment.tenant_id != tenant.id:
        return _deny("cross_tenant", "boundary")
    explicit = await binding_for(session, environment.id, user.id)
    tenant_role = await effective_tenant_role(session, user, tenant)
    tenant_row = await get_membership(session, tenant.id, user.id)
    if tenant_row is not None and tenant_row.status in (
        MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value,
    ):
        return _deny("tenant_membership_revoked", "tenant")
    if explicit is not None and explicit.status in (
        MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value,
    ):
        return _deny("environment_membership_revoked", "environment")
    if explicit is not None and explicit.status == MembershipStatus.SUSPENDED.value:
        return EnvironmentAccess(
            False, explicit.role, "environment", "suspended", "environment_membership_suspended"
        )
    if tenant_row is not None and tenant_row.status == MembershipStatus.SUSPENDED.value:
        return EnvironmentAccess(
            False, tenant_row.role, "tenant", "suspended", "tenant_membership_suspended"
        )
    if tenant_role is None:
        return _deny("no_membership", "unauthenticated")
    if explicit is not None and explicit.status == MembershipStatus.ACTIVE.value:
        return EnvironmentAccess(
            True, _cap(explicit.role, tenant_role), "environment", "active", "explicit"
        )
    return EnvironmentAccess(True, tenant_role, "tenant", "active", "inherited")
