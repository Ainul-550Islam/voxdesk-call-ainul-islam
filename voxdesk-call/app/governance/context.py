"""Resolve a governance scope from the existing authenticated hierarchy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.db.models import Environment, Organization, Tenant
from app.environments.access import resolve as resolve_environment_access
from app.tenancy.isolation import BoundaryDenied, Forbidden, NotFound


@dataclass(frozen=True)
class GovernanceScope:
    tenant: Tenant
    organization: Organization
    environment: Environment | None = None

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def organization_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def environment_id(self) -> uuid.UUID | None:
        return self.environment.id if self.environment else None


async def resolve_scope(
    session: AsyncSession,
    ctx: TenantContext,
    environment_id: uuid.UUID | None = None,
) -> GovernanceScope:
    """Load parent rows and reject an environment that is not this tenant's.

    A supplied environment id is never treated as an authorization boundary by
    itself. It is only an additional child selector under the authenticated
    tenant and organization.
    """
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant is None or tenant.id != ctx.tenant_id:
        raise NotFound()
    if tenant.organization_id is None:
        raise BoundaryDenied()
    organization = await session.get(Organization, tenant.organization_id)
    if organization is None:
        raise NotFound()
    environment = None
    if environment_id is not None:
        environment = await session.scalar(
            select(Environment).where(
                Environment.id == environment_id,
                Environment.tenant_id == tenant.id,
            )
        )
        if environment is None:
            raise BoundaryDenied()
        access = await resolve_environment_access(session, ctx.user, environment, tenant)
        if not access.allowed:
            if access.reason == "cross_tenant":
                raise BoundaryDenied()
            raise Forbidden("Environment access is not authorized")
    return GovernanceScope(tenant=tenant, organization=organization, environment=environment)
