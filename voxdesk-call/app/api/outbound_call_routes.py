# File: app/api/outbound_call_routes.py — Missing P0 APIs: outbound call, web call, call control pause/resume/end/reopen, DTMF send-digit, with RBAC/idempotency/tenant isolation
"""
Outbound Call API, Web Call API, Call Control API, Live DTMF API.
Closes gaps:
1. Single outbound call API missing — POST /calls with real provider creation
2. Browser/Web call API missing — POST /web-calls + secure session/token lifecycle
3. Active call control API incomplete — pause/resume/end/reopen
9. Live DTMF / digit-control API missing — active call DTMF/send-digit

audit, telemetry, rate limiting, idempotency, DNC, calling window, provider integration,
bulk operations, analytics, and secure token lifecycle.

Architecture reuse:
- app.telephony.outbound.place_call for canonical campaign->lead flow
- app.telephony.phone for validation/redaction
- app.leads.consent for voice consent
- app.auth.dependencies for tenant isolation and RBAC
- app.db.models.Call for persistence
- app.tenancy.isolation for hierarchy checks
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum as PyEnum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    Lead,
    LeadStatus,
    RequestIdempotencyReceipt,
)
from app.db.session import get_session
from app.environments.membership import resolve as resolve_environment_membership
from app.environments.resource_scope import resolve_scope
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    claim_request,
    complete_request,
    fail_request,
)
from app.telephony import dialer_limits, dnc, phone as phone_util
from app.core.logging import log
from app.core.rate_limit import allow_identity_action

router = APIRouter(prefix="/api/calls", tags=["calls-control"])

# ---------------------------------------------------------------------------
# Constants & Config
# ---------------------------------------------------------------------------

MAX_IDEMPOTENCY_KEY_LENGTH = 128
MIN_IDEMPOTENCY_KEY_LENGTH = 8
WEB_TOKEN_TTL_MINUTES = 15
WEB_TOKEN_REFRESH_WINDOW_MINUTES = 5
MAX_DTMF_DIGITS = 32
MAX_BULK_SIZE = 100
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
E164_LOOSE_REGEX = re.compile(r"^\+?[\d\s\-\(\)]{8,20}$")
DTMF_REGEX = re.compile(r"^[0-9#*wW]+$")
OUTBOUND_RATE_LIMIT_PER_MINUTE = 60
WEB_CALL_RATE_LIMIT_PER_MINUTE = 30
DTMF_RATE_LIMIT_PER_MINUTE = 20

class CallAction(str, PyEnum):
    PAUSE = "pause"
    RESUME = "resume"
    END = "end"
    REOPEN = "reopen"
    DTMF = "dtmf"
    CANCEL = "cancel"
    RETRY = "retry"

class OutboundProvider(str, PyEnum):
    TWILIO = "twilio"
    TELNYX = "telnyx"
    MOCK = "mock"

class WebCallStatus(str, PyEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    COMPLETED = "completed"

# ---------------------------------------------------------------------------
# Base models
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class OutboundCallRequest(_Strict):
    to: str = Field(min_length=8, max_length=20, description="E.164 destination")
    from_number: Optional[str] = Field(default=None, description="Optional caller ID override")
    agent_id: Optional[str] = Field(default=None, max_length=80)
    lead_id: Optional[uuid.UUID] = None
    campaign_id: Optional[uuid.UUID] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    provider: Optional[str] = Field(default=None, description="Provider override: twilio/telnyx/mock")
    timeout_seconds: int = Field(default=25, ge=5, le=60)
    record: bool = Field(default=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("to")
    @classmethod
    def validate_to(cls, v: str) -> str:
        if not v:
            raise ValueError("to is required")
        # Normalize
        normalized = re.sub(r"[\s\-\(\)]", "", v)
        if not normalized.startswith("+"):
            # Allow US numbers without +1
            if len(normalized) == 10 and normalized.isdigit():
                normalized = f"+1{normalized}"
            elif len(normalized) == 11 and normalized.startswith("1"):
                normalized = f"+{normalized}"
        if not E164_REGEX.match(normalized):
            # Loose check then attempt phone_util
            if not phone_util.is_valid(normalized):
                raise ValueError(f"invalid E.164 phone: {v}")
        return normalized

    @field_validator("from_number")
    @classmethod
    def validate_from(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        normalized = re.sub(r"[\s\-\(\)]", "", v)
        if not normalized.startswith("+"):
            if len(normalized) == 10 and normalized.isdigit():
                normalized = f"+1{normalized}"
        return normalized

class WebCallRequest(_Strict):
    agent_id: str = Field(min_length=1, max_length=80)
    customer_name: Optional[str] = Field(default=None, max_length=120)
    customer_email: Optional[str] = Field(default=None, max_length=200)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    ttl_minutes: int = Field(default=15, ge=5, le=120)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class WebCallSessionOut(_Strict):
    id: str
    call_id: str
    agent_id: str
    token: str
    expires_at: str
    status: str
    refresh_token: Optional[str] = None

class CallControlRequest(_Strict):
    reason: Optional[str] = Field(default=None, max_length=500)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DtmfRequest(_Strict):
    digits: str = Field(min_length=1, max_length=32, pattern=r"^[0-9#*wW]+$")
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    duration_ms: int = Field(default=100, ge=50, le=1000)
    gap_ms: int = Field(default=100, ge=0, le=1000)

class OutboundCallOut(_Strict):
    id: str
    call_sid: str
    to: str
    from_number: str
    direction: str
    status: str
    agent_id: Optional[str] = None
    lead_id: Optional[str] = None
    created_at: str
    provider: Optional[str] = None
    idempotency_key: Optional[str] = None

class OutboundCallListOut(_Strict):
    calls: List[OutboundCallOut]
    total: int
    limit: int
    offset: int

class OutboundCallDetailOut(_Strict):
    id: str
    call_sid: str
    to: str
    from_number: str
    direction: str
    status: str
    agent_id: Optional[str] = None
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    duration_seconds: Optional[int] = None
    created_at: str
    updated_at: Optional[str] = None
    ended_at: Optional[str] = None
    provider: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class BulkOutboundRequest(_Strict):
    calls: List[OutboundCallRequest] = Field(min_length=1, max_length=100)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    batch_name: Optional[str] = Field(default=None, max_length=200)

class BulkOutboundOut(_Strict):
    batch_id: str
    total: int
    accepted: int
    rejected: int
    results: List[Dict[str, Any]]

class WebCallListOut(_Strict):
    sessions: List[WebCallSessionOut]
    total: int
    limit: int
    offset: int

class CallAnalyticsOut(_Strict):
    call_id: str
    duration_seconds: Optional[int] = None
    status: str
    provider: Optional[str] = None
    cost_cents: Optional[int] = None
    recording_url: Optional[str] = None
    transcript_excerpt: Optional[str] = None

# ---------------------------------------------------------------------------
# Helpers — time, idempotency, rate limit, phone, audit
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now().isoformat()

async def _check_rate_limit(tenant_id: uuid.UUID, action: str, limit_per_minute: int) -> None:
    allowed = await allow_identity_action(
        action=f"call:{action}",
        who=str(tenant_id),
        limit=limit_per_minute,
        window=60.0,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Too many call operations"},
            headers={"Retry-After": "60"},
        )

def _normalize_phone(phone: str) -> str:
    return dnc.normalize_phone(phone)

def _redact_phone(phone: str) -> str:
    if not phone or len(phone) < 4:
        return "***"
    return phone[:3] + "***" + phone[-2:]

def _validate_e164_strict(phone: str) -> bool:
    if not phone:
        return False
    return bool(E164_REGEX.match(phone)) or phone_util.is_valid(phone)

def _audit(event: str, **kwargs: Any) -> None:
    log.info(event, **kwargs)


async def _record_call_audit(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    environment_id: uuid.UUID,
    event_type: str,
    result: str,
    call_id: uuid.UUID,
    detail: Dict[str, Any],
) -> None:
    """Stage a durable call event in the same transaction as the mutation."""
    actor_type = (
        ctx.auth_method
        if ctx.auth_method in {"api_key", "service_account", "scim"}
        else "human"
    )
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_type=actor_type,
        actor_email=ctx.user.email,
        environment_id=environment_id,
        event_type=event_type,
        resource_type="call",
        resource_id=call_id,
        result=result,
        detail=detail,
    )


def _provider_from_request(requested: Optional[str]) -> OutboundProvider:
    if not requested or not requested.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "telephony_provider_required", "message": "A supported telephony provider is required."},
        )
    try:
        provider = OutboundProvider(requested.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "telephony_provider_unsupported", "message": "The requested telephony provider is unsupported."},
        ) from None
    if provider == OutboundProvider.MOCK:
        raise HTTPException(
            status_code=501,
            detail={"code": "telephony_mock_not_implemented", "message": "The mock provider is not a live telephony provider."},
        )
    if provider == OutboundProvider.TELNYX:
        raise HTTPException(
            status_code=501,
            detail={"code": "telnyx_legacy_route_not_implemented", "message": "Use the versioned telephony runtime for a configured provider adapter."},
        )
    return provider

async def _scope_for_call_request(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    permission: Permission,
    for_write: bool = False,
):
    scope = await resolve_scope(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        explicit_environment_id=ctx.environment_id,
        for_write=for_write,
    )
    access = await resolve_environment_membership(session, ctx.user, scope, ctx.tenant)
    if (
        not access.allowed
        or access.role is None
        or not has_permission(access.role, permission)
    ):
        raise HTTPException(status_code=403, detail="permission denied in this environment")
    return scope


async def _get_call(
    session: AsyncSession,
    ctx: TenantContext,
    call_id: uuid.UUID,
    *,
    permission: Permission = Permission.CALL_READ,
    for_write: bool = False,
) -> Call:
    scope = await _scope_for_call_request(
        session, ctx, permission=permission, for_write=for_write
    )
    row = await session.scalar(
        select(Call).where(
            Call.id == call_id,
            Call.tenant_id == ctx.tenant_id,
            Call.environment_id == scope.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")
    return row


async def _claim_route_idempotency(
    session: AsyncSession,
    *,
    ctx: TenantContext,
    environment_id: uuid.UUID,
    operation: str,
    key: str | None,
    request_data: Any,
):
    if not key or len(key.strip()) < MIN_IDEMPOTENCY_KEY_LENGTH or len(key.strip()) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="a printable Idempotency-Key of 8 to 128 characters is required")
    try:
        return await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            environment_id=environment_id,
            operation=operation,
            key=key.strip(),
            request_data=request_data,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_conflict", "message": "Idempotency-Key was already used for a different request."},
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_in_progress", "message": "The request is in progress or requires reconciliation."},
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_previous_failure", "message": "The prior request failed; use a new key for a new attempt."},
        ) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Idempotency-Key is invalid") from None

def _call_to_out(call: Call, agent_id: Optional[str] = None, lead_id: Optional[str] = None, provider: Optional[str] = None, idem_key: Optional[str] = None) -> OutboundCallOut:
    return OutboundCallOut(
        id=str(call.id),
        call_sid=call.call_sid,
        to=call.to_number,
        from_number=call.from_number,
        direction=call.direction.value if hasattr(call.direction, "value") else str(call.direction),
        status=call.status.value if hasattr(call.status, "value") else str(call.status),
        agent_id=agent_id,
        lead_id=lead_id,
        created_at=call.started_at.isoformat() if call.started_at else _now_iso(),
        provider=provider,
        idempotency_key=None,
    )

def _call_to_detail(call: Call) -> OutboundCallDetailOut:
    duration = None
    if call.started_at and call.ended_at:
        try:
            duration = int((call.ended_at - call.started_at).total_seconds())
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            duration = None
    return OutboundCallDetailOut(
        id=str(call.id),
        call_sid=call.call_sid,
        to=call.to_number,
        from_number=call.from_number,
        direction=call.direction.value if hasattr(call.direction, "value") else str(call.direction),
        status=call.status.value if hasattr(call.status, "value") else str(call.status),
        lead_id=str(call.lead_id) if call.lead_id else None,
        campaign_id=None,
        duration_seconds=duration,
        created_at=call.started_at.isoformat() if call.started_at else _now_iso(),
        updated_at=call.updated_at.isoformat() if hasattr(call, "updated_at") and call.updated_at else None,
        ended_at=call.ended_at.isoformat() if call.ended_at else None,
        provider=None,
        metadata={},
    )

# ---------------------------------------------------------------------------
# Compliance helpers — DNC, calling window, consent
# ---------------------------------------------------------------------------

async def _check_dnc(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    phone: str,
    lead_id: Optional[uuid.UUID] = None,
    *,
    environment_id: uuid.UUID | None = None,
    agent_id: str = "",
) -> None:
    """Fail closed on centralized tenant/global/prefix DNC data, lead state, and voice consent."""
    from app.leads.consent import voice_denied

    verdict = await dnc.evaluate_dnc(
        session,
        tenant_id,
        phone,
        environment_id=environment_id,
        agent_id=agent_id,
    )
    if verdict.blocked:
        raise HTTPException(status_code=403, detail="phone is on do-not-call list")

    if lead_id is None:
        return
    lead_filters = [Lead.id == lead_id, Lead.tenant_id == tenant_id]
    if environment_id is not None:
        lead_filters.append(Lead.environment_id == environment_id)
    lead = await session.scalar(select(Lead).where(*lead_filters))
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if lead.status == LeadStatus.DNC:
        raise HTTPException(status_code=403, detail="lead is on do-not-call")
    if await voice_denied(
        session,
        tenant_id,
        lead.id,
        environment_id=lead.environment_id,
    ):
        raise HTTPException(status_code=403, detail="voice consent denied")

def _check_calling_window(tenant: Any) -> None:
    from app.telephony.outbound import is_call_window_open

    if not is_call_window_open(tenant):
        raise HTTPException(status_code=422, detail="outside calling window")


async def _check_dialer_policy(
    session: AsyncSession,
    tenant: Any,
    *,
    agent_id: str = "",
    campaign: Any = None,
) -> None:
    _check_calling_window(tenant)
    win = await dialer_limits.check_calling_window(
        session, tenant.id, agent_id=agent_id
    )
    if not win.allowed:
        raise HTTPException(status_code=422, detail="outside calling window")
    conc = await dialer_limits.check_concurrency_and_rate(
        session, tenant.id, agent_id=agent_id, campaign=campaign
    )
    if not conc.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": conc.reason or "concurrency_limit_exceeded",
                "message": "Outbound concurrency or rate limit exceeded",
            },
        )

async def _resolve_caller_id(tenant: Any, requested: Optional[str]) -> str:
    if requested:
        normalized = _normalize_phone(requested)
        if not _validate_e164_strict(normalized):
            raise HTTPException(status_code=422, detail="invalid from_number")
        return normalized
    # Tenant defaults
    for attr in ("outbound_caller_id", "twilio_number", "primary_phone"):
        if hasattr(tenant, attr):
            val = getattr(tenant, attr)
            if val:
                return val
    raise HTTPException(status_code=422, detail="no caller ID configured")

# ---------------------------------------------------------------------------
# Provider dial helpers
# ---------------------------------------------------------------------------

def _require_twilio_ready() -> str:
    """Validate live credentials and a safe public HTTPS callback origin."""
    from urllib.parse import urlsplit

    from app.core.config import settings

    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise HTTPException(
            status_code=503,
            detail={"code": "telephony_provider_not_configured", "message": "Twilio live calling is not configured."},
        )
    base_url = str(settings.public_base_url or "").rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "telephony_public_url_not_configured", "message": "A valid HTTPS public callback URL is required."},
        )
    return base_url


async def _attempt_provider_dial(
    session: AsyncSession,
    tenant: Any,
    call: Call | None,
    to: str,
    from_number: str,
    timeout_seconds: int = 25,
    record: bool = True,
    lead_id: Optional[uuid.UUID] = None,
    provider_override: Optional[str] = None,
) -> str:
    """Create a live Twilio call and return only the SID issued by Twilio."""
    del session, call  # These are retained in the helper signature for compatibility.
    provider = _provider_from_request(provider_override)
    if provider != OutboundProvider.TWILIO:
        raise HTTPException(
            status_code=501,
            detail={"code": "telephony_provider_not_implemented", "message": "This legacy route supports only configured Twilio calls."},
        )

    base_url = _require_twilio_ready()

    from app.telephony.outbound import _twilio_client

    answer_url = f"{base_url}/telephony/outbound-answer"
    if lead_id is not None:
        answer_url = f"{answer_url}?lead_id={lead_id}"
    status_url = f"{base_url}/telephony/status"
    try:
        tw_call = _twilio_client().calls.create(
            to=to,
            from_=from_number,
            url=answer_url,
            status_callback=status_url,
            status_callback_event=[
                "initiated", "ringing", "answered", "completed", "no-answer", "busy", "failed"
            ],
            timeout=timeout_seconds,
            record=record,
        )
    except Exception as exc:
        _audit(
            "outbound.provider_dial_failed",
            error_category=type(exc).__name__,
            to=_redact_phone(to),
            tenant_id=str(tenant.id) if hasattr(tenant, "id") else "unknown",
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "telephony_provider_outcome_unknown", "message": "Twilio did not return a confirmed call identifier; do not retry with a new idempotency key until reconciled."},
        ) from None
    call_sid = str(getattr(tw_call, "sid", "") or "").strip()
    if not call_sid:
        raise HTTPException(
            status_code=502,
            detail={"code": "telephony_provider_outcome_unknown", "message": "Twilio returned no confirmed call identifier; do not retry with a new idempotency key until reconciled."},
        )
    return call_sid

# ---------------------------------------------------------------------------
# Endpoints — Outbound single call
# ---------------------------------------------------------------------------

@router.post("", response_model=OutboundCallOut, status_code=201)
async def create_outbound_call(
    payload: OutboundCallRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Create a durable, tenant/environment-scoped outbound call intent."""
    del request
    tenant = ctx.tenant
    if not tenant.outbound_enabled:
        raise HTTPException(status_code=403, detail="outbound calling disabled for tenant")
    await _check_rate_limit(ctx.tenant_id, "outbound_call", OUTBOUND_RATE_LIMIT_PER_MINUTE)

    scope = await resolve_scope(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        explicit_environment_id=ctx.environment_id,
        for_write=True,
    )
    access = await resolve_environment_membership(session, ctx.user, scope, tenant)
    if (
        not access.allowed
        or access.role is None
        or not has_permission(access.role, Permission.CALL_WRITE)
    ):
        raise HTTPException(status_code=403, detail="outbound calls are not allowed in this environment")

    to_normalized = _normalize_phone(payload.to)
    if not _validate_e164_strict(to_normalized):
        raise HTTPException(status_code=422, detail="invalid destination phone")
    await _check_dnc(
        session,
        ctx.tenant_id,
        to_normalized,
        payload.lead_id,
        environment_id=scope.id,
        agent_id=payload.agent_id or "",
    )
    await _check_dialer_policy(
        session,
        tenant,
        agent_id=payload.agent_id or "",
    )

    if payload.campaign_id is not None and payload.lead_id is None:
        raise HTTPException(status_code=422, detail="lead_id is required with campaign_id")
    if payload.lead_id is not None and payload.campaign_id is None:
        raise HTTPException(status_code=422, detail="campaign_id is required for campaign lead calls")

    campaign = None
    lead = None
    if payload.campaign_id is not None:
        from app.db.models import Campaign

        if not has_permission(access.role, Permission.CAMPAIGN_RUN):
            raise HTTPException(status_code=403, detail="campaign dialing is not allowed in this environment")
        campaign = await session.scalar(
            select(Campaign).where(
                Campaign.id == payload.campaign_id,
                Campaign.tenant_id == ctx.tenant_id,
                Campaign.environment_id == scope.id,
            )
        )
        lead = await session.scalar(
            select(Lead).where(
                Lead.id == payload.lead_id,
                Lead.tenant_id == ctx.tenant_id,
                Lead.environment_id == scope.id,
            )
        )
        if campaign is None or lead is None:
            raise HTTPException(status_code=404, detail="campaign or lead not found in this environment")
        if not campaign.is_active:
            raise HTTPException(status_code=409, detail="campaign is not active")

    idem_key = payload.idempotency_key or x_idempotency_key
    if payload.idempotency_key and x_idempotency_key and payload.idempotency_key != x_idempotency_key:
        raise HTTPException(status_code=400, detail="body and header Idempotency-Key values differ")
    if not idem_key or len(idem_key.strip()) < MIN_IDEMPOTENCY_KEY_LENGTH or len(idem_key.strip()) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="a printable Idempotency-Key of 8 to 128 characters is required")
    idem_key = idem_key.strip()

    provider = None
    caller_id = None
    if campaign is None:
        provider = _provider_from_request(payload.provider)
        _require_twilio_ready()
        caller_id = await _resolve_caller_id(tenant, payload.from_number)
        configured_callers = {
            _normalize_phone(str(value))
            for value in (
                getattr(tenant, "twilio_number", None),
                getattr(tenant, "outbound_caller_id", None),
            )
            if value
        }
        if caller_id not in configured_callers:
            raise HTTPException(
                status_code=403,
                detail="from_number must be a tenant-configured caller ID",
            )

    request_data = payload.model_dump(mode="json", exclude={"idempotency_key"})
    request_data.update({"environment_id": str(scope.id), "destination_normalized": to_normalized})
    try:
        claim = await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            environment_id=scope.id,
            operation="legacy.call.outbound",
            key=idem_key,
            request_data=request_data,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_conflict", "message": "Idempotency-Key was already used for a different request."},
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_in_progress", "message": "The call request is in progress or requires provider reconciliation."},
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_previous_failure", "message": "The prior request failed; use a new key for a new attempt."},
        ) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Idempotency-Key is invalid") from None

    if claim.replayed:
        try:
            existing_id = uuid.UUID(claim.receipt.resource_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=500, detail="stored idempotency result is not recoverable") from None
        existing = await session.scalar(
            select(Call).where(
                Call.id == existing_id,
                Call.tenant_id == ctx.tenant_id,
                Call.environment_id == scope.id,
            )
        )
        if existing is None:
            raise HTTPException(status_code=500, detail="stored idempotency result no longer exists")
        return _call_to_out(
            existing,
            agent_id=payload.agent_id,
            lead_id=str(payload.lead_id) if payload.lead_id else None,
            provider=provider.value if provider else "campaign",
        )

    if campaign is not None and lead is not None:
        # The durable receipt is committed before the campaign service is
        # allowed to perform its provider side effect.
        claim.receipt.resource_type = "campaign_dial_pending"
        claim.receipt.resource_id = f"{campaign.id}:{lead.id}"
        await session.commit()
        try:
            from app.telephony.outbound import place_call

            result = await place_call(session, tenant, campaign, lead, dry_run=False)
        except Exception as exc:
            # Do not expose provider response bodies or exception text. A
            # failure here may be ambiguous, so leave the receipt in progress.
            claim.receipt.error_category = type(exc).__name__[:64]
            await session.commit()
            raise HTTPException(
                status_code=502,
                detail={"code": "campaign_dial_outcome_unknown", "message": "Campaign dial outcome is unknown; the idempotency key is locked pending reconciliation."},
            ) from None
        if not result.get("ok"):
            await fail_request(session, claim.receipt, category="campaign_dial_rejected")
            await session.commit()
            raise HTTPException(
                status_code=422,
                detail={"code": "campaign_dial_rejected", "message": "Campaign service rejected the call before confirming a provider dial."},
            )
        call_sid = str(result.get("call_sid") or "")
        call_row = await session.scalar(
            select(Call).where(
                Call.tenant_id == ctx.tenant_id,
                Call.environment_id == scope.id,
                Call.call_sid == call_sid,
            )
        ) if call_sid else None
        if call_row is None:
            claim.receipt.error_category = "campaign_result_missing"
            await session.commit()
            raise HTTPException(
                status_code=502,
                detail={"code": "campaign_call_record_missing", "message": "Provider reported success but the durable call record is unavailable; the idempotency key is locked for reconciliation."},
            )
        await _record_call_audit(
            session,
            ctx,
            environment_id=scope.id,
            event_type="call_started",
            result="success",
            call_id=call_row.id,
            detail={
                "operation": "campaign_outbound",
                "campaign_id": str(campaign.id),
                "lead_id": str(lead.id),
            },
        )
        await complete_request(
            session,
            claim.receipt,
            resource_type="call",
            resource_id=call_row.id,
        )
        await session.commit()
        _audit(
            "outbound.created_via_campaign",
            tenant_id=str(ctx.tenant_id),
            call_id=str(call_row.id),
            lead_id=str(lead.id),
            campaign_id=str(campaign.id),
        )
        return _call_to_out(
            call_row,
            agent_id=payload.agent_id,
            lead_id=str(lead.id),
            provider=payload.provider or "campaign",
        )

    assert caller_id is not None and provider is not None
    pending_call_id = uuid.uuid4()
    claim.receipt.resource_type = "call"
    claim.receipt.resource_id = str(pending_call_id)
    await session.commit()

    try:
        real_sid = await _attempt_provider_dial(
            session,
            tenant,
            None,
            to_normalized,
            caller_id,
            payload.timeout_seconds,
            payload.record,
            payload.lead_id,
            provider.value,
        )
    except HTTPException as exc:
        if exc.status_code == 502:
            claim.receipt.error_category = "provider_outcome_unknown"
            await session.commit()
            raise
        await fail_request(session, claim.receipt, category=f"provider_rejected_{exc.status_code}")
        await session.commit()
        raise

    call = Call(
        id=pending_call_id,
        tenant_id=ctx.tenant_id,
        environment_id=scope.id,
        call_sid=real_sid,
        from_number=caller_id,
        to_number=to_normalized,
        status=CallStatus.RINGING,
        direction=CallDirection.OUTBOUND,
        lead_id=payload.lead_id,
    )
    session.add(call)
    await session.flush()
    from app.webhooks.call_event_bridge import publish_call_event
    await publish_call_event(session, call, "call_started")
    await _record_call_audit(
        session,
        ctx,
        environment_id=scope.id,
        event_type="call_started",
        result="success",
        call_id=call.id,
        detail={"operation": "direct_outbound", "provider": provider.value},
    )
    await complete_request(session, claim.receipt, resource_type="call", resource_id=call.id)
    await session.commit()
    await session.refresh(call)
    _audit(
        "outbound.created_direct",
        tenant_id=str(ctx.tenant_id),
        call_id=str(call.id),
        to=_redact_phone(to_normalized),
        provider=provider.value,
    )
    return _call_to_out(
        call,
        agent_id=payload.agent_id,
        lead_id=str(payload.lead_id) if payload.lead_id else None,
        provider=provider.value,
    )

