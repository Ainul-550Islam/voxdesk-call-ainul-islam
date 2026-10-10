# File: app/api/batch_call_routes.py — Outbound batch calling campaigns, recipients, start/pause/resume/cancel, analytics, CSV import/export
"""
Outbound batch calling campaign API backed by the canonical Campaign + Lead runtime.
- Create, list, get, update, delete batch call campaigns
- Recipient management: add, list, get, update, delete, bulk retry, CSV import/export
- Lifecycle transitions: draft -> scheduled/running -> paused <-> running -> completed/cancelled
- Centralized DNC enforcement at ingestion and pre-dial via app.telephony.dnc
- Backing Campaign + Lead rows driven by app.telephony.outbound.run_campaign_step
- Distributed rate limiting, durable idempotency, and atomic AuditLog writes
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.rate_limit import enforce_tenant_rate_limit
from app.db.enterprise_models import (
    BatchCall,
    BatchRecipient,
    BatchRecipientStatus,
    BatchStatus,
)
from app.db.models import (
    Campaign,
    Environment,
    Lead,
    LeadStatus,
)
from app.db.session import get_session
from app.resilience.idempotency import get_idempotent_resource_id, store_idempotent_resource_id
from app.telephony import dnc
from app.telephony import phone as phone_util
from app.telephony.outbound import sync_batch_from_calls
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(prefix="/api/batch-calls", tags=["batch-calls"])

MAX_RECIPIENTS_PER_BATCH = 50000
MAX_RECIPIENTS_PER_REQUEST = 5000
MAX_CONCURRENCY = 100
MIN_CONCURRENCY = 1
MAX_ATTEMPTS = 10
MIN_ATTEMPTS = 1
DEFAULT_RETRY_DELAY = 3600
MIN_RETRY_DELAY = 60
MAX_RETRY_DELAY = 86400
E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ALLOWED_VOICEMAIL_ACTIONS = {"hangup", "leave_message", "transfer", "callback"}
ALLOWED_STATUS_TRANSITIONS = {
    BatchStatus.DRAFT.value: {
        BatchStatus.SCHEDULED.value,
        BatchStatus.RUNNING.value,
        BatchStatus.CANCELLED.value,
    },
    BatchStatus.SCHEDULED.value: {
        BatchStatus.RUNNING.value,
        BatchStatus.PAUSED.value,
        BatchStatus.CANCELLED.value,
    },
    BatchStatus.RUNNING.value: {
        BatchStatus.PAUSED.value,
        BatchStatus.COMPLETED.value,
        BatchStatus.CANCELLED.value,
        BatchStatus.FAILED.value,
    },
    BatchStatus.PAUSED.value: {
        BatchStatus.RUNNING.value,
        BatchStatus.CANCELLED.value,
        BatchStatus.COMPLETED.value,
    },
    BatchStatus.COMPLETED.value: set(),
    BatchStatus.CANCELLED.value: set(),
    BatchStatus.FAILED.value: {BatchStatus.RUNNING.value, BatchStatus.CANCELLED.value},
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class CallingWindowConfig(_Strict):
    start: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$", description="HH:MM 24h start")
    end: str = Field(default="20:00", pattern=r"^\d{2}:\d{2}$", description="HH:MM 24h end")
    timezone: str = Field(default="UTC", max_length=64)
    days_of_week: List[int] = Field(
        default_factory=lambda: [0, 1, 2, 3, 4, 5, 6],
        description="0=Monday .. 6=Sunday",
    )

    @field_validator("days_of_week")
    @classmethod
    def _validate_days(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("days_of_week cannot be empty")
        for d in v:
            if d < 0 or d > 6:
                raise ValueError("day must be 0..6")
        return sorted(set(v))


class RecipientInput(_Strict):
    phone: str = Field(min_length=8, max_length=32, description="E.164 phone number")
    name: str = Field(default="", max_length=120)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=100)


class BatchCallCreate(_Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    agent_id: str = Field(min_length=1, max_length=80)
    campaign_id: Optional[uuid.UUID] = None
    environment_id: Optional[uuid.UUID] = None
    concurrency: int = Field(default=5, ge=MIN_CONCURRENCY, le=MAX_CONCURRENCY)
    max_attempts: int = Field(default=3, ge=MIN_ATTEMPTS, le=MAX_ATTEMPTS)
    retry_delay_seconds: int = Field(default=DEFAULT_RETRY_DELAY, ge=MIN_RETRY_DELAY, le=MAX_RETRY_DELAY)
    voicemail_action: str = Field(default="hangup", pattern="^(hangup|leave_message|transfer|callback)$")
    voicemail_message: Optional[str] = Field(default=None, max_length=1000)
    scheduled_at: Optional[datetime] = None
    calling_window: CallingWindowConfig = Field(default_factory=CallingWindowConfig)
    recipients: List[RecipientInput] = Field(default_factory=list, max_length=MAX_RECIPIENTS_PER_REQUEST)
    check_dnc: bool = Field(default=True, description="Automatically filter recipients against DNC list")
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BatchCallUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    concurrency: Optional[int] = Field(default=None, ge=MIN_CONCURRENCY, le=MAX_CONCURRENCY)
    max_attempts: Optional[int] = Field(default=None, ge=MIN_ATTEMPTS, le=MAX_ATTEMPTS)
    retry_delay_seconds: Optional[int] = Field(default=None, ge=MIN_RETRY_DELAY, le=MAX_RETRY_DELAY)
    voicemail_action: Optional[str] = Field(default=None, pattern="^(hangup|leave_message|transfer|callback)$")
    scheduled_at: Optional[datetime] = None
    calling_window: Optional[CallingWindowConfig] = None
    metadata: Optional[Dict[str, Any]] = None


class BatchAddRecipients(_Strict):
    recipients: List[RecipientInput] = Field(min_length=1, max_length=MAX_RECIPIENTS_PER_REQUEST)
    check_dnc: bool = Field(default=True)
    skip_duplicates: bool = Field(default=True)


class RecipientUpdate(_Strict):
    name: Optional[str] = Field(default=None, max_length=120)
    custom_fields: Optional[Dict[str, Any]] = None
    status: Optional[str] = Field(
        default=None,
        pattern="^(pending|queued|completed|failed|dnc_blocked|cancelled)$",
    )


class BatchCallOut(_Strict):
    id: str
    tenant_id: str
    environment_id: Optional[str] = None
    name: str
    description: str
    agent_id: str
    campaign_id: Optional[str] = None
    status: str
    concurrency: int
    max_attempts: int
    retry_delay_seconds: int
    voicemail_action: str
    scheduled_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    calling_window: Dict[str, Any]
    total_recipients: int
    completed_recipients: int
    failed_recipients: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    meta: Dict[str, Any]


class BatchListOut(_Strict):
    batches: List[BatchCallOut]
    total: int
    limit: int
    offset: int
    has_more: bool


class RecipientOut(_Strict):
    id: str
    batch_id: str
    tenant_id: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    phone: str
    name: str
    custom_fields: Dict[str, Any]
    status: str
    attempts: int
    last_error: str
    next_attempt_at: Optional[str] = None
    call_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class RecipientListOut(_Strict):
    recipients: List[RecipientOut]
    total: int
    limit: int
    offset: int
    status_breakdown: Dict[str, int]


class BatchAnalyticsOut(_Strict):
    batch_id: str
    name: str
    status: str
    total_recipients: int
    completed: int
    failed: int
    pending: int
    queued: int
    dialing: int
    dnc_blocked: int
    window_blocked: int
    no_answer: int
    busy: int
    voicemail: int
    retry_scheduled: int
    completion_rate: float
    success_rate: float
    average_attempts: float
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    estimated_remaining_seconds: Optional[int] = None


class CsvImportRequest(_Strict):
    csv_content: str = Field(min_length=5, max_length=5_000_000, description="CSV content with phone,name,... headers")
    check_dnc: bool = Field(default=True)
    skip_duplicates: bool = Field(default=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _normalize_phone(raw: str) -> str:
    try:
        return dnc.normalize_phone(raw)
    except phone_util.InvalidPhoneNumber as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid E.164 phone number '{raw[:20]}': {exc}",
        ) from None


def _validate_window(start: str, end: str) -> None:
    if not TIME_REGEX.match(start) or not TIME_REGEX.match(end):
        raise HTTPException(status_code=422, detail="calling window start/end must be HH:MM (00:00..23:59)")
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    if (sh * 60 + sm) >= (eh * 60 + em):
        raise HTTPException(status_code=422, detail="calling window start must be strictly before end")


def _to_out(row: BatchCall) -> BatchCallOut:
    d = row.as_dict()
    return BatchCallOut(
        id=d["id"],
        tenant_id=d["tenant_id"],
        environment_id=d["environment_id"],
        name=d["name"],
        description=d["description"],
        agent_id=d["agent_id"],
        campaign_id=d["campaign_id"],
        status=d["status"],
        concurrency=d["concurrency"],
        max_attempts=d["max_attempts"],
        retry_delay_seconds=d["retry_delay_seconds"],
        voicemail_action=d["voicemail_action"],
        scheduled_at=d["scheduled_at"],
        started_at=d["started_at"],
        completed_at=d["completed_at"],
        calling_window=d["calling_window"],
        total_recipients=d["total_recipients"],
        completed_recipients=d["completed_recipients"],
        failed_recipients=d["failed_recipients"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        meta=d["meta"],
    )


def _to_recipient_out(row: BatchRecipient) -> RecipientOut:
    d = row.as_dict()
    return RecipientOut(
        id=d["id"],
        batch_id=d["batch_id"],
        tenant_id=d["tenant_id"],
        campaign_id=d.get("campaign_id"),
        lead_id=d.get("lead_id"),
        phone=d["phone"],
        name=d["name"],
        custom_fields=d["custom_fields"],
        status=d["status"],
        attempts=d["attempts"],
        last_error=d["last_error"],
        next_attempt_at=d["next_attempt_at"],
        call_id=d["call_id"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


async def _get_batch(session: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> BatchCall:
    row = await session.get(BatchCall, batch_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="batch call campaign not found")
    return row


async def _resolve_environment_id(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    explicit_env_id: uuid.UUID | None,
) -> uuid.UUID:
    if explicit_env_id is not None:
        env = await session.get(Environment, explicit_env_id)
        if env is not None and env.tenant_id == tenant_id:
            return env.id
    prod_env = (
        await session.execute(
            select(Environment)
            .where(Environment.tenant_id == tenant_id, Environment.is_default.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if prod_env is not None:
        return prod_env.id
    any_env = (
        await session.execute(
            select(Environment).where(Environment.tenant_id == tenant_id).limit(1)
        )
    ).scalar_one_or_none()
    if any_env is not None:
        return any_env.id
    env = Environment(
        tenant_id=tenant_id,
        name="Production",
        slug="production",
        kind="production",
        is_default=True,
    )
    session.add(env)
    await session.flush()
    return env.id


async def _ensure_backing_campaign(
    session: AsyncSession,
    batch: BatchCall,
) -> Campaign:
    """Create or synchronize the canonical ``Campaign`` row backing ``batch``."""
    env_id = await _resolve_environment_id(session, batch.tenant_id, batch.environment_id)
    if batch.environment_id is None:
        batch.environment_id = env_id

    campaign: Campaign | None = None
    if batch.campaign_id is not None:
        campaign = await session.get(Campaign, batch.campaign_id)
        if campaign is not None and campaign.tenant_id != batch.tenant_id:
            campaign = None

    if campaign is None:
        campaign = Campaign(
            tenant_id=batch.tenant_id,
            environment_id=env_id,
            name=batch.name,
            goal="followup",
            script_prompt=batch.description or "",
            is_active=(batch.status == BatchStatus.RUNNING.value),
            calls_per_minute=max(2, int(batch.concurrency or 5) * 2),
            batch_call_id=batch.id,
        )
        session.add(campaign)
        await session.flush()
        batch.campaign_id = campaign.id
    else:
        campaign.name = batch.name
        campaign.calls_per_minute = max(2, int(batch.concurrency or 5) * 2)
        campaign.batch_call_id = batch.id
        campaign.is_active = batch.status == BatchStatus.RUNNING.value
        await session.flush()

    return campaign


async def _ingest_recipient(
    session: AsyncSession,
    *,
    batch: BatchCall,
    campaign: Campaign,
    phone_norm: str,
    name: str,
    custom_fields: Dict[str, Any],
    check_dnc: bool,
) -> tuple[BatchRecipient, bool]:
    """Create ``BatchRecipient`` and (when not DNC-blocked) its backing ``Lead`` row."""
    blocked = False
    block_reason = ""
    if check_dnc:
        is_blk, reason = await dnc.is_blocked(
            session,
            batch.tenant_id,
            phone_norm,
            environment_id=campaign.environment_id,
            agent_id=batch.agent_id,
        )
        if is_blk:
            blocked = True
            block_reason = reason or "Blocked by tenant DNC list"

    lead_id: uuid.UUID | None = None
    if not blocked:
        lead = Lead(
            tenant_id=batch.tenant_id,
            environment_id=campaign.environment_id,
            campaign_id=campaign.id,
            name=name,
            phone=phone_norm,
            custom_fields=dict(custom_fields or {}),
            status=(
                LeadStatus.QUEUED
                if batch.status == BatchStatus.RUNNING.value
                else LeadStatus.NEW
            ),
        )
        session.add(lead)
        await session.flush()
        lead_id = lead.id

    rec = BatchRecipient(
        batch_id=batch.id,
        tenant_id=batch.tenant_id,
        campaign_id=campaign.id,
        lead_id=lead_id,
        phone=phone_norm,
        name=name,
        custom_fields=dict(custom_fields or {}),
        status=(
            BatchRecipientStatus.DNC_BLOCKED.value
            if blocked
            else (
                BatchRecipientStatus.QUEUED.value
                if batch.status == BatchStatus.RUNNING.value
                else BatchRecipientStatus.PENDING.value
            )
        ),
        last_error=block_reason,
    )
    session.add(rec)
    return rec, blocked


@router.post("", response_model=BatchCallOut, status_code=201)
async def create_batch_call(
    payload: BatchCallCreate,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/batch-calls — Create batch call campaign and backing Campaign + Lead rows."""
    try:
        await enforce_tenant_rate_limit(ctx.tenant_id, "create_batch", 30)
        _validate_window(payload.calling_window.start, payload.calling_window.end)

        idem_key = payload.idempotency_key or x_idempotency_key
        if idem_key:
            cached_id = await get_idempotent_resource_id(
                session,
                tenant_id=ctx.tenant_id,
                operation="batch_call.create",
                key=idem_key,
            )
            if cached_id:
                existing = await session.get(BatchCall, uuid.UUID(cached_id))
                if existing and existing.tenant_id == ctx.tenant_id:
                    return _to_out(existing)

        initial_status = (
            BatchStatus.SCHEDULED.value if payload.scheduled_at else BatchStatus.DRAFT.value
        )

        normalized_recipients: List[tuple[str, RecipientInput]] = []
        seen_phones: set[str] = set()
        for r in payload.recipients:
            norm = _normalize_phone(r.phone)
            if norm in seen_phones:
                continue
            seen_phones.add(norm)
            normalized_recipients.append((norm, r))

        batch = BatchCall(
            tenant_id=ctx.tenant_id,
            environment_id=payload.environment_id,
            name=payload.name,
            description=payload.description,
            agent_id=payload.agent_id,
            campaign_id=payload.campaign_id,
            status=initial_status,
            concurrency=payload.concurrency,
            max_attempts=payload.max_attempts,
            retry_delay_seconds=payload.retry_delay_seconds,
            voicemail_action=payload.voicemail_action,
            scheduled_at=payload.scheduled_at,
            calling_window_start=payload.calling_window.start,
            calling_window_end=payload.calling_window.end,
            timezone=payload.calling_window.timezone,
            total_recipients=len(normalized_recipients),
            completed_recipients=0,
            failed_recipients=0,
            created_by=ctx.user_id,
            meta={
                "days_of_week": payload.calling_window.days_of_week,
                "voicemail_message": payload.voicemail_message,
                "check_dnc": payload.check_dnc,
                "custom_metadata": payload.metadata,
            },
        )
        session.add(batch)
        await session.flush()

        campaign = await _ensure_backing_campaign(session, batch)

        dnc_blocked_count = 0
        for norm_phone, r_in in normalized_recipients:
            _, is_blocked = await _ingest_recipient(
                session,
                batch=batch,
                campaign=campaign,
                phone_norm=norm_phone,
                name=r_in.name,
                custom_fields=r_in.custom_fields,
                check_dnc=payload.check_dnc,
            )
            if is_blocked:
                dnc_blocked_count += 1

        batch.failed_recipients = dnc_blocked_count
        await session.flush()

        await record_enterprise_audit(
            session,
            ctx.tenant_id,
            ctx.user_id,
            "batch_call.created",
            {
                "batch_id": str(batch.id),
                "campaign_id": str(campaign.id),
                "name": batch.name,
                "total_recipients": len(normalized_recipients),
                "dnc_blocked": dnc_blocked_count,
            },
            resource_type="batch_call",
            resource_id=batch.id,
        )

        if idem_key:
            await store_idempotent_resource_id(
                session,
                tenant_id=ctx.tenant_id,
                operation="batch_call.create",
                key=idem_key,
                resource_type="batch_call",
                resource_id=batch.id,
                request_data={"name": payload.name, "agent_id": payload.agent_id},
            )

        await session.commit()
        await session.refresh(batch)
        return _to_out(batch)
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("", response_model=BatchListOut)
async def list_batch_calls(
    status: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None, max_length=80),
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/batch-calls — List tenant batch call campaigns."""
    filters = [BatchCall.tenant_id == ctx.tenant_id]
    if isinstance(status, str) and status:
        filters.append(BatchCall.status == status)
    if isinstance(agent_id, str) and agent_id:
        filters.append(BatchCall.agent_id == agent_id)
    if isinstance(search, str) and search:
        filters.append(BatchCall.name.ilike(f"%{search}%"))

    total_q = await session.execute(select(func.count(BatchCall.id)).where(*filters))
    total = total_q.scalar() or 0

    rows = (
        await session.execute(
            select(BatchCall)
            .where(*filters)
            .order_by(BatchCall.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    for r in rows:
        await sync_batch_from_calls(session, r)
    if rows:
        await session.commit()

    return BatchListOut(
        batches=[_to_out(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/{batch_id}", response_model=BatchCallOut)
async def get_batch_call(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_batch(session, ctx.tenant_id, batch_id)
    await sync_batch_from_calls(session, row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.patch("/{batch_id}", response_model=BatchCallOut)
async def update_batch_call(
    batch_id: uuid.UUID,
    payload: BatchCallUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """PATCH /api/batch-calls/{id} — Update campaign settings."""
    row = await _get_batch(session, ctx.tenant_id, batch_id)
    if row.status in (BatchStatus.COMPLETED.value, BatchStatus.CANCELLED.value):
        raise HTTPException(
            status_code=409, detail=f"cannot update batch in {row.status} state"
        )

    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.concurrency is not None:
        row.concurrency = payload.concurrency
    if payload.max_attempts is not None:
        row.max_attempts = payload.max_attempts
    if payload.retry_delay_seconds is not None:
        row.retry_delay_seconds = payload.retry_delay_seconds
    if payload.voicemail_action is not None:
        row.voicemail_action = payload.voicemail_action
    if payload.scheduled_at is not None:
        row.scheduled_at = payload.scheduled_at
        if row.status == BatchStatus.DRAFT.value:
            row.status = BatchStatus.SCHEDULED.value
    if payload.calling_window is not None:
        _validate_window(payload.calling_window.start, payload.calling_window.end)
        row.calling_window_start = payload.calling_window.start
        row.calling_window_end = payload.calling_window.end
        row.timezone = payload.calling_window.timezone
        meta = dict(row.meta or {})
        meta["days_of_week"] = payload.calling_window.days_of_week
        row.meta = meta
    if payload.metadata is not None:
        meta = dict(row.meta or {})
        meta["custom_metadata"] = payload.metadata
        row.meta = meta

    row.updated_at = _now()
    await _ensure_backing_campaign(session, row)
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.updated",
        {"batch_id": str(batch_id)},
        resource_type="batch_call",
        resource_id=batch_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/{batch_id}")
async def delete_batch_call(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /api/batch-calls/{id} — Delete campaign (must not be running)."""
    row = await _get_batch(session, ctx.tenant_id, batch_id)
    if row.status == BatchStatus.RUNNING.value:
        raise HTTPException(
            status_code=409,
            detail="cannot delete running batch call; pause or cancel first",
        )
    if row.campaign_id:
        camp = await session.get(Campaign, row.campaign_id)
        if camp is not None and camp.tenant_id == ctx.tenant_id:
            camp.is_active = False
            camp.batch_call_id = None
    await session.delete(row)
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.deleted",
        {"batch_id": str(batch_id)},
        resource_type="batch_call",
        resource_id=batch_id,
    )
    await session.commit()
    return {"id": str(batch_id), "deleted": True}


