"""Incident lifecycle and regulatory breach clocks.

An incident moves forward only: detected → triaged → contained → resolved →
closed. A backward move is a 409 with the current state, because rewriting the
lifecycle would destroy the very timing evidence a regulator asks for.

Breach clocks are separate rows: an incident may carry several (GDPR, UK GDPR,
HIPAA, a US state statute), each with its own deadline computed from the moment
of detection. A clock cannot be marked notified without a reference — an
operator's word is not proof.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    audit_action,
    claim_idempotent,
    complete_idempotent,
    http_error,
    not_found,
    replay_reference,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.incidents import breach_clock, service as incident_service
from app.db.compliance_v2_models import BreachClock, Incident
from app.db.session import get_session

router = APIRouter(prefix="/incidents", tags=["compliance-v2-incidents"])

JURISDICTIONS = ("eu_gdpr", "uk_gdpr", "us_hipaa", "us_state")


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(default="", max_length=8000)
    severity: str = "sev3"
    category: str = Field(default="security", max_length=64)
    personal_data_involved: bool = False
    jurisdictions: list[str] = Field(default_factory=list)


class IncidentOut(BaseModel):
    id: str
    reference: str
    title: str
    summary: str
    severity: str
    status: str
    category: str
    personal_data_involved: bool
    detected_at: str
    triaged_at: str
    contained_at: str
    resolved_at: str
    closed_at: str


class AdvanceIn(BaseModel):
    to_status: str
    note: str = Field(default="", max_length=2000)


class ClockStart(BaseModel):
    jurisdictions: list[str] = Field(min_length=1)


class NotificationIn(BaseModel):
    notified_at: dt.datetime
    channel: str = Field(default="email", max_length=32)
    recipient: str = Field(min_length=1, max_length=200)
    proof_reference: str = Field(min_length=1, max_length=200)


def _clock_out(row: BreachClock) -> dict[str, Any]:
    return breach_clock.as_dict(row)


@router.get("")
async def list_incidents(
    tenant_id: uuid.UUID,
    open_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The tenant's incidents. Another tenant's incident is never listed."""
    scoped_tenant(ctx, tenant_id)
    query = select(Incident).where(Incident.tenant_id == tenant_id)
    if open_only:
        query = query.where(Incident.status.notin_(("closed",)))
    rows = await session.execute(query.order_by(Incident.detected_at.desc()).limit(limit))
    return [incident_service.as_dict(row) for row in rows.scalars()]


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
async def open_incident(
    tenant_id: uuid.UUID,
    payload: IncidentCreate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    if payload.severity not in incident_service.SEVERITIES:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_severity",
            f"severity must be one of {list(incident_service.SEVERITIES)}",
        )
    unknown = [item for item in payload.jurisdictions if item not in JURISDICTIONS]
    if unknown:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unknown_jurisdiction",
            f"unknown jurisdiction(s): {', '.join(unknown)}",
        )
    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.incident.create",
        payload=payload.model_dump(mode="json"),
    )
    replayed = replay_reference(claim, resource_type="incident")
    if replayed is not None:
        existing = await incident_service.get_incident(
            session, incident_id=uuid.UUID(replayed), tenant_id=tenant_id
        )
        return IncidentOut(**incident_service.as_dict(existing))
    try:
        incident = await incident_service.create_incident(
            session,
            tenant_id=tenant_id,
            title=payload.title,
            summary=payload.summary,
            severity=payload.severity,
            created_by=ctx.user_id,
            category=payload.category,
            personal_data_involved=payload.personal_data_involved,
        )
        if payload.jurisdictions:
            await breach_clock.start_clocks_for_incident(
                session,
                incident_id=incident.id,
                tenant_id=tenant_id,
                jurisdictions=payload.jurisdictions,
                detected_at=incident.detected_at,
            )
    except incident_service.IncidentError as exc:
        raise http_error(status.HTTP_400_BAD_REQUEST, "incident_error", str(exc)) from None

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="incident_opened",
        resource_type="incident",
        resource_id=incident.id,
        detail={
            "reference": incident.reference,
            "severity": incident.severity,
            "jurisdictions": payload.jurisdictions,
            "personal_data_involved": incident.personal_data_involved,
        },
    )
    await complete_idempotent(session, claim, resource_type="incident", resource_id=incident.id)
    await session.commit()
    await session.refresh(incident)
    return IncidentOut(**incident_service.as_dict(incident))


