"""Hierarchy context derived from the authenticated tenant, never from a claim.

``organization_id`` is not a JWT field. Old access tokens keep working because
nothing here reads a new claim; the parent organization is loaded from the
tenant row the existing authenticator already resolved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.db.models import Environment, Organization
from app.tenancy.isolation import BoundaryDenied, NotFound


@dataclass(frozen=True)
class HierarchyContext:
    organization_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    organization_status: str
    tenant_status: str
    auth_method: str
    is_machine: bool
    #: Set only when the caller named an environment and it belongs to this tenant.
    environment_id: uuid.UUID | None = None
    environment_kind: str | None = None
    environment_status: str | None = None
    #: Server-resolved production/default row. Omitted from tokens and from any
    #: client-supplied field. Present so a legacy caller can discover it.
    default_environment_id: uuid.UUID | None = None


async def load_hierarchy(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    environment_id: uuid.UUID | None = None,
) -> HierarchyContext:
    tenant = ctx.tenant
    if tenant.organization_id is None:
        raise NotFound()
    organization = await session.get(Organization, tenant.organization_id)
    if organization is None:
        raise NotFound()
    default_id = await session.scalar(
        select(Environment.id).where(
            Environment.tenant_id == tenant.id,
            Environment.is_default.is_(True),
        )
    )
    resolved_id: uuid.UUID | None = None
    kind: str | None = None
    env_status: str | None = None
    if environment_id is not None:
        environment = await session.get(Environment, environment_id)
        if environment is None or environment.tenant_id != tenant.id:
            raise BoundaryDenied()
        resolved_id = environment.id
        kind = environment.kind
        env_status = environment.status
    return HierarchyContext(
        organization_id=organization.id,
        tenant_id=tenant.id,
        user_id=ctx.user_id,
        organization_status=organization.status,
        tenant_status=tenant.lifecycle_status,
        auth_method=ctx.auth_method,
        is_machine=ctx.is_machine,
        environment_id=resolved_id,
        environment_kind=kind,
        environment_status=env_status,
        default_environment_id=default_id,
    )


def reject_client_override(ctx: TenantContext, claimed_tenant_id: uuid.UUID | None) -> uuid.UUID:
    """A body or query tenant id never replaces the authenticated tenant.

    A missing claim is ignored. A disagreeing claim is a boundary miss, not a
    switch and not a 403 that would confirm the other tenant exists.
    """
    if claimed_tenant_id is not None and claimed_tenant_id != ctx.tenant_id:
        raise BoundaryDenied()
    return ctx.tenant_id