@router.post("/{batch_id}/recipients", response_model=dict, status_code=201)
async def add_recipients(
    batch_id: uuid.UUID,
    payload: BatchAddRecipients,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/recipients — Add recipients to batch and backing Campaign."""
    await enforce_tenant_rate_limit(ctx.tenant_id, "add_recipients", 60)
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    if batch.status in (BatchStatus.COMPLETED.value, BatchStatus.CANCELLED.value):
        raise HTTPException(
            status_code=409,
            detail=f"cannot add recipients to {batch.status} batch",
        )

    campaign = await _ensure_backing_campaign(session, batch)

    existing_q = await session.execute(
        select(BatchRecipient.phone).where(BatchRecipient.batch_id == batch_id)
    )
    existing_phones = {r[0] for r in existing_q.all()}

    if len(existing_phones) + len(payload.recipients) > MAX_RECIPIENTS_PER_BATCH:
        raise HTTPException(
            status_code=422,
            detail=f"batch limit exceeded ({MAX_RECIPIENTS_PER_BATCH} max)",
        )

    added = 0
    skipped = 0
    dnc_blocked = 0

    for r in payload.recipients:
        norm = _normalize_phone(r.phone)
        if norm in existing_phones:
            if payload.skip_duplicates:
                skipped += 1
                continue
            raise HTTPException(
                status_code=409, detail=f"duplicate phone in batch: {norm}"
            )
        existing_phones.add(norm)
        _, is_blk = await _ingest_recipient(
            session,
            batch=batch,
            campaign=campaign,
            phone_norm=norm,
            name=r.name,
            custom_fields=r.custom_fields,
            check_dnc=payload.check_dnc,
        )
        if is_blk:
            dnc_blocked += 1
        added += 1

    batch.total_recipients = len(existing_phones)
    batch.failed_recipients = (batch.failed_recipients or 0) + dnc_blocked
    batch.updated_at = _now()

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.recipients_added",
        {
            "batch_id": str(batch_id),
            "added": added,
            "skipped": skipped,
            "dnc_blocked": dnc_blocked,
        },
        resource_type="batch_call",
        resource_id=batch_id,
    )
    await session.commit()
    return {
        "batch_id": str(batch_id),
        "added": added,
        "skipped": skipped,
        "dnc_blocked": dnc_blocked,
        "total_recipients": batch.total_recipients,
    }


@router.get("/{batch_id}/recipients", response_model=RecipientListOut)
async def list_recipients(
    batch_id: uuid.UUID,
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    await sync_batch_from_calls(session, batch)
    await session.commit()

    filters = [
        BatchRecipient.batch_id == batch_id,
        BatchRecipient.tenant_id == ctx.tenant_id,
    ]
    if isinstance(status, str) and status:
        filters.append(BatchRecipient.status == status)
    if isinstance(search, str) and search:
        filters.append(
            (BatchRecipient.phone.ilike(f"%{search}%"))
            | (BatchRecipient.name.ilike(f"%{search}%"))
        )

    total_q = await session.execute(select(func.count(BatchRecipient.id)).where(*filters))
    total = total_q.scalar() or 0

    rows = (
        await session.execute(
            select(BatchRecipient)
            .where(*filters)
            .order_by(BatchRecipient.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    bd_q = await session.execute(
        select(BatchRecipient.status, func.count(BatchRecipient.id))
        .where(
            BatchRecipient.batch_id == batch_id,
            BatchRecipient.tenant_id == ctx.tenant_id,
        )
        .group_by(BatchRecipient.status)
    )
    breakdown = {st: int(cnt) for st, cnt in bd_q.all()}

    return RecipientListOut(
        recipients=[_to_recipient_out(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
        status_breakdown=breakdown,
    )


@router.patch("/{batch_id}/recipients/{recipient_id}", response_model=RecipientOut)
async def update_recipient(
    batch_id: uuid.UUID,
    recipient_id: uuid.UUID,
    payload: RecipientUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await _get_batch(session, ctx.tenant_id, batch_id)
    row = await session.get(BatchRecipient, recipient_id)
    if row is None or row.batch_id != batch_id or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="recipient not found")

    if payload.name is not None:
        row.name = payload.name
    if payload.custom_fields is not None:
        row.custom_fields = payload.custom_fields
    if payload.status is not None:
        row.status = payload.status
    row.updated_at = _now()
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.recipient_updated",
        {"batch_id": str(batch_id), "recipient_id": str(recipient_id)},
        resource_type="batch_recipient",
        resource_id=recipient_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_recipient_out(row)


@router.delete("/{batch_id}/recipients/{recipient_id}")
async def delete_recipient(
    batch_id: uuid.UUID,
    recipient_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    row = await session.get(BatchRecipient, recipient_id)
    if row is None or row.batch_id != batch_id or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="recipient not found")
    if row.status == BatchRecipientStatus.DIALING.value:
        raise HTTPException(
            status_code=409, detail="cannot delete recipient currently dialing"
        )

    if row.lead_id:
        lead = await session.get(Lead, row.lead_id)
        if lead is not None and lead.tenant_id == ctx.tenant_id:
            await session.delete(lead)

    await session.delete(row)
    batch.total_recipients = max(0, (batch.total_recipients or 1) - 1)
    batch.updated_at = _now()
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.recipient_deleted",
        {"batch_id": str(batch_id), "recipient_id": str(recipient_id)},
        resource_type="batch_recipient",
        resource_id=recipient_id,
    )
    await session.commit()
    return {"id": str(recipient_id), "deleted": True}


@router.post("/{batch_id}/recipients/retry-failed", response_model=dict)
async def retry_failed_recipients(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/recipients/retry-failed — Re-queue failed/no_answer/busy recipients."""
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    retryable_statuses = [
        BatchRecipientStatus.FAILED.value,
        BatchRecipientStatus.NO_ANSWER.value,
        BatchRecipientStatus.BUSY.value,
    ]
    rows = (
        await session.execute(
            select(BatchRecipient).where(
                BatchRecipient.batch_id == batch_id,
                BatchRecipient.tenant_id == ctx.tenant_id,
                BatchRecipient.status.in_(retryable_statuses),
                BatchRecipient.attempts < batch.max_attempts,
            )
        )
    ).scalars().all()

    requeued = 0
    for r in rows:
        r.status = BatchRecipientStatus.QUEUED.value
        r.next_attempt_at = _now()
        r.updated_at = _now()
        if r.lead_id:
            lead = await session.get(Lead, r.lead_id)
            if lead is not None and lead.tenant_id == ctx.tenant_id:
                setattr(lead, "status", LeadStatus.QUEUED)
                lead.next_attempt_at = None
        requeued += 1

    batch.failed_recipients = max(0, (batch.failed_recipients or 0) - requeued)
    batch.updated_at = _now()
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.retry_failed",
        {"batch_id": str(batch_id), "requeued": requeued},
        resource_type="batch_call",
        resource_id=batch_id,
    )
    await session.commit()
    return {"batch_id": str(batch_id), "requeued": requeued}


