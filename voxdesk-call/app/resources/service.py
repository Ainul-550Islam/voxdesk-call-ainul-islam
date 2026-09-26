"""Create and read resources inside an already authorized environment.

``environment_id`` is set once, at insert. Rebinding is refused.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, Lead, User
from app.environments.resource_binding import ImmutableEnvironment
from app.environments.resource_scope import resolve_scope
from app.environments.resource_types import EnvironmentResourceType
from app.resources.exceptions import InvalidResourceTransition
from app.resources.registry import spec_for
from app.resources.repository import get_resource, list_resources


async def resolve_for_legacy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    explicit_environment_id: uuid.UUID | None = None,
    for_write: bool = True,
) -> Environment:
    return await resolve_scope(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        explicit_environment_id=explicit_environment_id,
        for_write=for_write,
    )


async def bind_new_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment: Environment,
    name: str,
    phone: str,
    email: str | None = None,
) -> Lead:
    if environment.tenant_id != tenant_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    lead = Lead(
        tenant_id=tenant_id,
        environment_id=environment.id,
        name=name[:200],
        phone=phone[:32],
        email=email,
    )
    session.add(lead)
    await session.flush()
    return lead


def refuse_rebind(resource, environment_id: uuid.UUID) -> None:
    current = getattr(resource, "environment_id", None)
    if current is not None and str(current) != str(environment_id):
        raise ImmutableEnvironment()


async def read_one(
    session: AsyncSession,
    resource_type: EnvironmentResourceType,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_id: str,
):
    return await get_resource(
        session,
        resource_type,
        tenant_id=tenant_id,
        environment_id=environment_id,
        resource_id=resource_id,
    )


async def read_page(
    session: AsyncSession,
    resource_type: EnvironmentResourceType,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    limit: int,
    offset: int,
):
    spec = spec_for(resource_type)
    if not spec.environment_scoped:
        raise InvalidResourceTransition("That resource is not environment-scoped")
    return await list_resources(
        session,
        resource_type,
        tenant_id=tenant_id,
        environment_id=environment_id,
        limit=limit,
        offset=offset,
    )


def actor_user_id(user: User | None) -> uuid.UUID | None:
    return None if user is None else user.id
