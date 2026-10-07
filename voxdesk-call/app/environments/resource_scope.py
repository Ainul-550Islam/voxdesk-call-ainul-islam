"""Resolve which environment a resource operation is allowed to use.

Priority is an authorized explicit environment, then the caller's selected
environment, then the tenant's active default production environment. A missing
safe environment is a rejection. This module never inserts an environment.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, EnvironmentSelection
from app.environments.resource_binding import assert_environment_accepts_write, assert_environment_belongs_to_tenant
from app.resources.exceptions import ResourceScopeError
from app.tenancy.isolation import BoundaryDenied


async def load_environment(
    session: AsyncSession, environment_id: uuid.UUID, tenant_id: uuid.UUID
) -> Environment:
    environment = await session.get(Environment, environment_id)
    return assert_environment_belongs_to_tenant(environment, tenant_id)


async def selected_environment(
    session: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> Environment | None:
    selection = (
        await session.execute(
            select(EnvironmentSelection).where(
                EnvironmentSelection.user_id == user_id,
                EnvironmentSelection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if selection is None:
        return None
    environment = await session.get(Environment, selection.environment_id)
    if environment is None or environment.tenant_id != tenant_id:
        return None
    if environment.status != "active":
        return None
    return environment


async def default_production(
    session: AsyncSession, tenant_id: uuid.UUID, *, require_active: bool = True
) -> Environment | None:
    rows = (
        await session.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_id,
                Environment.kind == "production",
            ).order_by(Environment.is_default.desc())
        )
    ).scalars().all()
    for environment in rows:
        if require_active and environment.status != "active":
            continue
        return environment
    return None


async def resolve_scope(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    explicit_environment_id: uuid.UUID | None = None,
    for_write: bool = False,
) -> Environment:
    """Pick the only environment this operation may use."""
    if explicit_environment_id is not None:
        environment = await load_environment(session, explicit_environment_id, tenant_id)
    elif user_id is not None and (selected := await selected_environment(
        session, user_id=user_id, tenant_id=tenant_id
    )) is not None:
        environment = selected
    else:
        environment = await default_production(session, tenant_id, require_active=True)
    if environment is None:
        raise ResourceScopeError("No safe environment is available")
    if environment.tenant_id != tenant_id:
        raise BoundaryDenied()
    if for_write:
        assert_environment_accepts_write(environment)
    return environment


async def legacy_production_id(session: AsyncSession, tenant_id) -> uuid.UUID | None:
    environment = await default_production(session, tenant_id, require_active=True)
    return None if environment is None else environment.id
