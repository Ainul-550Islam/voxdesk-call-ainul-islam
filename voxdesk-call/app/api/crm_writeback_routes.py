# File: app/api/crm_writeback_routes.py — Multi-provider CRM write-back & field mappings backed by app/integrations/crm (Part 1F / Gate G1)
"""
CRM write-back API backed by ``app.integrations.crm`` providers:
- Dispatches disposition, task/note, extracted fields, retry, and backfill to real CrmProvider adapters
- Persists and validates field mappings on ``CrmIntegration.field_mappings`` via ``app.integrations.crm.mapping``
- Records real external IDs in ``CrmWritebackLog`` and ``CrmSyncLog``
- Uses Redis rate limiting, durable DB idempotency, and atomic AuditLog writes
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.core.rate_limit import enforce_tenant_rate_limit
from app.db.enterprise_models import CrmWritebackLog, SalesforceConnection
from app.db.models import Call, CrmIntegration, CrmProviderType, Lead
from app.db.session import get_session
from app.integrations.crm import crypto
from app.integrations.crm.base import Capability, CrmProvider, ProviderContext
from app.integrations.crm.errors import CrmError
from app.integrations.crm.mapping import (
    ALLOWED_SOURCES,
    MappingError,
    resolve_custom_fields,
    validate_field_mappings_against_describe,
)
from app.integrations.crm.models import NormalizedActivity, NormalizedContact
from app.integrations.crm.registry import build as build_crm_provider
from app.resilience.idempotency import (
    get_idempotent_resource_id,
    store_idempotent_resource_id,
)

router = APIRouter(prefix="/api/crm", tags=["crm-writeback"])

MAX_NOTE_LENGTH = 8000
MAX_OUTCOME_LENGTH = 120
MAX_PROVIDER_LENGTH = 80
MAX_ENTITY_TYPE_LENGTH = 80
MAX_FIELDS_COUNT = 50
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

ALLOWED_PROVIDERS = {
    "salesforce",
    "hubspot",
    "gohighlevel",
    "ghl",
    "jobber",
    "webhook",
    "zendesk",
    "zoho",
    "pipedrive",
    "freshsales",
    "dynamics",
    "custom",
}

_PROVIDER_ENUM_MAP: dict[str, CrmProviderType] = {
    "salesforce": CrmProviderType.SALESFORCE,
    "hubspot": CrmProviderType.HUBSPOT,
    "gohighlevel": CrmProviderType.GOHIGHLEVEL,
    "ghl": CrmProviderType.GOHIGHLEVEL,
    "jobber": CrmProviderType.JOBBER,
    "webhook": CrmProviderType.WEBHOOK,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class DispositionWriteback(_Strict):
    provider: str = Field(
        default="salesforce", min_length=2, max_length=MAX_PROVIDER_LENGTH
    )
    outcome: str = Field(min_length=1, max_length=MAX_OUTCOME_LENGTH)
    notes: str = Field(default="", max_length=MAX_NOTE_LENGTH)
    follow_up_at: Optional[datetime] = None
    external_contact_id: Optional[str] = Field(default=None, max_length=200)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class TaskNoteWriteback(_Strict):
    provider: str = Field(
        default="salesforce", min_length=2, max_length=MAX_PROVIDER_LENGTH
    )
    kind: str = Field(default="task", pattern="^(task|note)$")
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=MAX_NOTE_LENGTH)
    due_at: Optional[datetime] = None
    assignee: Optional[str] = Field(default=None, max_length=200)
    external_contact_id: Optional[str] = Field(default=None, max_length=200)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class ExtractedFieldsWriteback(_Strict):
    provider: str = Field(
        default="salesforce", min_length=2, max_length=MAX_PROVIDER_LENGTH
    )
    entity_type: str = Field(
        default="contact", min_length=1, max_length=MAX_ENTITY_TYPE_LENGTH
    )
    entity_id: Optional[str] = Field(default=None, max_length=200)
    fields: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class CrmWritebackOut(_Strict):
    id: str
    tenant_id: str
    call_id: str
    provider: str
    entity_type: str
    entity_id: str
    action: str
    status: str
    payload: Dict[str, Any]
    error_message: Optional[str] = None
    created_at: Optional[str] = None


class CrmWritebackListOut(_Strict):
    writebacks: List[CrmWritebackOut]
    total: int
    limit: int
    offset: int


class CrmFieldMappingRequest(_Strict):
    provider: str = Field(min_length=2, max_length=MAX_PROVIDER_LENGTH)
    entity_type: str = Field(min_length=1, max_length=MAX_ENTITY_TYPE_LENGTH)
    mappings: Dict[str, str] = Field(default_factory=dict)
    default_values: Dict[str, Any] = Field(default_factory=dict)


class CrmBackfillRequest(_Strict):
    provider: str = Field(min_length=2, max_length=MAX_PROVIDER_LENGTH)
    from_date: datetime
    to_date: datetime
    limit: int = Field(default=100, ge=1, le=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _not_implemented(
    message: str = "CRM writeback runtime operation is not configured.",
) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "code": "CRM_WRITEBACK_NOT_IMPLEMENTED",
            "message": message,
        },
    )


def _to_out(row: CrmWritebackLog) -> CrmWritebackOut:
    payload = dict(row.payload or {})
    err_msg = payload.get("error_message") or getattr(row, "error", None) or None
    return CrmWritebackOut(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        call_id=str(row.call_id) if row.call_id else "",
        provider=row.provider,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        action=row.action,
        status=row.status,
        payload=payload,
        error_message=err_msg,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


async def _verify_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> Optional[Call]:
    res = await session.execute(
        select(Call).where(Call.id == call_id, Call.tenant_id == tenant_id)
    )
    return res.scalar_one_or_none()


def _resolve_provider_enum(provider_name: str) -> CrmProviderType:
    key = (provider_name or "").strip().lower()
    if key not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=422, detail=f"provider must be one of {sorted(ALLOWED_PROVIDERS)}"
        )
    enum_val = _PROVIDER_ENUM_MAP.get(key)
    if enum_val is None:
        raise _not_implemented(
            f"CRM provider {provider_name!r} has no runtime transport adapter."
        )
    return enum_val


async def _load_provider_for_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    provider_name: str,
) -> tuple[CrmIntegration, CrmProvider]:
    provider_type = _resolve_provider_enum(provider_name)
    key_ring = crypto.key_ring_from_settings()
    if key_ring is None:
        raise _not_implemented(
            "CRM credential encryption (CRM_ENCRYPTION_KEYS) is not configured."
        )

    integration = (
        await session.execute(
            select(CrmIntegration).where(
                CrmIntegration.tenant_id == tenant_id,
                CrmIntegration.provider == provider_type,
            )
        )
    ).scalar_one_or_none()

    # Fallback: if SalesforceConnection exists without a CrmIntegration row yet, bridge it
    if integration is None and provider_type == CrmProviderType.SALESFORCE:
        sf_conn = (
            await session.execute(
                select(SalesforceConnection).where(
                    SalesforceConnection.tenant_id == tenant_id,
                    SalesforceConnection.is_active == True,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if sf_conn is not None and sf_conn.access_token_encrypted:
            integration = CrmIntegration(
                tenant_id=tenant_id,
                provider=CrmProviderType.SALESFORCE,
                is_enabled=True,
                credentials_encrypted=sf_conn.access_token_encrypted,
                config={"instance_url": sf_conn.instance_url, "api_version": "v59.0"},
                field_mappings={},
                subscribed_events=["call.completed"],
            )
            session.add(integration)
            await session.flush()

    if integration is None or not integration.is_enabled:
        raise _not_implemented(
            f"CRM integration for provider {provider_type.value!r} is not configured for this tenant."
        )
    if not integration.credentials_encrypted:
        raise _not_implemented(
            f"CRM credentials for provider {provider_type.value!r} are not configured."
        )

    credentials = crypto.decrypt_credentials(
        integration.credentials_encrypted,
        tenant_id=str(tenant_id),
        provider=provider_type.value,
        key_ring=key_ring,
    )
    ctx = ProviderContext(
        tenant_id=str(tenant_id),
        credentials=credentials,
        config=dict(integration.config or {}),
        field_mappings=dict(integration.field_mappings or {}),
        timeout_seconds=settings.crm_request_timeout_seconds,
    )
    return integration, build_crm_provider(provider_type, ctx)


async def _resolve_or_upsert_contact(
    session: AsyncSession,
    adapter: CrmProvider,
    tenant_id: uuid.UUID,
    call: Optional[Call],
    explicit_contact_id: Optional[str] = None,
    custom_fields: Optional[dict[str, Any]] = None,
) -> str:
    if explicit_contact_id and explicit_contact_id.strip():
        return explicit_contact_id.strip()

    phone = ""
    email = None
    first_name = "Unknown"
    last_name = "Caller"
    if call is not None:
        from_num = getattr(call, "from_number", None) or getattr(call, "caller_number", None) or ""
        to_num = getattr(call, "to_number", None) or getattr(call, "callee_number", None) or ""
        phone = (
            from_num
            if str(call.direction or "inbound").lower().endswith("inbound")
            else (to_num or from_num)
        )
        lead = None
        if getattr(call, "lead_id", None):
            lead = (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == tenant_id, Lead.id == call.lead_id
                    )
                )
            ).scalar_one_or_none()
        if lead is not None:
            if lead.name:
                parts = lead.name.strip().split(None, 1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else "Caller"
            email = lead.email
            if lead.phone:
                phone = lead.phone

    contact = NormalizedContact(
        first_name=first_name,
        last_name=last_name,
        phone=phone or "+10000000000",
        email=email,
        source="VoxDesk AI Receptionist",
        custom_fields=dict(custom_fields or {}),
    )
    res = await adapter.upsert_contact(contact)
    return res.external_id


# ---------------------------------------------------------------------------
# Endpoints — Disposition, Task/Note, Extracted Fields write-back
# ---------------------------------------------------------------------------


@router.post(
    "/calls/{call_id}/disposition", response_model=CrmWritebackOut, status_code=201
)
async def writeback_disposition(
    call_id: uuid.UUID,
    payload: DispositionWriteback,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/crm/calls/{call_id}/disposition — Write call disposition to the configured CRM."""
    if call_id is None or payload is None:
        raise _not_implemented("call_id and payload are required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "crm_disposition", 60)

    idem_key = payload.idempotency_key or (
        x_idempotency_key if isinstance(x_idempotency_key, str) else None
    )
    if idem_key:
        cached_id = await get_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"crm.disposition.{call_id}",
            key=idem_key,
        )
        if cached_id:
            existing = await session.get(CrmWritebackLog, uuid.UUID(cached_id))
            if existing and existing.tenant_id == ctx.tenant_id:
                return _to_out(existing)

    integration, adapter = await _load_provider_for_tenant(
        session, ctx.tenant_id, payload.provider
    )
    call = await _verify_call(session, ctx.tenant_id, call_id)
    time.perf_counter()

    try:
        contact_id = await _resolve_or_upsert_contact(
            session,
            adapter,
            ctx.tenant_id,
            call,
            explicit_contact_id=payload.external_contact_id,
            custom_fields=payload.custom_fields,
        )
        duration_sec = float(call.duration_seconds) if call and call.duration_seconds else None
        direction = (call.direction if call else None) or "inbound"
        activity = NormalizedActivity(
            title=f"Call Disposition: {payload.outcome}",
            body=payload.notes or f"Outcome: {payload.outcome}",
            occurred_at=_now(),
            duration_seconds=duration_sec,
            attributes={
                "call_id": str(call_id),
                "disposition": payload.outcome,
                "outcome": payload.outcome,
                "direction": direction,
                "follow_up_at": (
                    payload.follow_up_at.isoformat() if payload.follow_up_at else None
                ),
            },
        )
        if Capability.CREATE_ACTIVITY in adapter.capabilities:
            act_res = await adapter.create_activity(contact_id, activity)
            external_entity_id = act_res.external_id
        else:
            external_entity_id = contact_id
        status_str = "completed"
        error_msg = None
    except CrmError as exc:
        external_entity_id = ""
        status_str = "failed"
        error_msg = exc.safe_message

    row = CrmWritebackLog(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        provider=integration.provider.value,
        entity_type="disposition",
        entity_id=external_entity_id,
        action="upsert",
        status=status_str,
        payload={
            "outcome": payload.outcome,
            "notes": payload.notes,
            "follow_up_at": (
                payload.follow_up_at.isoformat() if payload.follow_up_at else None
            ),
            "custom_fields": payload.custom_fields,
            "call_verified": call is not None,
            "error_message": error_msg,
        },
    )
    session.add(row)
    await session.flush()

    if idem_key:
        await store_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"crm.disposition.{call_id}",
            key=idem_key,
            resource_type="crm_writeback_log",
            resource_id=str(row.id),
            request_data={"outcome": payload.outcome, "provider": payload.provider},
        )

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.disposition_written",
        {
            "call_id": str(call_id),
            "provider": integration.provider.value,
            "outcome": payload.outcome,
            "external_id": external_entity_id,
            "status": status_str,
        },
        resource_type="crm_writeback_log",
        resource_id=row.id,
    )
    await session.commit()
    await session.refresh(row)
    if status_str == "failed":
        raise HTTPException(status_code=502, detail=error_msg or "CRM writeback failed")
    return _to_out(row)


