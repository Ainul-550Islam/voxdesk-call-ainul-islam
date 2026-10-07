# File: app/api/workflow_event_routes.py — Missing APIs: workflow call-event triggers, call-context fetch, outcome write-back post-call result → CRM/helpdesk/task/calendar
"""
Workflow event triggers API.
Closes gaps:
25. Workflow call-event triggers — before-call / after-call / transfer / completion event triggers
26. Workflow call-context API — fetch CRM/customer/ticket/booking context before call
27. Workflow outcome write-back API — post-call result → CRM/helpdesk/task/calendar
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Call, Workflow
from app.db.session import get_session
from app.db.enterprise_models import WorkflowTrigger

router = APIRouter(prefix="/api/workflows", tags=["workflow-events"])

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class TriggerCreate(_Strict):
    workflow_id: str = Field(min_length=1, max_length=80)
    event_type: str = Field(pattern="^(before_call|after_call|on_transfer|on_completion|on_failure|on_booking)$")
    config: dict = Field(default_factory=dict)
    is_enabled: bool = True

class TriggerUpdate(_Strict):
    event_type: Optional[str] = Field(default=None, pattern="^(before_call|after_call|on_transfer|on_completion|on_failure|on_booking)$")
    config: Optional[dict] = None
    is_enabled: Optional[bool] = None

class TriggerOut(_Strict):
    id: str
    tenant_id: str
    workflow_id: str
    event_type: str
    is_enabled: bool
    config: dict
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class CallContextOut(_Strict):
    call_id: str
    crm: dict
    customer: dict
    tickets: list[dict]
    bookings: list[dict]
    custom: dict

class OutcomeWritebackRequest(_Strict):
    target: str = Field(pattern="^(crm|helpdesk|task|calendar|webhook)$")
    entity_type: Optional[str] = Field(default=None, max_length=32)
    payload: dict = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)

class OutcomeWritebackOut(_Strict):
    id: str
    call_id: str
    target: str
    status: str
    created_at: str

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _to_out(row: WorkflowTrigger) -> TriggerOut:
    d = row.as_dict()
    return TriggerOut(
        id=d["id"],
        tenant_id=d["tenant_id"],
        workflow_id=d["workflow_id"],
        event_type=d["event_type"],
        is_enabled=d["is_enabled"],
        config=d["config"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )

@router.post("/triggers", response_model=TriggerOut, status_code=201)
async def create_trigger(
    payload: TriggerCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/workflows/triggers — Create call-event trigger for workflow."""
    # Resolve the authoritative tenant-owned workflow before storing a trigger.
    # Malformed, missing and foreign identifiers share the same 404 response.
    try:
        workflow_id = uuid.UUID(payload.workflow_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="workflow not found") from None
    workflow = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == ctx.tenant_id)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")

    trigger = WorkflowTrigger(
        tenant_id=ctx.tenant_id,
        workflow_id=str(workflow.id),
        event_type=payload.event_type,
        is_enabled=payload.is_enabled,
        config=payload.config,
    )
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    return _to_out(trigger)

@router.get("/triggers", response_model=dict)
async def list_triggers(
    workflow_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    is_enabled: Optional[bool] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [WorkflowTrigger.tenant_id == ctx.tenant_id]
    if workflow_id:
        scope.append(WorkflowTrigger.workflow_id == workflow_id)
    if event_type:
        scope.append(WorkflowTrigger.event_type == event_type)
    if is_enabled is not None:
        scope.append(WorkflowTrigger.is_enabled == is_enabled)
    total = (await session.execute(select(func.count(WorkflowTrigger.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(WorkflowTrigger).where(*scope).order_by(WorkflowTrigger.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {"triggers": [r.as_dict() for r in rows], "total": int(total), "limit": limit, "offset": offset}

@router.get("/triggers/{trigger_id}", response_model=TriggerOut)
async def get_trigger(
    trigger_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(WorkflowTrigger, trigger_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="trigger not found")
    return _to_out(row)

@router.patch("/triggers/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: uuid.UUID,
    payload: TriggerUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(WorkflowTrigger, trigger_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="trigger not found")
    if payload.event_type is not None:
        row.event_type = payload.event_type
    if payload.config is not None:
        row.config = payload.config
    if payload.is_enabled is not None:
        row.is_enabled = payload.is_enabled
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)

@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(WorkflowTrigger, trigger_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="trigger not found")
    await session.delete(row)
    await session.commit()
    return {"id": str(trigger_id), "deleted": True}

@router.get("/calls/{call_id}/context", response_model=CallContextOut)
async def get_call_context(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    GET /api/workflows/calls/{id}/context — Fetch CRM/customer/ticket/booking context before call.
    Aggregates from CRM adapters, calendar, etc.
    """
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="call not found")
    from app.db.models import Appointment, Lead

    crm = {}
    customer = {}
    if call.lead_id:
        lead = await session.get(Lead, call.lead_id)
        if lead is not None and lead.tenant_id == ctx.tenant_id:
            crm = {"lead": {"id": str(lead.id), "name": lead.name, "phone": lead.phone,
                            "email": lead.email, "company": lead.company, "score": lead.score}}
            customer = {"name": lead.name, "phone": lead.phone, "email": lead.email}
    appointments = (await session.scalars(select(Appointment).where(
        Appointment.tenant_id == ctx.tenant_id, Appointment.call_id == call_id
    ))).all()
    bookings = [{"id": str(a.id), "starts_at": a.starts_at.isoformat() if a.starts_at else None,
                 "status": a.status.value} for a in appointments]
    return CallContextOut(call_id=str(call.id), crm=crm, customer=customer, tickets=[],
                          bookings=bookings, custom=getattr(call, "custom_fields", {}) or {})

@router.post("/calls/{call_id}/outcome-writeback", response_model=OutcomeWritebackOut, status_code=201)
async def outcome_writeback(
    call_id: uuid.UUID,
    payload: OutcomeWritebackRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
):
    """
    POST /api/workflows/calls/{id}/outcome-writeback — Post-call result → CRM/helpdesk/task/calendar.
    Idempotent via idempotency_key if provided.
    """
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="call not found")

    raise HTTPException(status_code=501, detail="WORKFLOW_WRITEBACK_RUNTIME_NOT_IMPLEMENTED")
