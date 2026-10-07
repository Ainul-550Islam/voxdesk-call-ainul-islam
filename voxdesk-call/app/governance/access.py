"""Governance authorization using the application's existing principal context."""

from __future__ import annotations

import uuid

from app.auth.dependencies import TenantContext
from app.auth.permissions import Permission
from app.tenancy.isolation import Forbidden, require_same_tenant

from .context import GovernanceScope


def require_permission(ctx: TenantContext, permission: Permission) -> None:
    if not ctx.can(permission):
        raise Forbidden("Governance permission is required")


def require_scope(ctx: TenantContext, scope: GovernanceScope, tenant_id: uuid.UUID | None = None) -> None:
    if tenant_id is not None:
        require_same_tenant(ctx.tenant, tenant_id)
    if scope.tenant_id != ctx.tenant_id or scope.organization_id != ctx.tenant.organization_id:
        raise Forbidden("Governance scope is not authorized")


def require_runtime_access(ctx: TenantContext) -> None:
    """Runtime governance checks still use normal tenant read permission."""
    require_permission(ctx, Permission.GOVERNANCE_READ)
