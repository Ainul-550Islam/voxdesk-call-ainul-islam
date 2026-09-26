"""Resolve the effective environment for an authenticated request.

Sources, in order: an explicit route id, a server-side selection, then the
tenant's default environment. A raw id is loaded and then checked against the
tenant, the organization, the principal and the environment status. If nothing
is selected, callers that still only need the tenant keep working — this
function returns the default for inspection and does not require the caller to
switch.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import (
    AuditAction,
    Environment,
    EnvironmentSelection,
    Tenant,
    User,
)
from app.environments.guard import selection_allowed
from app.environments.membership import resolve
from app.organization.service import Actor
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied


async def _default_environment(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Environment | None:
    return (
        await session.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_id,
                Environment.is_default.is_(True),
            )
        )
    ).scalar_one_or_none()


async def _selection(
    session: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> EnvironmentSelection | None:
    return (
        await session.execute(
            select(EnvironmentSelection).where(
                EnvironmentSelection.user_id == user_id,
                EnvironmentSelection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def resolve_environment(
    session: AsyncSession,
    *,
    user: User,
    tenant: Tenant,
    environment_id: uuid.UUID | None = None,
) -> Environment | None:
    """The environment this request may use, or None when the tenant has none.

    A supplied id outside the tenant is a boundary miss, not a fallback.
    """
    if tenant.organization_id is None:
        raise BoundaryDenied()
    if environment_id is not None:
        environment = await session.get(Environment, environment_id)
        if environment is None or environment.tenant_id != tenant.id:
            raise BoundaryDenied()
        decision = await resolve(session, user, environment, tenant)
        if decision.via == "boundary" or not decision.allowed and decision.reason == "cross_tenant":
            raise BoundaryDenied()
        return environment
    selected = await _selection(session, user.id, tenant.id)
    if selected is not None:
        environment = await session.get(Environment, selected.environment_id)
        if (
            environment is not None
            and environment.tenant_id == tenant.id
            and selection_allowed(environment)
        ):
            return environment
    return await _default_environment(session, tenant.id)


async def select_environment(
    session: AsyncSession,
    *,
    user: User,
    tenant: Tenant,
    environment_id: uuid.UUID,
    actor: Actor | None = None,
) -> Environment:
    environment = await session.get(Environment, environment_id)
    if environment is None or environment.tenant_id != tenant.id:
        raise BoundaryDenied()
    decision = await resolve(session, user, environment, tenant)
    if not decision.allowed:
        if decision.via == "boundary":
            raise BoundaryDenied()
        raise LifecycleDenied("That environment cannot be selected")
    if not selection_allowed(environment):
        raise LifecycleDenied("An archived or suspended environment cannot be current")
    row = await _selection(session, user.id, tenant.id)
    if row is None:
        row = EnvironmentSelection(
            user_id=user.id, tenant_id=tenant.id, environment_id=environment.id,
        )
        session.add(row)
    else:
        row.environment_id = environment.id
    who = actor or Actor(user_id=user.id, email=user.email, tenant_id=tenant.id)
    await emit(
        session,
        AuditAction.ENVIRONMENT_SELECTED,
        tenant_id=tenant.id,
        actor_user_id=who.user_id,
        actor_email=who.email,
        ip_address=who.ip_address,
        user_agent=who.user_agent,
        detail={
            "environment_id": str(environment.id),
            "tenant_id": str(tenant.id),
            "kind": environment.kind,
        },
        commit=False,
    )
    await session.commit()
    await session.refresh(environment)
    return environment