@router.get("", response_model=OutboundCallListOut)
async def list_outbound_calls(
    status: Optional[str] = Query(default=None, description="Filter by status"),
    direction: Optional[str] = Query(default=None, description="Filter by direction"),
    agent_id: Optional[str] = Query(default=None, max_length=80),
    phone: Optional[str] = Query(default=None, description="Filter by phone fragment"),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls — List outbound calls with filters."""
    scope = await _scope_for_call_request(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    filters = [Call.tenant_id == ctx.tenant_id, Call.environment_id == scope.id]
    if status:
        try:
            # Validate status
            CallStatus(status)
            filters.append(Call.status == status)
        except ValueError:
            pass
    if direction:
        filters.append(Call.direction == direction)
    if phone:
        frag = f"%{phone}%"
        filters.append(or_(Call.to_number.ilike(frag), Call.from_number.ilike(frag)))

    total_q = await session.execute(select(func.count(Call.id)).where(*filters))
    total = total_q.scalar() or 0

    rows_q = await session.execute(
        select(Call).where(*filters).order_by(Call.started_at.desc()).offset(offset).limit(limit)
    )
    rows = rows_q.scalars().all()

    calls = [_call_to_out(r) for r in rows]
    return OutboundCallListOut(calls=calls, total=total, limit=limit, offset=offset)

@router.get("/outbound/{call_id}", response_model=OutboundCallDetailOut)
async def get_outbound_call(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/outbound/{id} — Outbound-call detail."""
    call = await _get_call(session, ctx, call_id)
    return _call_to_detail(call)

@router.post("/{call_id}/cancel")
async def cancel_outbound_call(
    call_id: uuid.UUID,
    payload: CallControlRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Cancel a live Twilio call only after the provider confirms the request."""
    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    claim = await _claim_route_idempotency(
        session,
        ctx=ctx,
        environment_id=call.environment_id,
        operation="legacy.call.cancel",
        key=payload.idempotency_key,
        request_data={"call_id": str(call.id), "reason": payload.reason or "operator_cancel"},
    )
    if claim.replayed:
        return {"id": str(call.id), "status": call.status.value, "action": "already_canceled"}

    claim.receipt.resource_type = "call"
    claim.receipt.resource_id = str(call.id)
    if call.status in (
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.NO_ANSWER,
        CallStatus.CANCELLED,
    ):
        await fail_request(
            session,
            claim.receipt,
            category=f"call_already_terminal_{call.status.value}",
        )
        await session.commit()
        raise HTTPException(status_code=409, detail="call already terminal")

    try:
        _require_twilio_ready()
    except HTTPException as exc:
        await fail_request(
            session,
            claim.receipt,
            category=f"provider_not_ready_{exc.status_code}",
        )
        await session.commit()
        raise

    # Persist the key before the non-transactional carrier operation. A crash
    # after the provider request leaves a durable in-progress receipt and cannot
    # accidentally repeat the cancellation.
    await session.commit()

    try:
        from app.telephony.outbound import _twilio_client

        provider_result = _twilio_client().calls(call.call_sid).update(status="canceled")
        provider_status = str(getattr(provider_result, "status", "") or "").lower()
        if provider_status != "canceled":
            raise RuntimeError("provider did not confirm cancellation")
    except Exception as exc:
        claim.receipt.error_category = type(exc).__name__[:64]
        await session.commit()
        _audit(
            "outbound.cancel_outcome_unknown",
            tenant_id=str(ctx.tenant_id),
            call_id=str(call.id),
            error_category=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "telephony_cancel_outcome_unknown", "message": "The provider did not confirm cancellation; the idempotency key is locked pending reconciliation."},
        ) from None

    call.status = CallStatus.CANCELLED
    call.ended_at = _now()
    call.failure_reason = "cancelled_by_operator"
    from app.webhooks.call_event_bridge import publish_call_event
    await publish_call_event(session, call, "call_ended")
    await _record_call_audit(
        session,
        ctx,
        environment_id=call.environment_id,
        event_type="call_ended",
        result="success",
        call_id=call.id,
        detail={"operation": "cancel", "reason_supplied": bool(payload.reason)},
    )
    await complete_request(
        session,
        claim.receipt,
        resource_type="call",
        resource_id=call.id,
    )
    await session.commit()
    _audit(
        "outbound.canceled",
        tenant_id=str(ctx.tenant_id),
        call_id=str(call.id),
        reason_supplied=bool(payload.reason),
    )
    return {"id": str(call.id), "status": call.status.value, "action": "canceled"}


@router.post("/{call_id}/retry", response_model=OutboundCallOut)
async def retry_outbound_call(
    call_id: uuid.UUID,
    payload: CallControlRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Retry an eligible terminal call with a separate durable idempotency key."""
    original = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    claim = await _claim_route_idempotency(
        session,
        ctx=ctx,
        environment_id=original.environment_id,
        operation="legacy.call.retry",
        key=payload.idempotency_key,
        request_data={"call_id": str(original.id), "reason": payload.reason or "operator_retry"},
    )
    if claim.replayed:
        try:
            retry_id = uuid.UUID(claim.receipt.resource_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=500, detail="stored retry result is not recoverable") from None
        retry_row = await session.scalar(
            select(Call).where(
                Call.id == retry_id,
                Call.tenant_id == ctx.tenant_id,
                Call.environment_id == original.environment_id,
            )
        )
        if retry_row is None:
            raise HTTPException(status_code=500, detail="stored retry result no longer exists")
        return _call_to_out(
            retry_row,
            lead_id=str(retry_row.lead_id) if retry_row.lead_id else None,
            provider="twilio",
        )

    retry_id = uuid.uuid4()
    try:
        if original.status not in (
            CallStatus.FAILED,
            CallStatus.COMPLETED,
            CallStatus.CANCELLED,
            CallStatus.NO_ANSWER,
        ):
            raise HTTPException(status_code=409, detail="only terminal calls can be retried")
        if not ctx.tenant.outbound_enabled:
            raise HTTPException(status_code=403, detail="outbound calling disabled for tenant")
        await _check_rate_limit(
            ctx.tenant_id,
            "outbound_call",
            OUTBOUND_RATE_LIMIT_PER_MINUTE,
        )
        to_normalized = _normalize_phone(original.to_number)
        if not _validate_e164_strict(to_normalized):
            raise HTTPException(status_code=422, detail="invalid destination phone")
        await _check_dnc(
            session,
            ctx.tenant_id,
            to_normalized,
            original.lead_id,
            environment_id=original.environment_id,
        )
        await _check_dialer_policy(session, ctx.tenant)
        _require_twilio_ready()
        caller_id = await _resolve_caller_id(ctx.tenant, original.from_number)
        configured_callers = {
            _normalize_phone(str(value))
            for value in (
                getattr(ctx.tenant, "twilio_number", None),
                getattr(ctx.tenant, "outbound_caller_id", None),
            )
            if value
        }
        if caller_id not in configured_callers:
            raise HTTPException(
                status_code=403,
                detail="original caller ID is no longer configured for this tenant",
            )
    except HTTPException as exc:
        if exc.status_code == 429:
            # Rate limits are transient; don't burn the caller's durable key.
            await session.rollback()
        else:
            await fail_request(
                session,
                claim.receipt,
                category=f"retry_rejected_{exc.status_code}",
            )
            await session.commit()
        raise

    claim.receipt.resource_type = "call"
    claim.receipt.resource_id = str(retry_id)
    await session.commit()
    try:
        provider_sid = await _attempt_provider_dial(
            session,
            ctx.tenant,
            None,
            to_normalized,
            caller_id,
            record=True,
            lead_id=original.lead_id,
            provider_override="twilio",
        )
    except HTTPException as exc:
        if exc.status_code == 502:
            claim.receipt.error_category = "provider_outcome_unknown"
            await session.commit()
            raise
        await fail_request(session, claim.receipt, category=f"provider_rejected_{exc.status_code}")
        await session.commit()
        raise

    retry_row = Call(
        id=retry_id,
        tenant_id=ctx.tenant_id,
        environment_id=original.environment_id,
        call_sid=provider_sid,
        from_number=caller_id,
        to_number=to_normalized,
        status=CallStatus.RINGING,
        direction=CallDirection.OUTBOUND,
        lead_id=original.lead_id,
    )
    session.add(retry_row)
    await session.flush()
    from app.webhooks.call_event_bridge import publish_call_event
    await publish_call_event(session, retry_row, "call_started")
    await _record_call_audit(
        session,
        ctx,
        environment_id=original.environment_id,
        event_type="call_started",
        result="success",
        call_id=retry_row.id,
        detail={"operation": "retry", "source_call_id": str(original.id)},
    )
    await complete_request(session, claim.receipt, resource_type="call", resource_id=retry_row.id)
    await session.commit()
    await session.refresh(retry_row)
    _audit(
        "outbound.retried",
        tenant_id=str(ctx.tenant_id),
        old_call_id=str(original.id),
        new_call_id=str(retry_row.id),
    )
    return _call_to_out(
        retry_row,
        lead_id=str(retry_row.lead_id) if retry_row.lead_id else None,
        provider="twilio",
    )

@router.post("/bulk", response_model=BulkOutboundOut, status_code=201)
async def bulk_outbound_calls(
    payload: BulkOutboundRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Run a bounded batch using one stable durable key per item."""
    if len(payload.calls) > MAX_BULK_SIZE:
        raise HTTPException(status_code=422, detail=f"bulk size exceeds {MAX_BULK_SIZE}")
    if payload.idempotency_key and x_idempotency_key and payload.idempotency_key != x_idempotency_key:
        raise HTTPException(status_code=400, detail="body and header Idempotency-Key values differ")
    batch_key = (payload.idempotency_key or x_idempotency_key or "").strip()
    if len(batch_key) < MIN_IDEMPOTENCY_KEY_LENGTH or len(batch_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="a printable batch Idempotency-Key of 8 to 128 characters is required")

    tenant_id = ctx.tenant_id
    batch_fingerprint = hashlib.sha256(
        f"{tenant_id}:{batch_key}".encode("utf-8")
    ).hexdigest()[:12]
    batch_id = f"bulk_{batch_fingerprint}"
    results: list[dict[str, Any]] = []
    accepted = 0
    rejected = 0
    for index, item in enumerate(payload.calls):
        child_key = item.idempotency_key or hashlib.sha256(
            f"{tenant_id}:{batch_key}:{index}".encode("utf-8")
        ).hexdigest()
        scoped_item = item.model_copy(update={"idempotency_key": child_key})
        try:
            result = await create_outbound_call(
                scoped_item,
                None,
                ctx,
                session,
                child_key,
            )
            results.append(
                {
                    "index": index,
                    "ok": True,
                    "call_id": result.id,
                    "call_sid": result.call_sid,
                    "status": result.status,
                }
            )
            accepted += 1
        except HTTPException as exc:
            await session.rollback()
            # SQLAlchemy expires ORM instances after rollback. The request's
            # authenticated context is attached to this same session, so reload
            # its trusted principal rows before the next item is processed.
            await session.refresh(ctx.tenant)
            await session.refresh(ctx.user)
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "status_code": exc.status_code,
                    "error_code": detail.get("code", "call_rejected"),
                }
            )
            rejected += 1
        except Exception as exc:
            await session.rollback()
            # Rollback expires authenticated ORM state; restore it explicitly
            # before processing the next independent item.
            await session.refresh(ctx.tenant)
            await session.refresh(ctx.user)
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "status_code": 500,
                    "error_code": type(exc).__name__[:64],
                }
            )
            rejected += 1

    _audit(
        "outbound.bulk",
        tenant_id=str(ctx.tenant_id),
        batch_id=batch_id,
        total=len(payload.calls),
        accepted=accepted,
        rejected=rejected,
    )
    return BulkOutboundOut(
        batch_id=batch_id,
        total=len(payload.calls),
        accepted=accepted,
        rejected=rejected,
        results=results,
    )

# ---------------------------------------------------------------------------
# Web Call API — delegates to app.telephony.web_call (PART 3 / F-06 closure)
# ---------------------------------------------------------------------------

@router.post("/web-calls", status_code=201)
async def create_web_call(
    payload: WebCallRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Create a browser web call by delegating to `app.telephony.web_call.create_web_call`."""
    del x_idempotency_key
    from app.telephony import web_call as web_call_service

    scope = await _scope_for_call_request(
        session, ctx, permission=Permission.CALL_WRITE, for_write=True
    )
    dynamic_vars = dict(payload.custom_fields or {})
    if payload.customer_name:
        dynamic_vars.setdefault("customer_name", payload.customer_name)
    if payload.customer_email:
        dynamic_vars.setdefault("customer_email", payload.customer_email)

    return await web_call_service.create_web_call(
        session,
        tenant=ctx.tenant,
        agent_id=payload.agent_id,
        version=None,
        dynamic_vars=dynamic_vars,
        metadata=payload.metadata,
        environment_id=scope.id,
        ttl_seconds=int(payload.ttl_minutes) * 60,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
    )


@router.post("/web-calls/{call_id}/revoke")
async def revoke_web_call(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """End/revoke an active web call via `app.telephony.web_call.end_web_call`."""
    from app.telephony import web_call as web_call_service

    return await web_call_service.end_web_call(
        session,
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        reason="revoked_by_operator",
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
    )

# ---------------------------------------------------------------------------
# Call Control API — pause/resume/end/reopen + analytics
# ---------------------------------------------------------------------------

@router.post("/{call_id}/pause", status_code=501)
async def pause_call(
    call_id: uuid.UUID,
    payload: CallControlRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Pause requires a provider capability not implemented by this legacy route."""
    del call_id, payload, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "call_pause_not_implemented", "message": "The configured legacy call adapter does not support a verified pause operation."},
    )

@router.post("/{call_id}/resume", status_code=501)
async def resume_call(
    call_id: uuid.UUID,
    payload: CallControlRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Resume requires a provider capability not implemented by this legacy route."""
    del call_id, payload, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "call_resume_not_implemented", "message": "The configured legacy call adapter does not support a verified resume operation."},
    )

@router.post("/{call_id}/end")
async def end_call(
    call_id: uuid.UUID,
    payload: CallControlRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """End a provider call only after the provider confirms a terminal status."""
    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    if call.status in (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELLED, CallStatus.NO_ANSWER):
        return {"id": str(call.id), "status": call.status.value, "action": "already_ended"}
    _require_twilio_ready()
    claim = await _claim_route_idempotency(
        session,
        ctx=ctx,
        environment_id=call.environment_id,
        operation="legacy.call.end",
        key=payload.idempotency_key,
        request_data={"call_id": str(call.id), "reason": payload.reason or "operator_end"},
    )
    if claim.replayed:
        return {"id": str(call.id), "status": call.status.value, "action": "already_ended"}
    claim.receipt.resource_type = "call"
    claim.receipt.resource_id = str(call.id)
    await session.commit()

    provider_action = "canceled" if call.status == CallStatus.RINGING else "completed"
    try:
        from app.telephony.outbound import _twilio_client

        provider_result = _twilio_client().calls(call.call_sid).update(status=provider_action)
        provider_status = str(getattr(provider_result, "status", "") or "").lower()
        if provider_status == "canceled":
            call.status = CallStatus.CANCELLED
        elif provider_status == "completed":
            call.status = CallStatus.COMPLETED
        elif provider_status in {"failed", "busy"}:
            call.status = CallStatus.FAILED
        elif provider_status in {"no-answer", "no_answer"}:
            call.status = CallStatus.NO_ANSWER
        else:
            raise RuntimeError("provider did not confirm a terminal call state")
    except Exception as exc:
        claim.receipt.error_category = type(exc).__name__[:64]
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "telephony_end_outcome_unknown", "message": "The provider did not confirm call termination; the idempotency key is locked pending reconciliation."},
        ) from None

    call.ended_at = _now()
    if call.status == CallStatus.FAILED:
        call.failure_reason = "provider_reported_failure"
    elif call.status == CallStatus.CANCELLED:
        call.failure_reason = "cancelled_by_operator"
    from app.webhooks.call_event_bridge import publish_call_event
    await publish_call_event(session, call, "call_ended")
    await complete_request(session, claim.receipt, resource_type="call", resource_id=call.id)
    await session.commit()
    _audit(
        "call.ended",
        tenant_id=str(ctx.tenant_id),
        call_id=str(call.id),
        status=call.status.value,
        reason=payload.reason,
    )
    return {
        "id": str(call.id),
        "status": call.status.value,
        "action": CallAction.END.value,
        "ended_at": call.ended_at.isoformat(),
    }

@router.post("/{call_id}/reopen", status_code=501)
async def reopen_call(
    call_id: uuid.UUID,
    payload: CallControlRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """A completed provider call cannot be reopened as a live carrier call."""
    del call_id, payload, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "call_reopen_not_implemented", "message": "A completed provider call cannot be reopened; start a new call with a fresh idempotency key."},
    )

@router.get("/{call_id}/analytics", response_model=CallAnalyticsOut)
async def get_call_analytics(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/analytics — Call analytics stub."""
    call = await _get_call(session, ctx, call_id)
    duration = None
    if call.started_at and call.ended_at:
        try:
            duration = int((call.ended_at - call.started_at).total_seconds())
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            duration = None
    return CallAnalyticsOut(
        call_id=str(call.id),
        duration_seconds=duration,
        status=call.status.value if hasattr(call.status, "value") else str(call.status),
        provider=None,
        cost_cents=None,
        recording_url=None,
        transcript_excerpt=None,
    )

# ---------------------------------------------------------------------------
# Live DTMF API — active call digit control
# ---------------------------------------------------------------------------

@router.post("/{call_id}/dtmf")
async def send_dtmf(
    call_id: uuid.UUID,
    payload: DtmfRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Send DTMF through the configured Twilio adapter with durable replay control."""
    await _check_rate_limit(ctx.tenant_id, f"dtmf:{call_id}", DTMF_RATE_LIMIT_PER_MINUTE)
    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    if call.status in (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELLED):
        raise HTTPException(status_code=409, detail="call not active")
    if not DTMF_REGEX.fullmatch(payload.digits):
        raise HTTPException(status_code=422, detail="invalid DTMF digits — allowed 0-9 # * w W")

    from app.core.config import settings
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise HTTPException(
            status_code=503,
            detail={"code": "telephony_provider_not_configured", "message": "Twilio live calling is not configured."},
        )
    claim = await _claim_route_idempotency(
        session,
        ctx=ctx,
        environment_id=call.environment_id,
        operation="legacy.call.dtmf",
        key=payload.idempotency_key,
        request_data={
            "call_id": str(call.id),
            "digits": payload.digits,
            "duration_ms": payload.duration_ms,
            "gap_ms": payload.gap_ms,
        },
    )
    if claim.replayed:
        return {
            "id": str(call.id),
            "status": "already_accepted",
            "digit_count": len(payload.digits),
            "at": _now_iso(),
        }
    claim.receipt.resource_type = "call_dtmf"
    claim.receipt.resource_id = str(call.id)
    await session.commit()

    twiml = (
        f'<Response><Play digits="{payload.digits}" '
        f'/> </Response>'
    )
    try:
        from app.telephony.outbound import _twilio_client

        provider_result = _twilio_client().calls(call.call_sid).update(twiml=twiml)
        if not getattr(provider_result, "sid", None):
            raise RuntimeError("provider did not return a call resource")
    except Exception as exc:
        claim.receipt.error_category = type(exc).__name__[:64]
        await session.commit()
        _audit(
            "dtmf.outcome_unknown",
            tenant_id=str(ctx.tenant_id),
            call_id=str(call.id),
            digit_count=len(payload.digits),
            error_category=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "dtmf_outcome_unknown", "message": "The provider did not confirm the DTMF request; the idempotency key is locked pending reconciliation."},
        ) from None

    await complete_request(
        session,
        claim.receipt,
        resource_type="call_dtmf",
        resource_id=call.id,
    )
    await session.commit()
    _audit(
        "dtmf.accepted_by_provider",
        tenant_id=str(ctx.tenant_id),
        call_id=str(call.id),
        digit_count=len(payload.digits),
    )
    return {
        "id": str(call.id),
        "status": "accepted_by_provider",
        "digit_count": len(payload.digits),
        "at": _now_iso(),
    }

@router.post("/{call_id}/dtmf/batch", status_code=501)
async def send_dtmf_batch(
    call_id: uuid.UUID,
    digits_list: List[str],
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Batch DTMF is disabled until every item has a durable receipt."""
    del call_id, digits_list, ctx
    raise HTTPException(
        status_code=501,
        detail={
            "code": "dtmf_batch_not_implemented",
            "message": "Use the single DTMF endpoint with a unique Idempotency-Key for each provider side effect.",
        },
    )

# ---------------------------------------------------------------------------
# Additional compliance & health endpoints
# ---------------------------------------------------------------------------

@router.get("/{call_id}/compliance")
async def get_call_compliance(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Report stored DNC/consent evidence and the current tenant call window."""
    call = await _get_call(session, ctx, call_id)
    from app.leads.repository import latest_consent
    from app.telephony.outbound import is_call_window_open

    dnc_verdict = await dnc.evaluate_dnc(
        session,
        ctx.tenant_id,
        call.to_number,
        lead_id=call.lead_id,
        environment_id=call.environment_id,
    )
    dnc_blocked = dnc_verdict.blocked
    consent_status = "unverified"
    if call.lead_id is not None:
        lead = await session.scalar(
            select(Lead).where(
                Lead.id == call.lead_id,
                Lead.tenant_id == ctx.tenant_id,
                Lead.environment_id == call.environment_id,
            )
        )
        if lead is not None:
            consent = await latest_consent(
                session,
                ctx.tenant_id,
                lead.id,
                "voice",
                environment_id=call.environment_id,
            )
            consent_status = consent.decision if consent is not None else "unknown"
        else:
            consent_status = "lead_missing"

    win_verdict = await dialer_limits.check_calling_window(session, ctx.tenant_id)
    window_open = is_call_window_open(ctx.tenant) and win_verdict.allowed
    compliant = bool(not dnc_blocked and consent_status == "granted" and window_open)
    return {
        "call_id": str(call.id),
        "to": _redact_phone(call.to_number),
        "dnc_blocked": dnc_blocked,
        "consent_status": consent_status,
        "calling_window_open_now": window_open,
        "compliant": compliant,
        "checked_at": _now_iso(),
    }

@router.get("/health/provider")
async def provider_health(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
):
    """Report provider credential configuration, not inferred carrier health."""
    from app.core.config import settings

    configured = bool(settings.twilio_account_sid and settings.twilio_auth_token)
    return {
        "provider": "twilio" if configured else None,
        "configured": configured,
        "status": "configured" if configured else "not_configured",
        "live_probe_performed": False,
        "checked_at": _now_iso(),
        "tenant_id": str(ctx.tenant_id),
    }

@router.get("/stats/summary")
async def outbound_stats_summary(
    days: int = Query(default=7, ge=1, le=90),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Return outbound-call statistics within the selected environment only."""
    scope = await _scope_for_call_request(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    since = _now() - timedelta(days=days)
    base = (
        Call.tenant_id == ctx.tenant_id,
        Call.environment_id == scope.id,
        Call.direction == CallDirection.OUTBOUND,
        Call.started_at >= since,
    )
    total = int(
        (await session.execute(select(func.count(Call.id)).where(*base))).scalar_one() or 0
    )
    completed = int(
        (await session.execute(
            select(func.count(Call.id)).where(*base, Call.status == CallStatus.COMPLETED)
        )).scalar_one() or 0
    )
    failed = int(
        (await session.execute(
            select(func.count(Call.id)).where(*base, Call.status == CallStatus.FAILED)
        )).scalar_one() or 0
    )
    cancelled = int(
        (await session.execute(
            select(func.count(Call.id)).where(*base, Call.status == CallStatus.CANCELLED)
        )).scalar_one() or 0
    )
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "days": days,
        "since": since.isoformat(),
        "total_outbound": total,
        "completed": completed,
        "failed": failed,
        "cancelled": cancelled,
        "success_rate": (completed / total * 100) if total > 0 else 0,
        "generated_at": _now_iso(),
    }


@router.get("/idempotency/stats")
async def idempotency_stats(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Return durable idempotency counts for the caller's current environment."""
    scope = await _scope_for_call_request(
        session, ctx, permission=Permission.TENANT_READ, for_write=False
    )
    rows = (
        await session.execute(
            select(RequestIdempotencyReceipt.status, func.count(RequestIdempotencyReceipt.id))
            .where(
                RequestIdempotencyReceipt.tenant_id == ctx.tenant_id,
                RequestIdempotencyReceipt.environment_scope == str(scope.id),
                RequestIdempotencyReceipt.operation.like("legacy.call.%"),
            )
            .group_by(RequestIdempotencyReceipt.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "total_entries": sum(counts.values()),
        "in_progress": counts.get("in_progress", 0),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "checked_at": _now_iso(),
    }


@router.delete("/idempotency/cache", status_code=410)
async def clear_idempotency_cache(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
):
    """Durable receipts cannot be cleared because that would permit duplicate calls."""
    del ctx
    raise HTTPException(
        status_code=410,
        detail={
            "code": "durable_idempotency_not_clearable",
            "message": "Durable call idempotency receipts are retained to prevent replayed provider side effects.",
        },
    )
