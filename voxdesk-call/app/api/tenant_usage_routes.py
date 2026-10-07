"""Tenant usage reads. Billing stays the meter.

This route resolves a limit through the existing quota service. It does not
set a price, a plan, or a Stripe quantity.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.quotas.models import QuotaKey
from app.tenancy.exceptions import HierarchyError
from app.tenancy.limits import inspect
from app.tenancy.isolation import to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["tenant-usage"])


class UsageCheck(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    used: int | None = None
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


@router.get("/usage")
async def usage_summary(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """One decision per known key. Unknown mode is returned, not coerced to zero."""
    try:
        bind_tenant(ctx, tenant_id)
        decisions = []
        for key in QuotaKey:
            decision = await inspect(session, ctx.tenant, key)
            decisions.append(decision.as_dict())
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"tenant_id": str(ctx.tenant_id), "decisions": decisions}


@router.post("/usage/check")
async def usage_check(
    tenant_id: uuid.UUID,
    payload: UsageCheck,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        decision = await inspect(
            session,
            ctx.tenant,
            payload.key,
            used=payload.used,
            environment_id=payload.environment_id,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return decision.as_dict()
