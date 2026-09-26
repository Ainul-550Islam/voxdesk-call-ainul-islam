"""Persistence reads. Every query names the parent id.

There is no unscoped list. A miss and a foreign id both return None so the
caller can render the same 404.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, Organization, Tenant


async def organization(session: AsyncSession, organization_id: uuid.UUID) -> Organization | None:
    return await session.get(Organization, organization_id)


async def tenants_in_organization(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[Tenant]:
    rows = await session.scalars(
        select(Tenant).where(Tenant.organization_id == organization_id).order_by(Tenant.created_at)
    )
    return list(rows)


async def environments_in_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> list[Environment]:
    rows = await session.scalars(
        select(Environment)
        .where(Environment.tenant_id == tenant_id)
        .order_by(Environment.created_at)
    )
    return list(rows)


async def environment_in_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, environment_id: uuid.UUID
) -> Environment | None:
    row = await session.get(Environment, environment_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row
