"""Build a resource context from the authenticated tenant and a loaded environment.

Client JSON is never a source for these ids. The caller must already have
authorized the tenant and the environment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.db.models import Environment, Tenant, User


@dataclass(frozen=True)
class ResourceContext:
    organization_id: uuid.UUID | None
    tenant_id: uuid.UUID
    environment_id: uuid.UUID
    environment_kind: str
    environment_status: str
    user_id: uuid.UUID | None
    role: str | None


def from_authorized(
    tenant: Tenant,
    environment: Environment,
    user: User | None,
) -> ResourceContext:
    if environment.tenant_id != tenant.id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    return ResourceContext(
        organization_id=getattr(tenant, "organization_id", None),
        tenant_id=tenant.id,
        environment_id=environment.id,
        environment_kind=environment.kind,
        environment_status=environment.status,
        user_id=None if user is None else user.id,
        role=None if user is None else user.role.value,
    )
