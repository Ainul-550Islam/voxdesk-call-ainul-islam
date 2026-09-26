"""Compare-and-set helpers for inbox mutations.

The version column is the lock. A lost update raises conflict and changes nothing.
"""

from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboxThreadState
from app.tenancy.isolation import Conflict


async def compare_and_set(
    session: AsyncSession,
    row: InboxThreadState,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    values: dict,
) -> InboxThreadState:
    expected = row.version
    result = await session.execute(
        update(InboxThreadState)
        .where(
            InboxThreadState.id == row.id,
            InboxThreadState.tenant_id == tenant_id,
            InboxThreadState.environment_id == environment_id,
            InboxThreadState.version == expected,
        )
        .values(version=expected + 1, **values)
    )
    if result.rowcount != 1:
        raise Conflict("inbox row changed concurrently")
    await session.refresh(row)
    return row