async def _transition(
    session: AsyncSession,
    ctx: TenantContext,
    batch_id: uuid.UUID,
    target_status: str,
) -> BatchCallOut:
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    allowed = ALLOWED_STATUS_TRANSITIONS.get(batch.status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"invalid status transition from '{batch.status}' to '{target_status}'",
        )

    campaign = await _ensure_backing_campaign(session, batch)
    now = _now()

    if target_status == BatchStatus.RUNNING.value:
        if batch.total_recipients == 0:
            raise HTTPException(
                status_code=422, detail="cannot start batch with 0 recipients"
            )
        if not batch.started_at:
            batch.started_at = now
        campaign.is_active = True

        recs = (
            await session.execute(
                select(BatchRecipient).where(
                    BatchRecipient.batch_id == batch_id,
                    BatchRecipient.tenant_id == ctx.tenant_id,
                    BatchRecipient.status.in_(
                        [
                            BatchRecipientStatus.PENDING.value,
                            BatchRecipientStatus.QUEUED.value,
                            BatchRecipientStatus.WINDOW_BLOCKED.value,
                        ]
                    ),
                )
            )
        ).scalars().all()
        for r in recs:
            # Re-check DNC immediately before queueing into the dialer
            is_blk, reason = await dnc.is_blocked(
                session,
                ctx.tenant_id,
                r.phone,
                lead_id=r.lead_id,
                environment_id=campaign.environment_id,
                agent_id=batch.agent_id,
            )
            if is_blk:
                r.status = BatchRecipientStatus.DNC_BLOCKED.value
                r.last_error = reason or "dnc_blocked"
                r.updated_at = now
                if r.lead_id:
                    lead_row = await session.get(Lead, r.lead_id)
                    if lead_row is not None:
                        setattr(lead_row, "status", LeadStatus.DNC)
                continue

            r.campaign_id = campaign.id
            if r.lead_id is None:
                lead_row = Lead(
                    tenant_id=ctx.tenant_id,
                    environment_id=campaign.environment_id,
                    campaign_id=campaign.id,
                    name=r.name,
                    phone=r.phone,
                    custom_fields=dict(r.custom_fields or {}),
                    status=LeadStatus.QUEUED,
                )
                session.add(lead_row)
                await session.flush()
                r.lead_id = lead_row.id
            else:
                lead_row = await session.get(Lead, r.lead_id)
                if lead_row is not None and lead_row.status in (LeadStatus.NEW, LeadStatus.QUEUED):
                    setattr(lead_row, "status", LeadStatus.QUEUED)
                    lead_row.campaign_id = campaign.id
            r.status = BatchRecipientStatus.QUEUED.value
            r.updated_at = now

    elif target_status == BatchStatus.PAUSED.value:
        campaign.is_active = False

    elif target_status in (BatchStatus.COMPLETED.value, BatchStatus.CANCELLED.value):
        batch.completed_at = now
        campaign.is_active = False

    old_status = batch.status
    batch.status = target_status
    batch.updated_at = now
    if target_status == BatchStatus.RUNNING.value:
        from app.telephony.outbound import run_campaign_step

        await session.flush()
        await run_campaign_step(session, campaign)
    await sync_batch_from_calls(session, batch)

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "batch_call.status_transition",
        {
            "batch_id": str(batch_id),
            "campaign_id": str(campaign.id),
            "from_status": old_status,
            "to_status": target_status,
        },
        resource_type="batch_call",
        resource_id=batch_id,
    )
    await session.commit()
    await session.refresh(batch)
    return _to_out(batch)


