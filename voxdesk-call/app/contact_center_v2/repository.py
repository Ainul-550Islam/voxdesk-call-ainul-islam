"""Small tenant-scoped persistence primitives shared by Batch 04 services."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import NotFound

T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def owned(
    session: AsyncSession,
    model: type[T],
    object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> T:
    stmt = select(model).where(model.id == object_id, model.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound(model.__name__)
    return row


async def optional_owned(
    session: AsyncSession,
    model: type[T],
    object_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> T | None:
    return (
        await session.execute(select(model).where(model.id == object_id, model.tenant_id == tenant_id))
    ).scalar_one_or_none()


def tenant_query(model: type[T], tenant_id: uuid.UUID) -> Select[Any]:
    return select(model).where(model.tenant_id == tenant_id)


async def compare_and_set(
    session: AsyncSession,
    model: type[T],
    object_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    expected: dict[str, Any],
    values: dict[str, Any],
) -> bool:
    predicates = [model.id == object_id, model.tenant_id == tenant_id]
    for name, value in expected.items():
        column = getattr(model, name)
        predicates.append(column.is_(None) if value is None else column == value)
    result = await session.execute(
        update(model).where(*predicates).values(**values).execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def flush(session: AsyncSession, *rows: Any) -> None:
    for row in rows:
        session.add(row)
    await session.flush()