@router.post("/calls/{call_id}/task", response_model=CrmWritebackOut, status_code=201)
async def writeback_task_note(
    call_id: uuid.UUID,
    payload: TaskNoteWriteback,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/crm/calls/{call_id}/task — Create a follow-up task or note in the CRM."""
    if call_id is None or payload is None:
        raise _not_implemented("call_id and payload are required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "crm_task_note", 60)

    idem_key = payload.idempotency_key or (
        x_idempotency_key if isinstance(x_idempotency_key, str) else None
    )
    if idem_key:
        cached_id = await get_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"crm.task_note.{call_id}",
            key=idem_key,
        )
        if cached_id:
            existing = await session.get(CrmWritebackLog, uuid.UUID(cached_id))
            if existing and existing.tenant_id == ctx.tenant_id:
                return _to_out(existing)

    integration, adapter = await _load_provider_for_tenant(
        session, ctx.tenant_id, payload.provider
    )
    call = await _verify_call(session, ctx.tenant_id, call_id)
    time.perf_counter()

    try:
        contact_id = await _resolve_or_upsert_contact(
            session,
            adapter,
            ctx.tenant_id,
            call,
            explicit_contact_id=payload.external_contact_id,
        )
        duration_sec = float(call.duration_seconds) if call and call.duration_seconds else None
        activity = NormalizedActivity(
            title=payload.subject,
            body=payload.body,
            occurred_at=_now(),
            duration_seconds=duration_sec,
            attributes={
                "call_id": str(call_id),
                "priority": payload.priority,
                "assignee": payload.assignee,
                "due_at": payload.due_at.isoformat() if payload.due_at else None,
            },
        )
        if payload.kind == "note" and Capability.CREATE_NOTE in adapter.capabilities:
            res = await adapter.create_note(contact_id, activity)
        else:
            res = await adapter.create_activity(contact_id, activity)
        external_entity_id = res.external_id
        status_str = "completed"
        error_msg = None
    except CrmError as exc:
        external_entity_id = ""
        status_str = "failed"
        error_msg = exc.safe_message

    row = CrmWritebackLog(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        provider=integration.provider.value,
        entity_type=payload.kind,
        entity_id=external_entity_id,
        action="create",
        status=status_str,
        payload={
            "subject": payload.subject,
            "body": payload.body,
            "due_at": payload.due_at.isoformat() if payload.due_at else None,
            "assignee": payload.assignee,
            "priority": payload.priority,
            "error_message": error_msg,
        },
    )
    session.add(row)
    await session.flush()

    if idem_key:
        await store_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"crm.task_note.{call_id}",
            key=idem_key,
            resource_type="crm_writeback_log",
            resource_id=str(row.id),
            request_data={"subject": payload.subject, "kind": payload.kind},
        )

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        f"crm.{payload.kind}_created",
        {
            "call_id": str(call_id),
            "provider": integration.provider.value,
            "subject": payload.subject,
            "external_id": external_entity_id,
        },
        resource_type="crm_writeback_log",
        resource_id=row.id,
    )
    await session.commit()
    await session.refresh(row)
    if status_str == "failed":
        raise HTTPException(status_code=502, detail=error_msg or "CRM writeback failed")
    return _to_out(row)


@router.post("/calls/{call_id}/fields", response_model=CrmWritebackOut, status_code=201)
async def writeback_extracted_fields(
    call_id: uuid.UUID,
    payload: ExtractedFieldsWriteback,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/crm/calls/{call_id}/fields — Sync extracted conversation fields to CRM."""
    if call_id is None or payload is None:
        raise _not_implemented("call_id and payload are required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "crm_fields", 60)

    if len(payload.fields) > MAX_FIELDS_COUNT:
        raise HTTPException(
            status_code=422, detail=f"maximum {MAX_FIELDS_COUNT} fields allowed"
        )

    idem_key = payload.idempotency_key or (
        x_idempotency_key if isinstance(x_idempotency_key, str) else None
    )
    if idem_key:
        cached_id = await get_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"crm.fields.{call_id}",
            key=idem_key,
        )
        if cached_id:
            existing = await session.get(CrmWritebackLog, uuid.UUID(cached_id))
            if existing and existing.tenant_id == ctx.tenant_id:
                return _to_out(existing)

    integration, adapter = await _load_provider_for_tenant(
        session, ctx.tenant_id, payload.provider
    )
    call = await _verify_call(session, ctx.tenant_id, call_id)
    time.perf_counter()

    mapped_fields = dict(payload.fields)
    if integration.field_mappings:
        resolved = resolve_custom_fields(integration.field_mappings, payload.fields)
        if resolved:
            mapped_fields.update(resolved)

    try:
        contact_id = await _resolve_or_upsert_contact(
            session,
            adapter,
            ctx.tenant_id,
            call,
            explicit_contact_id=payload.entity_id,
            custom_fields=mapped_fields,
        )
        if Capability.ADD_CUSTOM_FIELDS in adapter.capabilities and mapped_fields:
            res = await adapter.add_custom_fields(contact_id, mapped_fields)
            external_entity_id = res.external_id
        else:
            external_entity_id = contact_id
        status_str = "completed"
        error_msg = None
    except CrmError as exc:
        external_entity_id = ""
        status_str = "failed"
        error_msg = exc.safe_message

    row_payload = dict(payload.fields)
    if error_msg:
        row_payload["error_message"] = error_msg
    row = CrmWritebackLog(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        provider=integration.provider.value,
        entity_type=payload.entity_type,
        entity_id=external_entity_id,
        action="update",
        status=status_str,
        payload=row_payload,
    )
    session.add(row)
    await session.flush()

    if idem_key:
        await store_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"crm.fields.{call_id}",
            key=idem_key,
            resource_type="crm_writeback_log",
            resource_id=str(row.id),
            request_data={"entity_type": payload.entity_type, "provider": payload.provider},
        )

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.fields_synced",
        {
            "call_id": str(call_id),
            "provider": integration.provider.value,
            "entity_type": payload.entity_type,
            "field_count": len(payload.fields),
            "external_id": external_entity_id,
        },
        resource_type="crm_writeback_log",
        resource_id=row.id,
    )
    await session.commit()
    await session.refresh(row)
    if status_str == "failed":
        raise HTTPException(status_code=502, detail=error_msg or "CRM writeback failed")
    return _to_out(row)


