"""Tenant quota lookup.

This module normalizes a tenant's place in the hierarchy and asks the central
resolver for the effective limit. It does not enforce, and it does not invent
a number when neither the hierarchy nor billing has one.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant
from app.quotas.models import QuotaKey
from app.quotas.service import resolve_quota


async def tenant_quota(
    session: AsyncSession,
    tenant: Tenant,
    key: QuotaKey | str,
    *,
    environment_id: uuid.UUID | None = None,
    used: int | None = None,
):
    """Effective limit for one tenant. Billing remains a source, not a victim."""
    return await resolve_quota(
        session,
        key,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        environment_id=environment_id,
        tenant=tenant,
        used=used,
    )


async def compare_and_reserve(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    counter: str,
    limit: int,
    adding: int = 1,
) -> bool:
    """Admit ``adding`` units with one UPDATE ... WHERE.

    The allowlist is a non-billing integer. ``minutes_used`` and
    ``included_minutes`` are not on it: voice metering stays in billing.
    No route calls this. A limit of N cannot become N+1 because the predicate
    is in the same statement as the write.
    """
    from sqlalchemy import text, update
    from sqlalchemy.exc import OperationalError

    from app.tenancy.exceptions import LimitDenied, ValidationFailed

    columns = {"max_call_attempts": Tenant.max_call_attempts}
    column = columns.get(counter)
    if column is None:
        raise ValidationFailed("Unknown quota counter")
    if limit < 0 or adding < 0:
        raise LimitDenied("Quota values cannot be negative")
    if adding == 0:
        return True
    bind = session.bind
    if bind is not None and bind.dialect.name == "sqlite":
        await session.execute(text("PRAGMA busy_timeout = 5000"))
    statement = (
        update(Tenant)
        .where(Tenant.id == tenant_id, column + adding <= limit)
        .values({counter: column + adding})
    )
    for attempt in range(3):
        try:
            result = await session.execute(statement)
            return result.rowcount == 1
        except OperationalError:
            await session.rollback()
            if attempt == 2:
                raise
    return False
