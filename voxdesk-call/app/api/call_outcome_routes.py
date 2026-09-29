"""Human-recorded call outcome and resolution metrics endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.governance.context import resolve_scope
from app.qa import outcomes
from app.qa.evidence import load_call
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter()


class CallOutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["resolved", "unresolved", "follow_up_required", "handed_off"]
    reason_code: Literal[
        "agent_completed_task",
        "human_completed_task",
        "unable_to_resolve",
        "caller_disconnected",
        "provider_failure",
        "follow_up_needed",
        "external_dependency",
        "transfer_connected",
    ]
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8 or any(ord(char) < 32 for char in value):
            raise ValueError("idempotency_key must contain 8 to 128 printable characters")
        return value


async def _call_scope(session: AsyncSession, ctx: TenantContext, call_id: uuid.UUID):
    call = await load_call(session, ctx.tenant_id, call_id)
    scope = await resolve_scope(session, ctx, call.environment_id)
    return call, scope


@router.post("/api/conversations/{call_id}/outcomes", status_code=201)
async def record_call_outcome(
    call_id: uuid.UUID,
    body: CallOutcomeInput,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        _call, scope = await _call_scope(session, ctx, call_id)
        result = await outcomes.record_call_outcome(
            session,
            scope,
            call_id=call_id,
            actor_id=ctx.user_id,
            outcome=body.outcome,
            reason_code=body.reason_code,
            idempotency_key=body.idempotency_key,
        )
        await session.commit()
        return {**result.event.as_dict(), "duplicate": result.duplicate}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/api/conversations/{call_id}/outcomes")
async def get_call_outcomes(
    call_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        _call, scope = await _call_scope(session, ctx, call_id)
        events = await outcomes.latest_call_outcomes(
            session, scope, call_id=call_id, limit=limit
        )
        return {"outcomes": [event.as_dict() for event in events]}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/api/qa/outcomes/metrics")
async def get_call_outcome_metrics(
    environment_id: uuid.UUID,
    start: datetime,
    end: datetime,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        return await outcomes.outcome_metrics(session, scope, start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
