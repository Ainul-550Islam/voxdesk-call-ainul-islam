"""Environment authorization.

Every operation loads the environment, proves it belongs to the tenant, proves
the tenant belongs to the actor's organization, then applies the membership
precedence in ``membership.py``. Permission checks still go through the
existing RBAC vocabulary.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, User
from app.environments.membership import EnvironmentAccess, resolve
from app.tenancy.access import load_tenant_in_organization
from app.tenancy.permissions import allows


async def _environment_for_actor(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
) -> tuple[Environment | None, EnvironmentAccess]:
    tenant = await load_tenant_in_organization(session, user, tenant_id)
    if tenant is None or user is None:
        return None, EnvironmentAccess(False, None, "boundary", "denied", "not_found")
    environment = await session.get(Environment, environment_id)
    if environment is None or environment.tenant_id != tenant.id:
        return None, EnvironmentAccess(False, None, "boundary", "denied", "not_found")
    decision = await resolve(session, user, environment, tenant)
    return environment, decision


def _permitted(
    decision: EnvironmentAccess, conceptual: str, *, scopes: frozenset[str] | None
) -> EnvironmentAccess:
    if not decision.allowed or decision.role is None:
        return decision
    if not allows(decision.role, conceptual, scopes=scopes, membership_status=decision.status):
        return EnvironmentAccess(
            False, decision.role, decision.via, decision.status, "permission_denied"
        )
    return decision


async def can_read_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await _environment_for_actor(
        session, user, tenant_id, environment_id
    )
    return environment, _permitted(decision, "tenant:environments:read", scopes=scopes)


async def can_update_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await _environment_for_actor(
        session, user, tenant_id, environment_id
    )
    if environment is not None and environment.status in ("suspended", "archived"):
        return environment, EnvironmentAccess(
            False, decision.role, decision.via, environment.status, "environment_blocked"
        )
    return environment, _permitted(decision, "tenant:environments:update", scopes=scopes)


async def can_archive_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await can_update_environment(
        session, user, tenant_id, environment_id, scopes=scopes
    )
    if environment is None or not decision.allowed:
        return environment, decision
    from app.environments.guard import mutation_allowed
    if not mutation_allowed(environment, decision.role, action="archive"):
        return environment, EnvironmentAccess(
            False, decision.role, "guard", decision.status, "production_protected"
        )
    return environment, decision


async def can_change_default_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await can_update_environment(
        session, user, tenant_id, environment_id, scopes=scopes
    )
    if environment is None or not decision.allowed:
        return environment, decision
    from app.environments.guard import mutation_allowed
    if not mutation_allowed(environment, decision.role, action="default"):
        return environment, EnvironmentAccess(
            False, decision.role, "guard", decision.status, "production_protected"
        )
    return environment, decision


async def can_deploy_environment_metadata(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    """Metadata only. This does not execute a deployment."""
    environment, decision = await can_update_environment(
        session, user, tenant_id, environment_id, scopes=scopes
    )
    if environment is None or not decision.allowed:
        return environment, decision
    from app.environments.guard import mutation_allowed
    if not mutation_allowed(environment, decision.role, action="deploy"):
        return environment, EnvironmentAccess(
            False, decision.role, "guard", decision.status, "production_protected"
        )
    return environment, decision