@router.get("/{incident_id}")
async def get_incident(
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    try:
        incident = await incident_service.get_incident(
            session, incident_id=incident_id, tenant_id=tenant_id
        )
    except incident_service.IncidentNotFound:
        raise not_found("incident") from None
    entries = await incident_service.timeline(session, incident_id=incident.id)
    clocks = list(
        (
            await session.execute(
                select(BreachClock).where(
                    BreachClock.incident_id == incident.id,
                    BreachClock.tenant_id == tenant_id,
                )
            )
        ).scalars()
    )
    return {
        "incident": incident_service.as_dict(incident),
        "timeline": [
            {
                "entry_type": row.entry_type,
                "message": row.message,
                "actor_user_id": str(row.actor_user_id or ""),
                "created_at": row.created_at,
                "detail": dict(row.detail or {}),
            }
            for row in entries
        ],
        "clocks": [_clock_out(row) for row in clocks],
    }


@router.post("/{incident_id}/advance", response_model=IncidentOut)
async def advance_incident(
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    payload: AdvanceIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    try:
        incident = await incident_service.advance(
            session,
            incident_id=incident_id,
            to_status=payload.to_status,
            actor_user_id=ctx.user_id,
            note=payload.note,
            tenant_id=tenant_id,
        )
    except incident_service.IncidentNotFound:
        raise not_found("incident") from None
    except incident_service.InvalidTransition as exc:
        raise http_error(status.HTTP_409_CONFLICT, "invalid_transition", str(exc)) from None

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="incident_advanced",
        resource_type="incident",
        resource_id=incident.id,
        detail={"to_status": payload.to_status, "note": payload.note[:200]},
    )
    await session.commit()
    await session.refresh(incident)
    return IncidentOut(**incident_service.as_dict(incident))


@router.post("/{incident_id}/clocks", status_code=status.HTTP_201_CREATED)
async def start_clocks(
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    payload: ClockStart,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Open regulatory clocks for an incident that already exists."""
    scoped_tenant(ctx, tenant_id)
    unknown = [item for item in payload.jurisdictions if item not in JURISDICTIONS]
    if unknown:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unknown_jurisdiction",
            f"unknown jurisdiction(s): {', '.join(unknown)}",
        )
    try:
        incident = await incident_service.get_incident(
            session, incident_id=incident_id, tenant_id=tenant_id
        )
    except incident_service.IncidentNotFound:
        raise not_found("incident") from None
    rows = await breach_clock.start_clocks_for_incident(
        session,
        incident_id=incident.id,
        tenant_id=tenant_id,
        jurisdictions=payload.jurisdictions,
        detected_at=incident.detected_at,
    )
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="breach_clocks_started",
        resource_type="incident",
        resource_id=incident.id,
        detail={"jurisdictions": payload.jurisdictions},
    )
    await session.commit()
    return {"clocks": [_clock_out(row) for row in rows]}


@router.post("/{incident_id}/clocks/{clock_id}/notify")
async def record_notification(
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    clock_id: uuid.UUID,
    payload: NotificationIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Record that a notification was sent, with the receipt that proves it."""
    scoped_tenant(ctx, tenant_id)
    try:
        row = await breach_clock.record_notification(
            session,
            clock_id=clock_id,
            tenant_id=tenant_id,
            notified_at=payload.notified_at,
            channel=payload.channel,
            recipient=payload.recipient,
            proof_reference=payload.proof_reference,
            actor_user_id=ctx.user_id,
        )
    except breach_clock.BreachClockError as exc:
        raise http_error(status.HTTP_400_BAD_REQUEST, "breach_clock_error", str(exc)) from None
    if row.incident_id != incident_id:
        raise not_found("breach_clock")
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="breach_notified",
        resource_type="breach_clock",
        resource_id=row.id,
        detail={
            "jurisdiction": row.jurisdiction,
            "within_deadline": (row.notification_proof or {}).get("within_deadline", False),
        },
    )
    await session.commit()
    await session.refresh(row)
    return _clock_out(row)


@router.get("/clocks/due")
async def clocks_due(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Every open clock with its remaining time and effective status."""
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(BreachClock)
        .where(BreachClock.tenant_id == tenant_id, BreachClock.status.in_(("open", "overdue")))
        .order_by(BreachClock.deadline_at)
    )
    output = [_clock_out(row) for row in rows.scalars()]
    # Soonest deadline first; a clock without a deadline sorts last rather than
    # pretending to be urgent.
    output.sort(key=lambda item: item.get("remaining_hours") or float("inf"))
    return {"open_clocks": output, "count": len(output)}


@router.post("/clocks/tick")
async def run_clock_tick(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_INCIDENT_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Send reminders and escalate overdue clocks now, outside the scheduler."""
    scoped_tenant(ctx, tenant_id)
    outcome = await breach_clock.tick(session)
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="breach_clock_tick",
        resource_type="breach_clock",
        detail=dict(outcome),
    )
    await session.commit()
    return outcome


__all__ = ["router"]
