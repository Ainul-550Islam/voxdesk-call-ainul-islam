"""Organization membership derived from the current single-tenant user.

There is no organization-membership table. A principal belongs to the
organization that owns ``user.tenant_id``. A machine credential belongs to the
same organization as the human it is attributed to, and its scopes can only
narrow that access.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import Tenant, User, UserRole


def effective_role(user: User) -> UserRole:
    """The only role this foundation recognises: the role already on the user."""
    return user.role


def user_belongs_to_tenant(user: User, tenant_id: uuid.UUID) -> bool:
    return user.tenant_id == tenant_id


async def organization_id_for_user(session: AsyncSession, user: User) -> uuid.UUID | None:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        return None
    return tenant.organization_id


async def user_belongs_to_organization(
    session: AsyncSession, user: User, organization_id: uuid.UUID
) -> bool:
    current = await organization_id_for_user(session, user)
    return current is not None and current == organization_id


def role_allows(user: User, permission: Permission) -> bool:
    return has_permission(user.role, permission)
