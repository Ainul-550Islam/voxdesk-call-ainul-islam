"""FastAPI dependencies for governance operations."""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session

from .context import GovernanceScope, resolve_scope


def require_governance_read():
    return Depends(require_permission(Permission.GOVERNANCE_READ))


def require_governance_write():
    return Depends(require_permission(Permission.GOVERNANCE_WRITE))


def require_governance_approval():
    return Depends(require_permission(Permission.GOVERNANCE_APPROVE))


async def governance_scope(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
) -> GovernanceScope:
    if ctx.tenant_id != tenant_id:
        from app.tenancy.isolation import BoundaryDenied

        raise BoundaryDenied()
    return await resolve_scope(session, ctx)
