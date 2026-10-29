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
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Call, Workflow, WorkflowVersion, Environment
from app.db.session import get_session
from app.db.enterprise_models import WorkflowTrigger

from app.services import post_call_workflow as runtime
from app.audit.service import record_enterprise_audit, record_event
from app.core.rate_limit import rate_limit
from app.resilience.idempotency import claim_request, complete_request, IdempotencyError
from app.environments.context_resolution import resolve_environment
from app.environments.membership import resolve as resolve_membership
from app.auth.rbac import has_permission
from app.tenancy.isolation import HierarchyError, to_http
from app.api.integrations_v2_routes import get_int2_http_client
from app.schemas.call_control import OutcomeWritebackResult


async def _environment(session, ctx, write=False, permission=None):
    try:
        env = await resolve_environment(session, user=ctx.user, tenant=ctx.tenant, environment_id=ctx.environment_id)
        if env is None:
            raise HTTPException(409, detail={"code": "NOT_CONFIGURED"})
        decision = await resolve_membership(session, ctx.user, env, ctx.tenant)
        if not decision.allowed or env.status != "active":
            raise HTTPException(404, detail="environment not found")
        if not has_permission(decision.role, permission or (Permission.TENANT_UPDATE if write else Permission.TENANT_READ)):
            raise HTTPException(403, detail="environment permission denied")
        return env
    except HierarchyError as exc:
        raise to_http(exc) from None


async def _binding(session, ctx, config, event_type, workflow_id, enabled):
    if runtime.BINDING not in config:
        return None
    env = await _environment(session, ctx, True)
    if config[runtime.BINDING] != str(env.id):
        raise HTTPException(404, detail="environment not found")
    if set(config) != {runtime.BINDING} or event_type not in runtime.EVENTS:
        raise HTTPException(501, detail={"code": "UNSUPPORTED_CAPABILITY", "capability": "post_call_trigger_config"})
    await session.scalar(select(Environment).where(Environment.id == env.id, Environment.tenant_id == ctx.tenant_id).with_for_update())
    if enabled:
        workflow = await session.scalar(select(Workflow).where(Workflow.id == uuid.UUID(workflow_id), Workflow.tenant_id == ctx.tenant_id))
        version = None if workflow is None else await session.scalar(select(WorkflowVersion).where(
            WorkflowVersion.id == workflow.published_version_id, WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.tenant_id == ctx.tenant_id, WorkflowVersion.status == "published"))
        if workflow is None or workflow.status != "active" or version is None:
            raise HTTPException(409, detail={"code": "NOT_CONFIGURED", "capability": "published_workflow"})
        try:
            runtime.definition(version.graph_config, ctx.tenant_id)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(501, detail={"code": "UNSUPPORTED_CAPABILITY", "capability": "workflow_graph"}) from None
    try:
        if not await rate_limit(f"workflow:configuration:{ctx.tenant_id}:{env.id}", 20, 60):
            raise HTTPException(429, detail="Configuration rate exceeded", headers={"Retry-After": "60"})
    except RuntimeError:
        raise HTTPException(501, detail={"code": "NOT_CONFIGURED", "capability": "redis_rate_limit"}) from None
    return env


async def _owned_trigger(session, ctx, trigger_id, write=False):
    row = await session.scalar(select(WorkflowTrigger).where(WorkflowTrigger.id == trigger_id,
        WorkflowTrigger.tenant_id == ctx.tenant_id).with_for_update())
    if row is None:
        raise HTTPException(404, detail="trigger not found")
    if runtime.BINDING in row.config:
        env = await _environment(session, ctx, write)
        if row.config[runtime.BINDING] != str(env.id):
            raise HTTPException(404, detail="trigger not found")
    return row


