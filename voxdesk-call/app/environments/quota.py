"""Environment quota resolution.

Limits are read from the central quota service. This module does not hard-code
a number and does not treat a missing row as zero.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, Tenant
from app.quotas.models import QuotaKey
from app.quotas.service import resolve_quota


async def environment_quota(
    session: AsyncSession,
    tenant: Tenant,
    environment: Environment,
    key: QuotaKey | str,
    *,
    used: int | None = None,
):
    if environment.tenant_id != tenant.id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    return await resolve_quota(
        session,
        key,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        environment_id=environment.id,
        tenant=tenant,
        used=used,
    )
