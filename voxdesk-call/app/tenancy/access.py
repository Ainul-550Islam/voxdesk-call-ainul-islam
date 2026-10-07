"""Tenant authorization boundary.

The check is always organization, then tenant, then the existing permission.
A tenant in another organization is indistinguishable from a missing tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, User
from app.organization.membership_service import organization_for_user
from app.tenancy import permissions as tenant_permissions
from app.tenancy.membership_service import effective_tenant_role


@dataclass(frozen=True)
class TenantAccess:
    allowed: bool
    code: str
    reason: str

    @property
    def boundary(self) -> bool:
        return self.code == "boundary"


async def load_tenant_in_organization(
    session: AsyncSession, actor: User | None, tenant_id: uuid.UUID
) -> Tenant | None:
    """The tenant, only when it belongs to the actor's organization."""
    if actor is None:
        return None
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.organization_id is None:
        return None
    actor_org = await organization_for_user(session, actor)
    if actor_org is None or actor_org.id != tenant.organization_id:
        return None
    return tenant


async def verify_operation(
    session: AsyncSession,
    actor: User | None,
    tenant_id: uuid.UUID,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> TenantAccess:
    if actor is None:
        return TenantAccess(False, "denied", "unauthenticated")
    tenant = await load_tenant_in_organization(session, actor, tenant_id)
    if tenant is None:
        return TenantAccess(False, "boundary", "not_found")
    if tenant.lifecycle_status == "deleted":
        return TenantAccess(False, "denied", "tenant_deleted")
    role = await effective_tenant_role(session, actor, tenant)
    if role is None:
        return TenantAccess(False, "denied", "membership_inactive")
    if not tenant_permissions.allows(
        role, conceptual, scopes=scopes, membership_status=membership_status
    ):
        return TenantAccess(False, "denied", "permission_denied")
    if tenant.lifecycle_status in ("suspended", "read_only") and conceptual not in (
        "tenant:read",
        "tenant:members:read",
        "tenant:environments:read",
    ):
        return TenantAccess(False, "denied", "tenant_not_writable")
    return TenantAccess(True, "allowed", "allowed")


async def environment_boundary(
    session: AsyncSession,
    actor: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
) -> TenantAccess:
    """Read access, then the environment must belong to that same tenant."""
    from app.db.models import Environment

    decision = await verify_operation(session, actor, tenant_id, "tenant:environments:read")
    if not decision.allowed:
        return decision
    row = await session.get(Environment, environment_id)
    if row is None or row.tenant_id != tenant_id:
        return TenantAccess(False, "boundary", "not_found")
    return TenantAccess(True, "allowed", "allowed")