async def _audit(session, ctx, row, event, env):
    await record_event(session, tenant_id=ctx.tenant_id, environment_id=env.id if env else None,
        actor_user_id=ctx.user_id, event_type=event, resource_type="workflow_trigger", resource_id=row.id)


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
    #: One row per integration target that was attempted. A write-back fans
    #: out — a tenant can hold two CRM connections — so a single status would
    #: hide which target actually recorded the outcome.
    results: list[OutcomeWritebackResult] = Field(default_factory=list)
    replayed: bool = False
    idempotency_key: Optional[str] = None

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
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", min_length=8, max_length=128),
):
    """Create a trigger; automatic post-call binding requires an Idempotency-Key."""
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

    env = await _binding(session, ctx, payload.config, payload.event_type, str(workflow.id), payload.is_enabled)
    claim = None
    if env:
        if not idempotency_key:
            raise HTTPException(422, detail="Idempotency-Key is required")
        try:
            claim = await claim_request(session, tenant_id=ctx.tenant_id, environment_id=env.id,
                operation="workflow.trigger.create", key=idempotency_key, request_data=payload.model_dump(mode="json"))
        except IdempotencyError as exc:
            raise HTTPException(409, detail={"code": exc.code}) from None
        if claim.replayed:
            return _to_out(await _owned_trigger(session, ctx, uuid.UUID(claim.receipt.resource_id), True))
        count = await session.scalar(select(func.count(WorkflowTrigger.id)).where(WorkflowTrigger.tenant_id == ctx.tenant_id,
            WorkflowTrigger.config[runtime.BINDING].as_string() == str(env.id)))
        if count >= runtime.MAX_TRIGGERS:
            raise HTTPException(409, detail="Post-call trigger limit reached; remove unused bindings")

    trigger = WorkflowTrigger(
        tenant_id=ctx.tenant_id,
        workflow_id=str(workflow.id),
        event_type=payload.event_type,
        is_enabled=payload.is_enabled,
        config=payload.config,
    )
    session.add(trigger)
    await session.flush()
    await _audit(session, ctx, trigger, "workflow.trigger_created", env)
    if claim:
        await complete_request(session, claim.receipt, resource_type="workflow_trigger", resource_id=trigger.id)
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
    env = await _environment(session, ctx)
    scope = [WorkflowTrigger.tenant_id == ctx.tenant_id, or_(WorkflowTrigger.config[runtime.BINDING].as_string().is_(None),
        WorkflowTrigger.config[runtime.BINDING].as_string() == str(env.id))]
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
    row = await _owned_trigger(session, ctx, trigger_id)
    return _to_out(row)