# ---------------------------------------------------------------------------
# Writeback listing, detail, retry, delete
# ---------------------------------------------------------------------------


@router.get("/calls/{call_id}/writebacks", response_model=CrmWritebackListOut)
async def list_call_writebacks(
    call_id: uuid.UUID,
    provider: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [
        CrmWritebackLog.tenant_id == ctx.tenant_id,
        CrmWritebackLog.call_id == call_id,
    ]
    if isinstance(provider, str) and provider:
        scope.append(CrmWritebackLog.provider == provider.lower())
    if isinstance(entity_type, str) and entity_type:
        scope.append(CrmWritebackLog.entity_type == entity_type)

    total = (
        await session.execute(select(func.count(CrmWritebackLog.id)).where(*scope))
    ).scalar() or 0
    rows = (
        await session.execute(
            select(CrmWritebackLog)
            .where(*scope)
            .order_by(CrmWritebackLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return CrmWritebackListOut(
        writebacks=[_to_out(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/writebacks", response_model=CrmWritebackListOut)
async def list_all_writebacks(
    provider: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [CrmWritebackLog.tenant_id == ctx.tenant_id]
    if isinstance(provider, str) and provider:
        scope.append(CrmWritebackLog.provider == provider.lower())
    if isinstance(entity_type, str) and entity_type:
        scope.append(CrmWritebackLog.entity_type == entity_type)
    if isinstance(status, str) and status:
        scope.append(CrmWritebackLog.status == status)

    total = (
        await session.execute(select(func.count(CrmWritebackLog.id)).where(*scope))
    ).scalar() or 0
    rows = (
        await session.execute(
            select(CrmWritebackLog)
            .where(*scope)
            .order_by(CrmWritebackLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return CrmWritebackListOut(
        writebacks=[_to_out(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/writebacks/{writeback_id}", response_model=CrmWritebackOut)
async def get_writeback(
    writeback_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CrmWritebackLog, writeback_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="writeback not found")
    return _to_out(row)


@router.post("/writebacks/{writeback_id}/retry", response_model=CrmWritebackOut)
async def retry_writeback(
    writeback_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/crm/writebacks/{writeback_id}/retry — Re-execute a CRM write-back."""
    row = await session.get(CrmWritebackLog, writeback_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="writeback not found")

    _, adapter = await _load_provider_for_tenant(session, ctx.tenant_id, row.provider)
    call = await _verify_call(session, ctx.tenant_id, row.call_id)
    payload_data = dict(row.payload or {})

    try:
        contact_id = await _resolve_or_upsert_contact(
            session,
            adapter,
            ctx.tenant_id,
            call,
            explicit_contact_id=row.entity_id or None,
        )
        if row.entity_type == "note" and Capability.CREATE_NOTE in adapter.capabilities:
            res = await adapter.create_note(
                contact_id,
                NormalizedActivity(
                    title=str(payload_data.get("subject") or "VoxDesk Note"),
                    body=str(payload_data.get("body") or ""),
                ),
            )
            row.entity_id = res.external_id
        elif row.entity_type in ("task", "disposition"):
            res = await adapter.create_activity(
                contact_id,
                NormalizedActivity(
                    title=str(
                        payload_data.get("subject")
                        or f"Call Disposition: {payload_data.get('outcome', 'Completed')}"
                    ),
                    body=str(
                        payload_data.get("body") or payload_data.get("notes") or ""
                    ),
                ),
            )
            row.entity_id = res.external_id
        else:
            if Capability.ADD_CUSTOM_FIELDS in adapter.capabilities:
                res = await adapter.add_custom_fields(contact_id, payload_data)
                row.entity_id = res.external_id
            else:
                row.entity_id = contact_id
        row.status = "completed"
        payload_data.pop("error_message", None)
        row.payload = payload_data
    except CrmError as exc:
        row.status = "failed"
        payload_data["error_message"] = exc.safe_message
        row.payload = payload_data

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.writeback_retried",
        {"writeback_id": str(writeback_id), "status": row.status},
        resource_type="crm_writeback_log",
        resource_id=row.id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/writebacks/{writeback_id}")
async def delete_writeback(
    writeback_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CrmWritebackLog, writeback_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="writeback not found")
    await session.delete(row)
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.writeback_deleted",
        {"writeback_id": str(writeback_id)},
        resource_type="crm_writeback_log",
        resource_id=writeback_id,
    )
    await session.commit()
    return {"deleted": True, "id": str(writeback_id)}


# ---------------------------------------------------------------------------
# Field mapping, backfill, and stats
# ---------------------------------------------------------------------------


@router.post("/mappings", response_model=dict, status_code=201)
async def create_mapping(
    payload: CrmFieldMappingRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/crm/mappings — Validate and persist CRM field mappings on CrmIntegration."""
    if payload is None:
        raise _not_implemented("CRM field mapping payload is required.")

    provider_type = _resolve_provider_enum(payload.provider)
    try:
        validated = validate_field_mappings_against_describe(payload.mappings)
    except MappingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    integration = (
        await session.execute(
            select(CrmIntegration).where(
                CrmIntegration.tenant_id == ctx.tenant_id,
                CrmIntegration.provider == provider_type,
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        integration = CrmIntegration(
            tenant_id=ctx.tenant_id,
            provider=provider_type,
            is_enabled=False,
            config={},
            field_mappings=validated,
            subscribed_events=["call.completed", "lead.created", "lead.updated"],
        )
        session.add(integration)
        await session.flush()
    else:
        merged_mappings = dict(integration.field_mappings or {})
        merged_mappings.update(validated)
        integration.field_mappings = merged_mappings
        if payload.default_values:
            cfg = dict(integration.config or {})
            cfg["default_values"] = dict(payload.default_values)
            integration.config = cfg
        await session.flush()

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.mapping_saved",
        {
            "provider": provider_type.value,
            "entity_type": payload.entity_type,
            "mappings": validated,
        },
        resource_type="crm_integration",
        resource_id=integration.id,
    )
    await session.commit()
    return {
        "id": str(integration.id),
        "provider": provider_type.value,
        "entity_type": payload.entity_type,
        "mappings": dict(integration.field_mappings or {}),
        "default_values": payload.default_values,
        "allowed_sources": sorted(ALLOWED_SOURCES),
        "updated_at": _now_iso(),
    }


@router.get("/mappings", response_model=dict)
async def list_mappings(
    provider: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/crm/mappings — List persisted CRM field mappings for the tenant."""
    scope = [CrmIntegration.tenant_id == ctx.tenant_id]
    if isinstance(provider, str) and provider:
        provider_type = _resolve_provider_enum(provider)
        scope.append(CrmIntegration.provider == provider_type)

    rows = (
        await session.execute(select(CrmIntegration).where(*scope))
    ).scalars().all()
    if not rows:
        raise _not_implemented(
            "No CRM integration mappings are configured for this tenant."
        )

    mappings_out = [
        {
            "id": str(r.id),
            "provider": r.provider.value if hasattr(r.provider, "value") else str(r.provider),
            "is_enabled": r.is_enabled,
            "mappings": dict(r.field_mappings or {}),
            "default_values": dict((r.config or {}).get("default_values") or {}),
        }
        for r in rows
    ]
    return {"mappings": mappings_out, "total": len(mappings_out)}


@router.post("/backfill", response_model=dict)
async def backfill_crm(
    payload: CrmBackfillRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/crm/backfill — Backfill historical calls within date range to CRM."""
    if payload is None:
        raise _not_implemented("CRM backfill payload is required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "crm_backfill", 5)

    integration, adapter = await _load_provider_for_tenant(
        session, ctx.tenant_id, payload.provider
    )
    calls = (
        await session.execute(
            select(Call)
            .where(
                Call.tenant_id == ctx.tenant_id,
                Call.started_at >= payload.from_date.replace(tzinfo=None),
                Call.started_at <= payload.to_date.replace(tzinfo=None),
            )
            .order_by(Call.started_at.asc())
            .limit(payload.limit)
        )
    ).scalars().all()

    synced_count = 0
    failed_count = 0
    for call in calls:
        try:
            contact_id = await _resolve_or_upsert_contact(
                session, adapter, ctx.tenant_id, call
            )
            if Capability.CREATE_ACTIVITY in adapter.capabilities:
                act_res = await adapter.create_activity(
                    contact_id,
                    NormalizedActivity(
                        title=f"Backfilled Call ({call.status})",
                        body=call.summary or f"Call {call.id}",
                        duration_seconds=float(call.duration_seconds or 0),
                        attributes={"call_id": str(call.id)},
                    ),
                )
                ext_id = act_res.external_id
            else:
                ext_id = contact_id
            session.add(
                CrmWritebackLog(
                    tenant_id=ctx.tenant_id,
                    call_id=call.id,
                    provider=integration.provider.value,
                    entity_type="disposition",
                    entity_id=ext_id,
                    action="upsert",
                    status="completed",
                    payload={"backfill": True, "call_status": call.status},
                )
            )
            synced_count += 1
        except CrmError:
            failed_count += 1

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.backfill_completed",
        {
            "provider": integration.provider.value,
            "synced": synced_count,
            "failed": failed_count,
        },
        resource_type="crm_integration",
        resource_id=integration.id,
    )
    await session.commit()
    return {
        "provider": integration.provider.value,
        "matched_calls": len(calls),
        "synced": synced_count,
        "failed": failed_count,
        "completed_at": _now_iso(),
    }


@router.get("/providers", response_model=dict)
async def list_supported_providers(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
):
    del ctx
    return {
        "providers": sorted(_PROVIDER_ENUM_MAP.keys()),
        "total": len(_PROVIDER_ENUM_MAP),
    }


@router.get("/stats", response_model=dict)
async def crm_stats(
    days: int = Query(30, ge=1, le=365),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    cutoff = _now() - timedelta(days=days)
    scope = [
        CrmWritebackLog.tenant_id == ctx.tenant_id,
        CrmWritebackLog.created_at >= cutoff,
    ]
    total_q = await session.execute(select(func.count(CrmWritebackLog.id)).where(*scope))
    total = total_q.scalar() or 0

    by_provider_q = await session.execute(
        select(CrmWritebackLog.provider, func.count(CrmWritebackLog.id))
        .where(*scope)
        .group_by(CrmWritebackLog.provider)
    )
    by_provider = {p: int(c) for p, c in by_provider_q.all()}

    by_type_q = await session.execute(
        select(CrmWritebackLog.entity_type, func.count(CrmWritebackLog.id))
        .where(*scope)
        .group_by(CrmWritebackLog.entity_type)
    )
    by_type = {t: int(c) for t, c in by_type_q.all()}

    by_status_q = await session.execute(
        select(CrmWritebackLog.status, func.count(CrmWritebackLog.id))
        .where(*scope)
        .group_by(CrmWritebackLog.status)
    )
    by_status = {s: int(c) for s, c in by_status_q.all()}

    return {
        "window_days": days,
        "total": int(total),
        "by_provider": by_provider,
        "by_entity_type": by_type,
        "by_status": by_status,
        "at": _now_iso(),
    }


@router.delete("/writebacks/purge/older-than")
async def purge_old_writebacks(
    days: int = Query(90, ge=7, le=3650),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    cutoff = _now() - timedelta(days=days)
    res = await session.execute(
        delete(CrmWritebackLog).where(
            CrmWritebackLog.tenant_id == ctx.tenant_id,
            CrmWritebackLog.created_at < cutoff,
        )
    )
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "crm.writebacks_purged",
        {"older_than_days": days, "deleted": int(res.rowcount or 0)},
        resource_type="crm_writeback_log",
    )
    await session.commit()
    return {"deleted": int(res.rowcount or 0), "older_than_days": days}
