"""Durable inbox state. Queries always name tenant and environment."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboxThreadState


async def get_thread(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
) -> InboxThreadState | None:
    return (
        await session.execute(
            select(InboxThreadState).where(
                InboxThreadState.tenant_id == tenant_id,
                InboxThreadState.environment_id == environment_id,
                InboxThreadState.call_id == call_id,
            )
        )
    ).scalar_one_or_none()


async def require_thread(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
) -> InboxThreadState:
    row = await get_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )
    if row is None:
        from app.tenancy.isolation import BoundaryDenied

        raise BoundaryDenied()
    return row
