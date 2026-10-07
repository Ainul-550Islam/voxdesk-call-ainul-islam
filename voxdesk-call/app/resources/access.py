"""Resource authorization: hierarchy, environment policy, then existing RBAC."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import Environment, Tenant, User
from app.environments.access import can_read_environment
from app.environments.resource_policy import assert_read, assert_write
from app.environments.resource_scope import load_environment
from app.environments.resource_types import EnvironmentResourceType
from app.resources.exceptions import ResourceUnauthorized
from app.resources.registry import spec_for
from app.tenancy.access import load_tenant_in_organization


async def authorize(
    session: AsyncSession,
    user: User | None,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_type: EnvironmentResourceType,
    action: str,
    scopes: frozenset[str] | None = None,
) -> tuple[Tenant, Environment]:
    tenant = await load_tenant_in_organization(session, user, tenant_id)
    if tenant is None or user is None:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    environment = await load_environment(session, environment_id, tenant.id)
    _environment, decision = await can_read_environment(
        session, user, tenant.id, environment.id, scopes=scopes,
    )
    if not decision.allowed:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    spec = spec_for(resource_type)
    permission_name = spec.write_permission if action != "read" else spec.read_permission
    permission = Permission(permission_name)
    allowed = has_permission(user.role, permission)
    if scopes is not None and permission.value not in scopes:
        allowed = False
    if action == "read":
        assert_read(environment, user.role, rbac_allows=allowed)
    else:
        assert_write(environment, user.role, action=action, rbac_allows=allowed)
    return tenant, environment


def require_known_type(name: str) -> EnvironmentResourceType:
    from app.environments.resource_types import EnvironmentResourceType as Type
    try:
        return Type((name or "").strip().lower().replace("-", "_"))
    except ValueError as exc:
        raise ResourceUnauthorized("Unsupported resource") from exc