@router.patch("/triggers/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    trigger_id: uuid.UUID,
    payload: TriggerUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _owned_trigger(session, ctx, trigger_id, True)
    old_config, old_event = row.config, row.event_type
    env = None
    if payload.event_type is not None:
        row.event_type = payload.event_type
    if payload.config is not None:
        row.config = payload.config
    if payload.is_enabled is not None:
        row.is_enabled = payload.is_enabled
    new_env = await _binding(session, ctx, row.config, row.event_type, row.workflow_id, row.is_enabled)
    if new_env is None and runtime.BINDING in old_config:
        env = await _binding(session, ctx, old_config, old_event, row.workflow_id, False)
    if new_env:
        count = await session.scalar(select(func.count(WorkflowTrigger.id)).where(WorkflowTrigger.tenant_id == ctx.tenant_id,
            WorkflowTrigger.config[runtime.BINDING].as_string() == str(new_env.id)))
        if count > runtime.MAX_TRIGGERS:
            raise HTTPException(409, detail="Post-call trigger limit reached")
    await _audit(session, ctx, row, "workflow.trigger_updated", new_env or env)
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
    row = await _owned_trigger(session, ctx, trigger_id, True)
    env = await _binding(session, ctx, row.config, row.event_type, row.workflow_id, False)
    await _audit(session, ctx, row, "workflow.trigger_deleted", env)
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

# ------------------------------------------------------- outcome write-back ---

#: Operation name for the durable HTTP receipt. It is also part of the write-back
#: ledger's id namespace, so it must stay stable: renaming it would let a retried
#: request mint a second ledger row for the same outcome.
OUTCOME_WRITEBACK_OPERATION = "workflow.outcome_writeback"

#: Target -> (integration-v2 category, ledger entity). ``task`` is a CRM activity
#: in every provider we ship, so it shares the CRM category and is told apart by
#: its entity.
_LEDGER_TARGETS: dict[str, tuple[str, str]] = {
    "crm": ("crm", "call_log"),
    "task": ("crm", "task"),
    "helpdesk": ("helpdesk", "ticket_call_log"),
}

#: Dispositions that mean the customer never turned up, so the booking is a
#: no-show rather than a completed appointment.
NO_SHOW_DISPOSITIONS = frozenset({"no_show", "no-show", "no_answer", "no-answer"})

MAX_WRITEBACK_SUBJECT_LENGTH = 240
MAX_WRITEBACK_SUMMARY_LENGTH = 8000
MAX_APPOINTMENT_NOTE_LENGTH = 8000


def _writeback_subject(call: Call, payload: OutcomeWritebackRequest) -> str:
    raw = str(payload.payload.get("subject") or "")
    if not raw:
        direction = call.direction.value if hasattr(call.direction, "value") else "call"
        raw = f"Voice call ({direction})"
    return raw.strip()[:MAX_WRITEBACK_SUBJECT_LENGTH]


def _writeback_summary(call: Call, payload: OutcomeWritebackRequest) -> str:
    raw = str(payload.payload.get("summary") or "") or str(getattr(call, "summary", "") or "")
    return raw.strip()[:MAX_WRITEBACK_SUMMARY_LENGTH]


def _writeback_disposition(payload: OutcomeWritebackRequest) -> str:
    return str(payload.payload.get("disposition") or "completed").strip()[:64] or "completed"


def _caller_phone(call: Call) -> str:
    return str(getattr(call, "from_number", "") or "")


async def _caller_email(session: AsyncSession, call: Call) -> str:
    """Caller email from the linked lead, or "" when none was captured.

    Never guessed: a wrong email in a CRM activity is worse than an empty one,
    because it silently attaches this call to the wrong contact.
    """
    lead_id = getattr(call, "lead_id", None)
    if not lead_id:
        return ""
    from app.db.models import Lead

    lead = await session.get(Lead, lead_id)
    if lead is None or lead.tenant_id != call.tenant_id:
        return ""
    return str(getattr(lead, "email", "") or "")


def _duration_seconds(call: Call) -> int:
    started = getattr(call, "started_at", None)
    ended = getattr(call, "ended_at", None)
    if started is None or ended is None:
        return 0
    try:
        return max(0, int((ended - started).total_seconds()))
    except (TypeError, ValueError):
        return 0


def _direction(call: Call) -> str:
    return call.direction.value if hasattr(call.direction, "value") else str(call.direction)


def _dispatcher_for_target(provider: Any, target: str) -> Any:
    """The provider method that records a call outcome for this target.

    Chosen by the provider's declared interface rather than by ``getattr`` on a
    name, so a provider that happens to expose a same-named helper is never
    mistaken for one that implements the contract.
    """
    from app.integrations_v2.crm import CrmProviderV2
    from app.integrations_v2.helpdesk.models import HelpdeskProviderV2

    if target == "helpdesk":
        if not isinstance(provider, HelpdeskProviderV2):
            raise HTTPException(
                status_code=422,
                detail=f"Connection is not a helpdesk provider; cannot write an outcome to {target}.",
            )
        return provider.log_voice_call
    if not isinstance(provider, CrmProviderV2):
        raise HTTPException(
            status_code=422,
            detail=f"Connection is not a CRM provider; cannot write an outcome to {target}.",
        )
    return provider.log_call_activity


def _result_from_ledger(ledger: Any) -> OutcomeWritebackResult:
    error = str(getattr(ledger, "last_error", "") or "")
    failed = str(ledger.status) in {"failed", "dead_letter"}
    return OutcomeWritebackResult(
        target=f"{ledger.provider}:{ledger.entity}",
        status=str(ledger.status),
        provider=str(getattr(ledger, "provider", "") or ""),
        external_id=str(getattr(ledger, "external_id", "") or "") or None,
        writeback_id=str(ledger.id),
        replayed=False,
        error_code="writeback_failed" if failed else None,
        error=error or None,
        at=_now().isoformat(),
    )


def _aggregate_status(results: list[OutcomeWritebackResult]) -> str:
    """One status for the whole request, derived from the per-target rows."""
    if not results:
        return "no_target"
    statuses = {result.status for result in results}
    if statuses == {"succeeded"}:
        return "succeeded"
    if "succeeded" in statuses:
        return "partial"
    if statuses & {"failed", "dead_letter"}:
        return "failed"
    if statuses == {"skipped"}:
        return "skipped"
    return "pending"


async def _linked_appointments(session: AsyncSession, call: Call) -> list[Any]:
    """Appointments this call owns, falling back to the caller's lead.

    Two lookups because a booking made on a campaign call is linked to the
    lead, while one made during the call is linked to the call itself.
    """
    from app.db.models import Appointment

    rows = list(
        (
            await session.scalars(
                select(Appointment).where(
                    Appointment.tenant_id == call.tenant_id,
                    Appointment.call_id == call.id,
                )
            )
        ).all()
    )
    if rows:
        return rows
    lead_id = getattr(call, "lead_id", None)
    if not lead_id:
        return []
    return list(
        (
            await session.scalars(
                select(Appointment).where(
                    Appointment.tenant_id == call.tenant_id,
                    Appointment.lead_id == lead_id,
                )
            )
        ).all()
    )


async def _writeback_to_integrations(
    session: AsyncSession,
    *,
    call: Call,
    payload: OutcomeWritebackRequest,
    http_client: Any,
) -> list[OutcomeWritebackResult]:
    """Write the outcome to every active connection for the target."""
    from app.integrations_v2.core.connection_store import list_tenant_connections
    from app.integrations_v2.core.errors import IntegrationV2Error
    from app.integrations_v2.core.writeback import (
        CallWritebackInput,
        execute_writeback_now,
    )
    from app.integrations_v2.register_all import instantiate_provider_v2

    category, entity = _LEDGER_TARGETS[payload.target]
    connections = [
        row
        for row in await list_tenant_connections(session, tenant_id=call.tenant_id, category=category)
        if row.status == "active"
    ]
    if not connections:
        return [
            OutcomeWritebackResult(
                target=payload.target,
                status="skipped",
                error_code="no_active_connection",
                error=f"No active {category} connection is configured for this tenant.",
                at=_now().isoformat(),
            )
        ]

    writeback_input = CallWritebackInput(
        call_id=str(call.id),
        subject=_writeback_subject(call, payload),
        summary=_writeback_summary(call, payload),
        disposition=_writeback_disposition(payload),
        duration_seconds=_duration_seconds(call),
        direction=_direction(call),
        caller_phone=_caller_phone(call),
        caller_email=await _caller_email(session, call),
        external_contact_id=str(payload.payload.get("external_contact_id") or "")[:160],
        external_ticket_id=str(payload.payload.get("external_ticket_id") or "")[:160],
        occurred_at=getattr(call, "ended_at", None) or _now(),
        extra={k: v for k, v in payload.payload.items() if k.startswith("x_")},
    )

    results: list[OutcomeWritebackResult] = []
    for connection in connections:
        # Each connection is a separate external system, so a failure is
        # isolated: the caller must be able to see which system recorded the
        # call and which did not.
        try:
            provider = instantiate_provider_v2(connection, http_client=http_client)
            dispatcher = _dispatcher_for_target(provider, payload.target)
            ledger, executed = await execute_writeback_now(
                session,
                connection=connection,
                writeback_input=writeback_input,
                dispatcher=dispatcher,
                action=f"{payload.target}_outcome_writeback",
                entity=entity,
            )
        except IntegrationV2Error as exc:
            results.append(
                OutcomeWritebackResult(
                    target=f"{connection.provider}:{entity}",
                    status="failed",
                    provider=connection.provider,
                    error_code=exc.code,
                    error=exc.safe_message,
                    at=_now().isoformat(),
                )
            )
            continue
        result = _result_from_ledger(ledger)
        result.replayed = not executed
        results.append(result)
    return results


async def _writeback_to_calendar(
    session: AsyncSession,
    *,
    call: Call,
    payload: OutcomeWritebackRequest,
) -> list[OutcomeWritebackResult]:
    """Record the call outcome on the bookings this call owns.

    A calendar write-back is only meaningful where a booking exists. A no-show
    disposition marks the appointment as one through the booking service, which
    is what releases the slot back; any other disposition appends the outcome
    to the appointment's notes, the field staff actually read. Either way a row
    changes, or the target reports skipped — never a success for doing nothing.
    """
    appointments = await _linked_appointments(session, call)
    if not appointments:
        return [
            OutcomeWritebackResult(
                target="calendar",
                status="skipped",
                error_code="no_appointment_for_call",
                error="No appointment is linked to this call, so there is no booking outcome to record.",
                at=_now().isoformat(),
            )
        ]

    disposition = _writeback_disposition(payload).lower()
    summary = _writeback_summary(call, payload)
    is_no_show = disposition in NO_SHOW_DISPOSITIONS
    results: list[OutcomeWritebackResult] = []
    for appointment in appointments:
        if is_no_show:
            from app.integrations.calendar import service as calendar_service

            # Commits internally: releasing the slot is an effect that must not
            # be lost if a later target in the same request fails.
            await calendar_service.mark_no_show(session, appointment)
        else:
            stamp = _now().strftime("%Y-%m-%d %H:%M UTC")
            note = f"[{stamp}] Call outcome: {disposition}"
            if summary:
                note = f"{note} — {summary[:400]}"
            existing = str(getattr(appointment, "notes", "") or "")
            appointment.notes = f"{existing}\n{note}".strip()[:MAX_APPOINTMENT_NOTE_LENGTH]
        results.append(
            OutcomeWritebackResult(
                target="calendar:appointment",
                status="succeeded",
                provider=str(appointment.provider.value)
                if appointment.provider is not None
                else None,
                external_id=str(appointment.external_event_id or appointment.id),
                writeback_id=str(appointment.id),
                at=_now().isoformat(),
            )
        )
    return results


async def _writeback_to_webhook(
    session: AsyncSession,
    *,
    call: Call,
    payload: OutcomeWritebackRequest,
) -> list[OutcomeWritebackResult]:
    """Deliver the outcome to the tenant's configured outbound webhook.

    The CRM v1 webhook adapter is the system's signed outbound callback: it
    posts an HMAC-signed body and returns the delivery id it was given. An
    unconfigured webhook integration reports ``skipped``, because there is no
    endpoint to sign for and nothing to deliver to.
    """
    from app.api.crm_writeback_routes import _load_provider_for_tenant
    from app.db.models import CrmProviderType
    from app.integrations.crm.base import Capability
    from app.integrations.crm.models import NormalizedActivity, NormalizedContact
    from app.integrations.crm.registry import supports

    try:
        _integration, adapter = await _load_provider_for_tenant(session, call.tenant_id, "webhook")
    except HTTPException as exc:
        message = exc.detail if isinstance(exc.detail, str) else "webhook integration is not configured"
        return [
            OutcomeWritebackResult(
                target="webhook",
                status="skipped",
                error_code="webhook_not_configured",
                error=str(message)[:300],
                at=_now().isoformat(),
            )
        ]

    if not supports(CrmProviderType.WEBHOOK, Capability.CREATE_ACTIVITY):
        return [
            OutcomeWritebackResult(
                target="webhook",
                status="skipped",
                error_code="webhook_cannot_record_activity",
                error="The configured webhook integration cannot record an activity.",
                at=_now().isoformat(),
            )
        ]

    contact_id = str(payload.payload.get("external_contact_id") or "").strip()
    if not contact_id:
        contact = NormalizedContact(
            first_name=str(payload.payload.get("caller_name") or "Unknown")[:80],
            last_name="",
            phone=_caller_phone(call),
            email=await _caller_email(session, call),
        )
        upserted = await adapter.upsert_contact(contact)
        contact_id = str(getattr(upserted, "external_id", "") or "")

    activity = NormalizedActivity(
        title=_writeback_subject(call, payload),
        body=_writeback_summary(call, payload),
        occurred_at=getattr(call, "ended_at", None) or _now(),
        duration_seconds=float(_duration_seconds(call)),
        attributes={
            "call_id": str(call.id),
            "disposition": _writeback_disposition(payload),
        },
    )
    try:
        result = await adapter.create_activity(contact_id, activity)
    except Exception as exc:  # noqa: BLE001 - reported per target, never swallowed
        return [
            OutcomeWritebackResult(
                target="webhook",
                status="failed",
                error_code=type(exc).__name__.lower(),
                error=str(exc)[:300],
                at=_now().isoformat(),
            )
        ]
    return [
        OutcomeWritebackResult(
            target="webhook:activity",
            status="succeeded",
            provider="webhook",
            external_id=str(getattr(result, "external_id", "") or "") or None,
            at=_now().isoformat(),
        )
    ]


def _pending_result(target: str, message: str) -> OutcomeWritebackResult:
    """The answer for a replay whose outcome was never recorded."""
    return OutcomeWritebackResult(
        target=target,
        status="pending",
        error_code="outcome_pending_reconciliation",
        error=message,
        at=_now().isoformat(),
    )


async def _stored_outcome_results(
    session: AsyncSession, *, call: Call, target: str
) -> list[OutcomeWritebackResult]:
    """Rebuild a replayed response from durable state.

    The write-back ledger holds the outcome for CRM/helpdesk/task, and the
    appointment row holds it for calendar. Nothing is re-dispatched: a replay
    re-reads what already happened, so a client that retries after a timeout
    can never cause a second write to the vendor.
    """
    from app.db.integrations_v2_models import WritebackLedger

    if target in _LEDGER_TARGETS:
        entity = _LEDGER_TARGETS[target][1]
        rows = list(
            (
                await session.scalars(
                    select(WritebackLedger).where(
                        WritebackLedger.tenant_id == call.tenant_id,
                        WritebackLedger.call_id == str(call.id)[:120],
                        WritebackLedger.entity == entity,
                    )
                )
            ).all()
        )
        if not rows:
            return [
                _pending_result(
                    target,
                    "This Idempotency-Key was accepted but its outcome was never recorded; it requires reconciliation.",
                )
            ]
        return [_result_from_ledger(row) for row in rows]

    if target == "calendar":
        appointments = await _linked_appointments(session, call)
        if not appointments:
            return [
                _pending_result(
                    "calendar",
                    "This Idempotency-Key was accepted but no appointment outcome was recorded.",
                )
            ]
        return [
            OutcomeWritebackResult(
                target="calendar:appointment",
                status="succeeded",
                provider=str(row.provider.value) if row.provider is not None else None,
                external_id=str(row.external_event_id or row.id),
                writeback_id=str(row.id),
                at=_now().isoformat(),
            )
            for row in appointments
        ]

    return [
        _pending_result(
            target,
            "This Idempotency-Key was accepted; the stored webhook outcome requires reconciliation.",
        )
    ]


@router.post("/calls/{call_id}/outcome-writeback", response_model=OutcomeWritebackOut, status_code=201)
async def outcome_writeback(
    call_id: uuid.UUID,
    payload: OutcomeWritebackRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=128),
    http_client: Any = Depends(get_int2_http_client),
):
    """
    POST /api/workflows/calls/{id}/outcome-writeback — Post-call result to CRM/helpdesk/task/calendar/webhook.

    Idempotent through a durable receipt keyed on ``idempotency_key`` (or the
    ``Idempotency-Key`` header). A replay returns the stored outcome and never
    contacts the integration again: a second write would either duplicate the
    record or be rejected by the vendor's own dedupe, and the caller could not
    tell which had happened.

    Every target is attempted independently and reported on its own row, so a
    tenant holding two CRM connections sees exactly which one recorded the call.
    """
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="call not found")

    key = (payload.idempotency_key or idempotency_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "idempotency_key_required",
                "message": "An Idempotency-Key of 8 to 128 characters is required for an outcome write-back.",
            },
        )

    try:
        claim = await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            environment_id=call.environment_id,
            operation=OUTCOME_WRITEBACK_OPERATION,
            key=key,
            request_data=payload.model_dump(mode="json"),
        )
    except IdempotencyError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from None

    if claim.replayed:
        results = await _stored_outcome_results(session, call=call, target=payload.target)
        return OutcomeWritebackOut(
            id=claim.receipt.resource_id or str(call.id),
            call_id=str(call.id),
            target=payload.target,
            status=_aggregate_status(results),
            created_at=_now().isoformat(),
            results=results,
            replayed=True,
            idempotency_key=key,
        )

    if payload.target in _LEDGER_TARGETS:
        results = await _writeback_to_integrations(
            session, call=call, payload=payload, http_client=http_client
        )
    elif payload.target == "calendar":
        results = await _writeback_to_calendar(session, call=call, payload=payload)
    else:
        results = await _writeback_to_webhook(session, call=call, payload=payload)

    first_id = next((r.writeback_id for r in results if r.writeback_id), str(call.id))
    await complete_request(
        session, claim.receipt, resource_type="writeback_ledger", resource_id=first_id
    )
    await record_enterprise_audit(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        action="workflow.outcome_writeback",
        resource_type="call",
        resource_id=call.id,
        detail={
            "target": payload.target,
            "status": _aggregate_status(results),
            "targets": [
                {"target": r.target, "status": r.status, "external_id": r.external_id}
                for r in results
            ],
        },
    )
    await session.commit()
    return OutcomeWritebackOut(
        id=first_id,
        call_id=str(call.id),
        target=payload.target,
        status=_aggregate_status(results),
        created_at=_now().isoformat(),
        results=results,
        replayed=False,
        idempotency_key=key,
    )


