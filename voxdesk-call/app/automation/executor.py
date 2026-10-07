"""Environment-aware automation execution.

A staging automation cannot mutate a production resource, and the reverse is
also rejected. Scope is taken from the stored automation and the target row,
never from an unverified client field alone.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.durable_repository import get_definition
from app.db.models import Environment
from app.jobs.models import PermanentJobError
from app.tenancy.isolation import BoundaryDenied


async def resolve_execution(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    automation_id: str,
    target_environment_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
) -> Environment:
    automation = await get_definition(session, tenant_id=tenant_id, automation_id=automation_id)
    if automation is None:
        raise PermanentJobError("not_found", "automation not found")
    if organization_id is not None and automation.tenant_id != tenant_id:
        raise BoundaryDenied()
    target = await session.get(Environment, target_environment_id)
    source = await session.get(Environment, automation.environment_id)
    if (
        target is None
        or source is None
        or target.tenant_id != tenant_id
        or source.tenant_id != tenant_id
    ):
        raise BoundaryDenied()
    if source.id != target.id or source.kind != target.kind:
        raise PermanentJobError(
            "environment_mismatch", "automation and resource environments differ"
        )
    return target
