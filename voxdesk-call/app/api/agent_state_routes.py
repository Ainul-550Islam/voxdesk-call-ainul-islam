"""Agent presence.

A heartbeat refreshes a live state. It does not turn offline into available.
An agent can change only their own state. Disabling an agent is a supervisor
action and is rejected here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center.agent_state import ensure
from app.contact_center.exceptions import AcdError
from app.contact_center.presence import heartbeat
from app.contact_center.service import accept_work, complete_work, set_state
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(tags=["agent-state"])


class StateBody(BaseModel):
    state: str
    capacity: int | None = Field(default=None, ge=1)
    tenant_id: uuid.UUID | None = None


class CompleteBody(BaseModel):
    assignment_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


class ClaimBody(BaseModel):
    tenant_id: uuid.UUID | None = None


def _reject_claim(ctx: TenantContext, claimed: uuid.UUID | None) -> None:
    from app.tenancy.context import reject_client_override

    reject_client_override(ctx, claimed)


@router.get("/api/agent-state/me")
async def get_me(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await ensure(session, ctx.tenant_id, ctx.user_id)
    await session.commit()
    return row.as_dict()


@router.post("/api/agent-state/me")
async def post_me(
    payload: StateBody,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    if payload.state == "disabled":
        raise HTTPException(status_code=403, detail={"code": "authorization", "message": "supervisor required"})
    try:
        _reject_claim(ctx, payload.tenant_id)
        row, outcome = await set_state(
            session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            target=payload.state,
            actor_id=ctx.user_id,
            supervisor=False,
            capacity=payload.capacity,
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = row.as_dict()
    body["outcome"] = outcome
    return body


@router.post("/api/agent-state/heartbeat")
async def post_heartbeat(
    payload: ClaimBody | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _reject_claim(ctx, None if payload is None else payload.tenant_id)
        row = await heartbeat(session, ctx.tenant_id, ctx.user_id)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/agent-state/accept")
async def post_accept(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        outcome = await accept_work(
            session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, actor_id=ctx.user_id
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return {"outcome": outcome}


@router.post("/api/agent-state/complete")
async def post_complete(
    payload: CompleteBody,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _reject_claim(ctx, payload.tenant_id)
        outcome = await complete_work(
            session,
            tenant_id=ctx.tenant_id,
            assignment_id=payload.assignment_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"outcome": outcome}
