"""Organization authorization.

Every decision delegates to the existing permission vocabulary. A principal
outside the organization gets the same answer as a missing organization, so
the caller cannot learn that the other organization exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.organization import permissions as organization_permissions
from app.organization.membership_service import (
    effective_role,
    organization_for_user,
)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    code: str
    reason: str

    @property
    def boundary(self) -> bool:
        return self.code == "boundary"


def _allow(reason: str = "allowed") -> AccessDecision:
    return AccessDecision(True, "allowed", reason)


def _deny(reason: str) -> AccessDecision:
    return AccessDecision(False, "denied", reason)


def _boundary() -> AccessDecision:
    return AccessDecision(False, "boundary", "not_found")


async def _in_organization(
    session: AsyncSession, user: User | None, organization_id: uuid.UUID
) -> bool:
    if user is None:
        return False
    current = await organization_for_user(session, user)
    return current is not None and current.id == organization_id


async def _decide(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    if user is None:
        return _deny("unauthenticated")
    if not await _in_organization(session, user, organization_id):
        return _boundary()
    from app.organization.membership_service import get_membership
    row = await get_membership(session, organization_id, user.id)
    if row is not None and row.status in ("revoked", "expired"):
        return _deny("membership_inactive")
    status = membership_status
    if row is not None and row.status == "suspended":
        status = "suspended"
    role = await effective_role(session, user, organization_id)
    if role is None:
        return _deny("membership_inactive")
    if organization_permissions.allows(
        role, conceptual, scopes=scopes, membership_status=status
    ):
        return _allow()
    return _deny("permission_denied")


async def can_read_organization(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:read",
        scopes=scopes, membership_status=membership_status,
    )


async def can_update_organization(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:update",
        scopes=scopes, membership_status=membership_status,
    )


async def can_manage_members(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:members:invite",
        scopes=scopes, membership_status=membership_status,
    )


async def can_create_tenant(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    """Still the platform-only permission. No customer role holds it."""
    return await _decide(
        session, user, organization_id, "organization:tenants:create",
        scopes=scopes, membership_status=membership_status,
    )


async def can_suspend_tenant(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:tenants:suspend",
        scopes=scopes, membership_status=membership_status,
    )
