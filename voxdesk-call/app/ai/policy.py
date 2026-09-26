"""Effective tenant policy for a governed call.

Loads the existing policy row and the existing preset selection. A client
model name cannot replace that selection. A client tenant id cannot switch
the tenant.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.tenancy.isolation import BoundaryDenied
from app.tenancy.policy import bind_tenant


async def effective(
    session: AsyncSession,
    tenant,
    *,
    environment_kind: str,
    claimed_tenant_id: uuid.UUID | None = None,
    requested_preset: str | None = None,
    ctx: TenantContext | None = None,
):
    """Return ``(policy, choice)``. Raises rather than switching tenant or model."""
    if ctx is not None:
        bind_tenant(ctx, ctx.tenant_id, claimed_tenant_id)
        tenant = ctx.tenant
    elif claimed_tenant_id is not None and claimed_tenant_id != tenant.id:
        raise BoundaryDenied()
    from app.ai.gateway import load_policy
    from app.ai.routing import select_for_runtime

    policy = await load_policy(session, tenant.id)
    choice = select_for_runtime(
        tenant,
        policy,
        environment_kind=environment_kind,
        requested_preset=requested_preset,
    )
    return policy, choice
