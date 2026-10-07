"""Supervisor controls.

Every mutation authenticates, authorizes, stays inside the server tenant,
validates state, writes an audit row, and treats a repeated action as a
duplicate rather than a second change.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center import metrics
from app.contact_center.exceptions import AcdError
from app.contact_center.repository import get_assignment, get_queue
from app.contact_center.service import (
    release_assignment,
    set_state,
    supervisor_reassign,
)
from app.db.session import get_session
from app.tenancy.context import reject_client_override
from app.tenancy.isolation import HierarchyError, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(tags=["supervisor"])


class StateBody(BaseModel):
    state: str
    tenant_id: uuid.UUID | None = None


class ReassignBody(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


class ClaimBody(BaseModel):
    tenant_id: uuid.UUID | None = None


def _scope(ctx: TenantContext, tenant_id: uuid.UUID | None, claimed: uuid.UUID | None) -> uuid.UUID:
    reject_client_override(ctx, claimed)
    if tenant_id is not None:
        bind_tenant(ctx, tenant_id, claimed)
    return ctx.tenant_id


@router.get("/api/supervisor/queues/{queue_id}")
@router.get("/api/tenants/{tenant_id}/supervisor/queues/{queue_id}")
async def monitor(
    queue_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        queue = await get_queue(session, tenant, queue_id)
        snap = await metrics.snapshot(session, tenant, queue_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"queue": queue.as_dict(), "metrics": snap}


@router.post("/api/supervisor/agents/{user_id}/state")
@router.post("/api/tenants/{tenant_id}/supervisor/agents/{user_id}/state")
async def force_state(
    user_id: uuid.UUID,
    payload: StateBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row, outcome = await set_state(
            session,
            tenant_id=tenant,
            user_id=user_id,
            target=payload.state,
            actor_id=ctx.user_id,
            supervisor=True,
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = row.as_dict()
    body["outcome"] = outcome
    return body


@router.post("/api/supervisor/assignments/{assignment_id}/release")
@router.post("/api/tenants/{tenant_id}/supervisor/assignments/{assignment_id}/release")
async def release(
    assignment_id: uuid.UUID,
    payload: ClaimBody | None = None,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        claimed = None if payload is None else payload.tenant_id
        tenant = _scope(ctx, tenant_id, claimed)
        row = await get_assignment(session, tenant, assignment_id)
        outcome = await release_assignment(session, row, requeue=True)
        from app.contact_center.service import _audit

        await _audit(
            session,
            tenant_id=tenant,
            actor_id=ctx.user_id,
            operation="acd_supervisor_release",
            detail={"assignment_id": str(assignment_id), "outcome": outcome},
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"outcome": outcome, "assignment_id": str(assignment_id)}


@router.post("/api/supervisor/assignments/{assignment_id}/reassign")
@router.post("/api/tenants/{tenant_id}/supervisor/assignments/{assignment_id}/reassign")
async def reassign(
    assignment_id: uuid.UUID,
    payload: ReassignBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        result = await supervisor_reassign(
            session,
            tenant_id=tenant,
            assignment_id=assignment_id,
            user_id=payload.user_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except AcdError as exc:
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return result.as_dict()
