"""Routing preview, requeue and decision lookup.

Preview does not assign. A decision id from another tenant is a 404.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center.exceptions import AcdError
from app.contact_center.repository import get_decision
from app.contact_center.service import assign_next, preview, requeue
from app.db.session import get_session
from app.tenancy.context import reject_client_override
from app.tenancy.isolation import HierarchyError, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(tags=["routing"])

_FAILURES = {"escalate_failed", "escalate_unavailable"}


class PreviewBody(BaseModel):
    queue_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


class RequeueBody(BaseModel):
    entry_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


class AssignBody(BaseModel):
    queue_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


def _scope(ctx: TenantContext, tenant_id: uuid.UUID | None, claimed: uuid.UUID | None) -> uuid.UUID:
    reject_client_override(ctx, claimed)
    if tenant_id is not None:
        bind_tenant(ctx, tenant_id, claimed)
    return ctx.tenant_id


@router.post("/api/routing/preview")
@router.post("/api/tenants/{tenant_id}/routing/preview")
async def post_preview(
    payload: PreviewBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.ROUTING_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await preview(session, tenant_id=tenant, queue_id=payload.queue_id)
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/routing/requeue")
@router.post("/api/tenants/{tenant_id}/routing/requeue")
async def post_requeue(
    payload: RequeueBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.ROUTING_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        result = await requeue(
            session, tenant_id=tenant, entry_id=payload.entry_id, actor_id=ctx.user_id
        )
        await session.commit()
    except AcdError as exc:
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    if result.outcome in _FAILURES:
        raise HTTPException(status_code=409, detail=result.as_dict())
    return result.as_dict()


@router.post("/api/routing/assign-next")
@router.post("/api/tenants/{tenant_id}/routing/assign-next")
async def post_assign_next(
    payload: AssignBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.ROUTING_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        result = await assign_next(
            session, tenant_id=tenant, queue_id=payload.queue_id, actor_id=ctx.user_id
        )
        await session.commit()
    except AcdError as exc:
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    if result.outcome in _FAILURES or result.assignment is None:
        raise HTTPException(status_code=409, detail=result.as_dict())
    return result.as_dict()


@router.get("/api/routing/decisions/{decision_id}")
@router.get("/api/tenants/{tenant_id}/routing/decisions/{decision_id}")
async def get_one(
    decision_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.ROUTING_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        row = await get_decision(session, tenant, decision_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()
