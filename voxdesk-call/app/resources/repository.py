"""Tenant-and-environment queries for the eight registered resources.

Every statement names both columns. An environment id alone is never a filter.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.environments.resource_queries import tenant_and_environment_scope, tenant_scope
from app.environments.resource_types import EnvironmentResourceType
from app.resources.registry import model_for


def _coerce_id(model, resource_id: str):
    column = model.id
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        python_type = str
    if python_type is uuid.UUID:
        return uuid.UUID(str(resource_id))
    return str(resource_id)


async def get_resource(
    session: AsyncSession,
    resource_type: EnvironmentResourceType,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_id: str,
):
    model = model_for(resource_type)
    try:
        identifier = _coerce_id(model, resource_id)
    except (ValueError, TypeError):
        return None
    statement = select(model).where(
        tenant_and_environment_scope(model, tenant_id, environment_id),
        model.id == identifier,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def list_resources(
    session: AsyncSession,
    resource_type: EnvironmentResourceType,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    model = model_for(resource_type)
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    predicate = tenant_scope(model, tenant_id)
    if environment_id is not None:
        predicate = tenant_and_environment_scope(model, tenant_id, environment_id)
    total = int((await session.execute(
        select(func.count()).select_from(model).where(predicate)
    )).scalar_one())
    order = model.created_at.desc() if hasattr(model, "created_at") else model.id
    rows = (
        await session.execute(
            select(model).where(predicate).order_by(order).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), total


async def add_resource(session: AsyncSession, resource) -> None:
    session.add(resource)
    await session.flush()