@router.post("/{batch_id}/start", response_model=BatchCallOut)
async def start_batch_call(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/start — Start batch campaign and activate backing Campaign."""
    return await _transition(session, ctx, batch_id, BatchStatus.RUNNING.value)


@router.post("/{batch_id}/pause", response_model=BatchCallOut)
async def pause_batch_call(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/pause — Pause running batch and deactivate backing Campaign."""
    return await _transition(session, ctx, batch_id, BatchStatus.PAUSED.value)


@router.post("/{batch_id}/resume", response_model=BatchCallOut)
async def resume_batch_call(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/resume — Resume paused batch and activate backing Campaign."""
    return await _transition(session, ctx, batch_id, BatchStatus.RUNNING.value)


@router.post("/{batch_id}/cancel", response_model=BatchCallOut)
async def cancel_batch_call(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/cancel — Cancel batch campaign and deactivate backing Campaign."""
    return await _transition(session, ctx, batch_id, BatchStatus.CANCELLED.value)


@router.get("/{batch_id}/analytics", response_model=BatchAnalyticsOut)
async def get_batch_analytics(
    batch_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/batch-calls/{id}/analytics — Progress and outcome breakdown derived from real Call rows."""
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    await sync_batch_from_calls(session, batch)
    await session.commit()

    bd_q = await session.execute(
        select(
            BatchRecipient.status,
            func.count(BatchRecipient.id),
            func.avg(BatchRecipient.attempts),
        )
        .where(
            BatchRecipient.batch_id == batch_id,
            BatchRecipient.tenant_id == ctx.tenant_id,
        )
        .group_by(BatchRecipient.status)
    )
    counts: Dict[str, int] = {}
    total_attempts_weighted = 0.0
    total_rows = 0
    for st, cnt, avg_att in bd_q.all():
        c = int(cnt)
        counts[st] = c
        total_rows += c
        total_attempts_weighted += c * float(avg_att or 0.0)

    completed = counts.get(BatchRecipientStatus.COMPLETED.value, 0)
    failed = counts.get(BatchRecipientStatus.FAILED.value, 0)
    pending = counts.get(BatchRecipientStatus.PENDING.value, 0)
    queued = counts.get(BatchRecipientStatus.QUEUED.value, 0)
    dialing = counts.get(BatchRecipientStatus.DIALING.value, 0)
    dnc_b = counts.get(BatchRecipientStatus.DNC_BLOCKED.value, 0)
    win_b = counts.get(BatchRecipientStatus.WINDOW_BLOCKED.value, 0)
    no_ans = counts.get(BatchRecipientStatus.NO_ANSWER.value, 0)
    busy = counts.get(BatchRecipientStatus.BUSY.value, 0)
    vm = counts.get(BatchRecipientStatus.VOICEMAIL.value, 0)
    retry_s = counts.get(BatchRecipientStatus.RETRY_SCHEDULED.value, 0)

    terminal = completed + failed + dnc_b + no_ans + busy
    completion_rate = round((terminal / total_rows) * 100.0, 2) if total_rows > 0 else 0.0
    success_rate = round((completed / terminal) * 100.0, 2) if terminal > 0 else 0.0
    avg_attempts = round(total_attempts_weighted / total_rows, 2) if total_rows > 0 else 0.0

    elapsed = None
    est_remaining = None
    if batch.started_at:
        end_ref = batch.completed_at or _now()
        started = batch.started_at if batch.started_at.tzinfo else batch.started_at.replace(tzinfo=timezone.utc)
        ended = end_ref if end_ref.tzinfo else end_ref.replace(tzinfo=timezone.utc)
        elapsed = max(0, int((ended - started).total_seconds()))
        if terminal > 0 and (total_rows - terminal) > 0 and elapsed > 0:
            rate_per_sec = terminal / elapsed
            if rate_per_sec > 0:
                est_remaining = int((total_rows - terminal) / rate_per_sec)

    return BatchAnalyticsOut(
        batch_id=str(batch.id),
        name=batch.name,
        status=batch.status,
        total_recipients=total_rows,
        completed=completed,
        failed=failed,
        pending=pending,
        queued=queued,
        dialing=dialing,
        dnc_blocked=dnc_b,
        window_blocked=win_b,
        no_answer=no_ans,
        busy=busy,
        voicemail=vm,
        retry_scheduled=retry_s,
        completion_rate=completion_rate,
        success_rate=success_rate,
        average_attempts=avg_attempts,
        started_at=batch.started_at.isoformat() if batch.started_at else None,
        completed_at=batch.completed_at.isoformat() if batch.completed_at else None,
        elapsed_seconds=elapsed,
        estimated_remaining_seconds=est_remaining,
    )


@router.post("/{batch_id}/import-csv", response_model=dict)
async def import_recipients_csv(
    batch_id: uuid.UUID,
    payload: CsvImportRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/batch-calls/{id}/import-csv — Import recipients from CSV string."""
    await enforce_tenant_rate_limit(ctx.tenant_id, "import_csv", 10)
    reader = csv.DictReader(io.StringIO(payload.csv_content.strip()))
    if not reader.fieldnames or "phone" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(status_code=422, detail="CSV must include a 'phone' column header")

    recipients: List[RecipientInput] = []
    for idx, row in enumerate(reader):
        if idx >= MAX_RECIPIENTS_PER_REQUEST:
            break
        normalized_row = {
            k.strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k
        }
        phone = normalized_row.pop("phone", "")
        name = normalized_row.pop("name", "")
        if not phone:
            continue
        recipients.append(RecipientInput(phone=phone, name=name, custom_fields=normalized_row))

    if not recipients:
        raise HTTPException(status_code=422, detail="no valid recipient rows found in CSV")

    return await add_recipients(
        batch_id=batch_id,
        payload=BatchAddRecipients(
            recipients=recipients,
            check_dnc=payload.check_dnc,
            skip_duplicates=payload.skip_duplicates,
        ),
        ctx=ctx,
        session=session,
    )


@router.get("/{batch_id}/export-csv")
async def export_recipients_csv(
    batch_id: uuid.UUID,
    status: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/batch-calls/{id}/export-csv — Stream recipients and outcomes as CSV."""
    batch = await _get_batch(session, ctx.tenant_id, batch_id)
    await sync_batch_from_calls(session, batch)
    await session.commit()

    filters = [
        BatchRecipient.batch_id == batch_id,
        BatchRecipient.tenant_id == ctx.tenant_id,
    ]
    if status:
        filters.append(BatchRecipient.status == status)

    rows = (
        await session.execute(
            select(BatchRecipient).where(*filters).order_by(BatchRecipient.created_at.asc())
        )
    ).scalars().all()

    fieldnames = [
        "id",
        "phone",
        "name",
        "status",
        "attempts",
        "last_error",
        "call_id",
        "lead_id",
        "created_at",
        "updated_at",
    ]

    async def _gen():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate(0)
        for r in rows:
            d = r.as_dict()
            writer.writerow({k: d.get(k) for k in fieldnames})
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _gen(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=batch_{batch_id}_recipients.csv"
        },
    )


@router.get("/stats/overview", response_model=dict)
async def batch_overview_stats(
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/batch-calls/stats/overview — Tenant-wide batch campaign summary."""
    status_q = await session.execute(
        select(BatchCall.status, func.count(BatchCall.id))
        .where(BatchCall.tenant_id == ctx.tenant_id)
        .group_by(BatchCall.status)
    )
    by_status = {st: int(cnt) for st, cnt in status_q.all()}
    total_batches = sum(by_status.values())

    rec_q = await session.execute(
        select(func.count(BatchRecipient.id)).where(BatchRecipient.tenant_id == ctx.tenant_id)
    )
    total_recipients = rec_q.scalar() or 0

    return {
        "tenant_id": str(ctx.tenant_id),
        "total_batches": total_batches,
        "by_status": by_status,
        "total_recipients": int(total_recipients),
        "at": _now_iso(),
    }
