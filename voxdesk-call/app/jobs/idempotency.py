"""Database-backed idempotency for asynchronous work.

A duplicate key returns the existing record. Redis TTL is not the lock.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobIdempotency


async def begin(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str
) -> tuple[JobIdempotency, bool]:
    """Insert an in-progress key. The second return value is True when new."""
    row = JobIdempotency(tenant_id=tenant_id, idempotency_key=key, status="in_progress")
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = await get(session, tenant_id=tenant_id, key=key)
        if found is None:
            raise
        return found, False
    return row, True


async def get(session: AsyncSession, *, tenant_id: uuid.UUID, key: str) -> JobIdempotency | None:
    return (
        await session.execute(
            select(JobIdempotency).where(
                JobIdempotency.tenant_id == tenant_id,
                JobIdempotency.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


async def complete(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str, result_ref: str
) -> JobIdempotency:
    row = await get(session, tenant_id=tenant_id, key=key)
    if row is None:
        raise KeyError("idempotency key not found")
    if row.status == "completed":
        return row
    row.status = "completed"
    row.result_ref = result_ref[:128]
    await session.flush()
    return row
