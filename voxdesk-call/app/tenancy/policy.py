"""One authorization path for organization, tenant and environment.

The authenticated tenant wins. A body id that disagrees is a boundary miss,
not a switch. Permission is checked only after the boundary, so a foreign id
stays a 404.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.auth.permissions import Permission
from app.db.models import Environment
from app.tenancy.context import reject_client_override
from app.tenancy.exceptions import BoundaryDenied, Forbidden
from app.tenancy.isolation import require_same_organization, require_same_tenant


def bind_tenant(
    ctx: TenantContext,
    tenant_id: uuid.UUID,
    claimed_tenant_id: uuid.UUID | None = None,
) -> None:
    """Path id must be the authenticated tenant. A disagreeing claim is a miss."""
    reject_client_override(ctx, claimed_tenant_id)
    require_same_tenant(ctx.tenant, tenant_id)


def bind_organization(ctx: TenantContext, organization_id: uuid.UUID) -> None:
    require_same_organization(ctx.tenant, organization_id)


def require_permission(ctx: TenantContext, permission: Permission) -> None:
    if not ctx.can(permission):
        raise Forbidden()


async def authorize_environment(
    session: AsyncSession,
    ctx: TenantContext,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    permission: Permission,
    *,
    claimed_tenant_id: uuid.UUID | None = None,
) -> Environment:
    """The environment, only when it belongs to the authenticated tenant."""
    bind_tenant(ctx, tenant_id, claimed_tenant_id)
    require_permission(ctx, permission)
    row = await session.get(Environment, environment_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise BoundaryDenied()
    return row