@router.get("/calls/{call_id}/executions", response_model=dict)
async def post_call_executions(
    call_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Scoped workflow outcomes, separate from durable job acknowledgements."""
    from app.db.models import DurableJob, WorkflowExecution
    from app.jobs.types import JobType
    env = await _environment(session, ctx, permission=Permission.CALL_READ)
    call = await session.scalar(select(Call).where(Call.id == call_id, Call.tenant_id == ctx.tenant_id,
        Call.environment_id == env.id))
    if call is None:
        raise HTTPException(404, detail="call not found")
    scope = (DurableJob.tenant_id == ctx.tenant_id, DurableJob.environment_id == env.id,
        DurableJob.job_type == JobType.WORKFLOW_EXECUTION, DurableJob.payload["call_id"].as_string() == str(call_id))
    total = await session.scalar(select(func.count(DurableJob.id)).where(*scope))
    jobs = list((await session.scalars(select(DurableJob).where(*scope).order_by(DurableJob.created_at, DurableJob.id)
        .limit(limit).offset(offset))).all())
    items = []
    for job in jobs:
        execution = await session.scalar(select(WorkflowExecution).where(
            WorkflowExecution.id == uuid.uuid5(job.id, "post-call-workflow:v1"), WorkflowExecution.tenant_id == ctx.tenant_id))
        if execution and (execution.input_payload.get("environment_id") != str(env.id)
                          or execution.input_payload.get("call_id") != str(call.id)):
            raise HTTPException(409, detail="Workflow execution scope inconsistent")
        items.append({"job_id": str(job.id), "job_status": job.status, "job_error": job.last_error_category,
            "version_id": job.payload.get("version_id"),
            "execution": None if execution is None else {"id": str(execution.id), "status": execution.status,
                "output": execution.output_metadata, "attempts": execution.attempt_count}})
    return {"items": items, "total": total, "limit": limit, "offset": offset}
