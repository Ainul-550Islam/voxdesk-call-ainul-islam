"""Environment-scoped transfer control and warm-transfer context APIs.

Provider-backed initiation uses the canonical telephony transfer service and a
durable, tenant/environment-scoped idempotency receipt. Provider callbacks—not
client-authored completion/failure requests—remain authoritative for transfer
outcomes. Operations without a verified provider implementation fail closed
with HTTP 501 rather than manufacturing a success state.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import (
    TRANSFER_IN_FLIGHT,
    Call,
    CallStatus,
    Lead,
    RequestIdempotencyReceipt,
    TransferState,
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
from app.telephony import phone as phone_util
from app.core.logging import log
from app.core.rate_limit import allow_identity_action
from app.audit.redaction import configured_secret_values, redact_text
from app.audit.service import record_event

router = APIRouter(prefix="/api/calls", tags=["transfer-control"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_DESTINATION_LENGTH = 64
MAX_REASON_LENGTH = 500
MAX_SUMMARY_LENGTH = 2000
MAX_WHISPER_LENGTH = 500
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MIN_IDEMPOTENCY_KEY_LENGTH = 8
TRANSFER_TIMEOUT_SECONDS = 30
DEFAULT_TRANSFER_REASON = "operator_requested"
ALLOWED_TRANSFER_REASONS = {
    "operator_requested", "customer_requested", "escalation", "supervisor_requested",
    "ivr_selection", "no_answer", "voicemail", "technical", "compliance"
}
E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class TransferRequest(_Strict):
    destination: str = Field(min_length=8, max_length=MAX_DESTINATION_LENGTH, description="Validated E.164 destination for the provider-backed human leg")
    reason: Optional[str] = Field(default=None, max_length=MAX_REASON_LENGTH)
    summary: Optional[str] = Field(default=None, max_length=MAX_SUMMARY_LENGTH, description="Warm-transfer summary for human")
    crm_context: Dict[str, Any] = Field(default_factory=dict, description="CRM context to propagate")
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    whisper: Optional[str] = Field(default=None, max_length=MAX_WHISPER_LENGTH, description="Whisper message for human before connect")
    answer_on_bridge: bool = Field(default=True)
    timeout_seconds: int = Field(default=TRANSFER_TIMEOUT_SECONDS, ge=5, le=120)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=100)

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, v: str) -> str:
        normalized = re.sub(r"[\s\-\(\)]", "", v or "")
        if not E164_REGEX.fullmatch(normalized) or not phone_util.is_valid(normalized):
            raise ValueError("destination must be a valid E.164 phone number")
        return normalized

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ALLOWED_TRANSFER_REASONS and len(v) > 3:
            # Allow custom reasons but warn
            return v
        return v

class TransferOut(_Strict):
    id: str
    call_id: str
    destination: str
    state: str
    reason: Optional[str] = None
    summary: Optional[str] = None
    whisper: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    transfer_sid: Optional[str] = None
    duration_seconds: Optional[int] = None
    priority: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

class WarmTransferContextOut(_Strict):
    call_id: str
    summary: Optional[str] = None
    crm_context: Dict[str, Any]
    transfer_reason: Optional[str] = None
    customer_phone: str
    customer_name: Optional[str] = None
    agent_id: Optional[str] = None
    transcript_excerpt: Optional[str] = None
    call_duration_seconds: Optional[int] = None
    intent: Optional[str] = None
    sentiment: Optional[str] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    generated_at: str

class TransferHistoryOut(_Strict):
    transfers: List[TransferOut]
    total: int
    limit: int
    offset: int

class TransferCancelRequest(_Strict):
    reason: Optional[str] = Field(default=None, max_length=500)

class TransferAnalyticsOut(_Strict):
    call_id: str
    total_transfers: int
    successful_transfers: int
    failed_transfers: int
    average_duration_seconds: Optional[float] = None
    last_transfer_at: Optional[str] = None
    transfer_states: Dict[str, int] = Field(default_factory=dict)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now().isoformat()

async def _check_rate(tenant_id: uuid.UUID, action: str, limit: int) -> None:
    allowed = await allow_identity_action(
        action=f"transfer:{action}",
        who=str(tenant_id),
        limit=limit,
        window=60.0,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Too many transfer operations."},
            headers={"Retry-After": "60"},
        )


async def _scope_for_transfer(
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
    if not access.allowed or access.role is None or not has_permission(access.role, permission):
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
    scope = await _scope_for_transfer(
        session, ctx, permission=permission, for_write=for_write
    )
    stmt = select(Call).where(
        Call.id == call_id,
        Call.tenant_id == ctx.tenant_id,
        Call.environment_id == scope.id,
    )
    if for_write:
        stmt = stmt.with_for_update()
    row = await session.scalar(stmt)
    if row is None:
        raise HTTPException(status_code=404, detail="call not found")
    return row


async def _claim_transfer_idempotency(
    session: AsyncSession,
    *,
    ctx: TenantContext,
    environment_id: uuid.UUID,
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
            operation="legacy.call.transfer",
            key=key.strip(),
            request_data=request_data,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_conflict", "message": "Idempotency-Key was already used for a different transfer request."},
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_in_progress", "message": "The transfer request is in progress or requires reconciliation."},
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_previous_failure", "message": "The prior transfer failed; use a new key to retry."},
        ) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Idempotency-Key is invalid") from None

def _audit(event: str, **kwargs: Any) -> None:
    log.info(event, **kwargs)


async def _record_transfer_audit(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    environment_id: uuid.UUID,
    event_type: str,
    result: str,
    call_id: uuid.UUID,
    detail: Dict[str, Any],
) -> None:
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


def _redact_destination(dest: str) -> str:
    if not dest:
        return "***"
    if dest.startswith("sip:"):
        # Redact user part
        try:
            user_host = dest[4:]
            if "@" in user_host:
                user, host = user_host.split("@", 1)
                return f"sip:{user[:2]}***@{host}"
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            pass
        return dest[:8] + "***"
    if dest.startswith("+"):
        return dest[:3] + "***" + dest[-2:]
    if dest.startswith("queue:"):
        return "queue:***"
    if dest.startswith("ext:"):
        return "ext:***"
    if dest.startswith("agent:"):
        return "agent:***"
    return dest[:2] + "***" + dest[-2:] if len(dest) > 4 else "***"

def _normalize_destination(dest: str) -> str:
    if dest.startswith("sip:") or dest.startswith("queue:") or dest.startswith("ext:"):
        return dest
    return re.sub(r"[\s\-\(\)]", "", dest)

def _call_duration(call: Call) -> Optional[int]:
    if call.started_at and call.ended_at:
        try:
            return int((call.ended_at - call.started_at).total_seconds())
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return None
    if call.started_at:
        try:
            return int((_now() - call.started_at).total_seconds())
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return None
    return None


def _transfer_duration(call: Call) -> Optional[int]:
    started = call.transfer_started_at or call.transfer_requested_at
    ended = call.transfer_completed_at or call.transfer_failed_at
    if started is None:
        return None
    if ended is None:
        ended = _now()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    return max(0, int((ended - started).total_seconds()))


async def _build_crm_context(session: AsyncSession, tenant_id: uuid.UUID, call: Call) -> Dict[str, Any]:
    crm_context: Dict[str, Any] = {}
    if call.lead_id:
        lead = await session.scalar(
            select(Lead).where(
                Lead.id == call.lead_id,
                Lead.tenant_id == tenant_id,
                Lead.environment_id == call.environment_id,
            )
        )
        if lead is not None:
            crm_context = {
                "lead_id": str(lead.id),
                "lead_name": lead.name,
                "lead_phone": lead.phone,
                "lead_email": lead.email,
                "lead_company": lead.company,
                "lead_score": lead.score,
                "lead_status": lead.status.value if hasattr(lead.status, "value") else str(lead.status),
                "custom_fields": lead.custom_fields,
            }

    crm_context["call_id"] = str(call.id)
    crm_context["call_sid"] = call.call_sid
    crm_context["from_number"] = call.from_number
    crm_context["to_number"] = call.to_number
    crm_context["direction"] = call.direction.value if hasattr(call.direction, "value") else str(call.direction)
    crm_context["status"] = call.status.value if hasattr(call.status, "value") else str(call.status)
    stored_context = call.transfer_context or {}
    operator_context = stored_context.get("crm_context", {}) if isinstance(stored_context, dict) else {}
    if isinstance(operator_context, dict) and operator_context:
        crm_context["operator_context"] = operator_context
    return _validate_context_data(crm_context)

async def _build_transcript_excerpt(session: AsyncSession, call_id: uuid.UUID, max_turns: int = 6) -> Optional[str]:
    from app.db.models import Turn

    turns = (
        await session.execute(
            select(Turn)
            .where(Turn.call_id == call_id)
            .order_by(Turn.created_at.desc())
            .limit(max_turns)
        )
    ).scalars().all()
    if not turns:
        return None
    ordered = list(reversed(turns))
    excerpt = "\n".join([f"{turn.speaker}: {turn.text[:300]}" for turn in ordered])
    return _redact_credential_material(excerpt)

_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|secret|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization)\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_CREDENTIAL_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}")
_KNOWN_SECRET_TOKEN_RE = re.compile(
    r"(?i)\b(?:sk|pk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b|\bAKIA[0-9A-Z]{16}\b"
)


def _reject_credential_material(text: str) -> None:
    configured_secrets = configured_secret_values()
    if any(secret in text for secret in configured_secrets) or any(
        pattern.search(text)
        for pattern in (
            _CREDENTIAL_ASSIGNMENT_RE,
            _BEARER_CREDENTIAL_RE,
            _KNOWN_SECRET_TOKEN_RE,
        )
    ):
        raise HTTPException(status_code=422, detail="Transfer context must not contain credential material")


def _redact_credential_material(text: str) -> str:
    cleaned = _CREDENTIAL_ASSIGNMENT_RE.sub("[CREDENTIAL REDACTED]", text)
    cleaned = _BEARER_CREDENTIAL_RE.sub("Bearer [CREDENTIAL REDACTED]", cleaned)
    cleaned = _KNOWN_SECRET_TOKEN_RE.sub("[CREDENTIAL REDACTED]", cleaned)
    cleaned = redact_text(
        cleaned,
        known_secrets=configured_secret_values(),
        redact_pii=False,
    )
    return cleaned.replace("[REDACTED]", "[CREDENTIAL REDACTED]")


def _validate_context_data(context: Dict[str, Any]) -> Dict[str, Any]:
    forbidden_fragments = ("password", "secret", "token", "authorization", "api_key", "apikey")

    def inspect(value: Any, depth: int = 0) -> None:
        if depth > 8:
            raise HTTPException(status_code=422, detail="CRM context nesting is too deep")
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(fragment in normalized for fragment in forbidden_fragments):
                    raise HTTPException(status_code=422, detail="CRM context must not contain credentials or secrets")
                inspect(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 100:
                raise HTTPException(status_code=422, detail="CRM context list exceeds the supported size")
            for child in value:
                inspect(child, depth + 1)

    inspect(context)
    try:
        encoded = json.dumps(context, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="CRM context must be JSON serializable") from None
    if len(encoded.encode("utf-8")) > 16_000:
        raise HTTPException(status_code=413, detail="CRM context exceeds 16 KB")
    _reject_credential_material(encoded)
    return context


def _generate_summary_from_context(crm_context: Dict[str, Any], transcript: Optional[str], reason: Optional[str]) -> Optional[str]:
    # Generate warm-transfer summary from available context
    parts = []
    if reason:
        parts.append(f"Transfer reason: {reason}")
    if crm_context.get("lead_name"):
        parts.append(f"Customer: {crm_context['lead_name']}")
    if crm_context.get("lead_company"):
        parts.append(f"Company: {crm_context['lead_company']}")
    if crm_context.get("lead_score") is not None:
        parts.append(f"Lead score: {crm_context['lead_score']}")
    if transcript:
        # Extract last customer intent from transcript
        lines = transcript.split("\n")[-3:]
        if lines:
            parts.append("Recent conversation:")
            parts.extend(lines[-2:])
    if not parts:
        return None
    return "\n".join(parts)[:2000]

# ---------------------------------------------------------------------------
# Endpoints — Transfer initiation
# ---------------------------------------------------------------------------

@router.post("/{call_id}/transfer", response_model=TransferOut, status_code=201)
async def initiate_transfer(
    call_id: uuid.UUID,
    payload: TransferRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Initiate a provider-backed transfer with durable idempotency."""
    await _check_rate(ctx.tenant_id, "initiate", 20)
    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    crm_context = _validate_context_data(payload.crm_context)
    metadata = _validate_context_data(payload.metadata)
    for text_value in (payload.reason, payload.summary, payload.whisper):
        if text_value:
            _reject_credential_material(text_value)
    from app.telephony.transfer_service import _safe_whisper_reason
    safe_whisper = _safe_whisper_reason(payload.whisper or payload.summary or "")

    destination = _normalize_destination(payload.destination)
    if not E164_REGEX.fullmatch(destination) or not phone_util.is_valid(destination):
        raise HTTPException(
            status_code=422,
            detail={"code": "transfer_destination_not_supported", "message": "This route supports only valid E.164 destinations."},
        )
    if payload.idempotency_key and x_idempotency_key and payload.idempotency_key != x_idempotency_key:
        raise HTTPException(status_code=400, detail="body and header Idempotency-Key values differ")
    idem_key = (payload.idempotency_key or x_idempotency_key or "").strip()
    claim = await _claim_transfer_idempotency(
        session,
        ctx=ctx,
        environment_id=call.environment_id,
        key=idem_key,
        request_data={
            "call_id": str(call.id),
            "destination": destination,
            "reason": payload.reason or DEFAULT_TRANSFER_REASON,
            "summary": payload.summary,
            "whisper": payload.whisper,
            "crm_context": crm_context,
            "answer_on_bridge": payload.answer_on_bridge,
            "timeout_seconds": payload.timeout_seconds,
            "metadata": metadata,
            "priority": payload.priority,
        },
    )
    if claim.replayed:
        return TransferOut(
            id=str(call.id),
            call_id=str(call.id),
            destination=_redact_destination(call.transfer_destination or destination),
            state=call.transfer_state.value if hasattr(call.transfer_state, "value") else str(call.transfer_state),
            reason=_redact_credential_material(call.transfer_reason or "") or None,
            summary=_redact_credential_material(payload.summary or "") or None,
            whisper=safe_whisper or None,
            created_at=call.transfer_requested_at.isoformat() if call.transfer_requested_at else _now_iso(),
            updated_at=call.updated_at.isoformat() if call.updated_at else None,
            duration_seconds=_transfer_duration(call),
            priority=payload.priority,
            metadata=metadata,
        )

    if call.status in {
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.NO_ANSWER,
        CallStatus.CANCELLED,
    }:
        await fail_request(session, claim.receipt, category="call_already_terminal")
        await _record_transfer_audit(
            session,
            ctx,
            environment_id=call.environment_id,
            event_type="call_transfer_rejected",
            result="denied",
            call_id=call.id,
            detail={"reason": "call_already_terminal"},
        )
        await session.commit()
        raise HTTPException(status_code=409, detail="call already terminal, cannot transfer")

    if call.transfer_state in TRANSFER_IN_FLIGHT:
        await fail_request(session, claim.receipt, category="transfer_already_in_flight")
        await _record_transfer_audit(
            session,
            ctx,
            environment_id=call.environment_id,
            event_type="call_transfer_rejected",
            result="denied",
            call_id=call.id,
            detail={"reason": "transfer_already_in_flight"},
        )
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail={"code": "transfer_already_in_flight", "message": "Another transfer is already in progress for this call."},
        )

    call.transfer_context = dict(call.transfer_context or {})
    if crm_context:
        call.transfer_context["crm_context"] = crm_context
    if metadata:
        call.transfer_context["request_metadata"] = metadata
    call.transfer_context["priority"] = payload.priority
    if payload.summary is not None:
        call.summary = payload.summary[:MAX_SUMMARY_LENGTH]
    call.transfer_context["updated_at"] = _now_iso()
    claim.receipt.resource_type = "call_transfer"
    claim.receipt.resource_id = str(call.id)
    try:
        from app.telephony.transfer_service import request_transfer

        result = await request_transfer(
            session,
            tenant=ctx.tenant,
            call=call,
            destination_override=destination,
            reason=payload.reason or DEFAULT_TRANSFER_REASON,
            whisper=safe_whisper or None,
            timeout_seconds=payload.timeout_seconds,
            answer_on_bridge=payload.answer_on_bridge,
        )
    except Exception as exc:
        # request_transfer commits REQUESTED before touching the provider. An
        # exception after that point is ambiguous; keep this key in progress
        # and never fabricate a transfer result or automatically redial.
        claim.receipt.error_category = type(exc).__name__[:64]
        await _record_transfer_audit(
            session,
            ctx,
            environment_id=call.environment_id,
            event_type="call_transfer_outcome_unknown",
            result="pending",
            call_id=call.id,
            detail={"error_category": type(exc).__name__[:64]},
        )
        await session.commit()
        _audit(
            "transfer.outcome_unknown",
            tenant_id=str(ctx.tenant_id),
            call_id=str(call.id),
            error_category=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "transfer_outcome_unknown", "message": "The provider did not confirm the transfer outcome; the idempotency key is locked pending reconciliation."},
        ) from None

    if not result.ok:
        error_category = result.error.value if result.error else "transfer_rejected"
        await fail_request(session, claim.receipt, category=error_category)
        await _record_transfer_audit(
            session,
            ctx,
            environment_id=call.environment_id,
            event_type="call_transfer_failed",
            result="failure",
            call_id=call.id,
            detail={"error_category": error_category, "state": result.state.value},
        )
        await session.commit()
        raise HTTPException(
            status_code=422,
            detail={"code": "transfer_rejected", "message": result.message, "state": result.state.value},
        )

    await complete_request(
        session,
        claim.receipt,
        resource_type="call_transfer",
        resource_id=call.id,
    )
    await _record_transfer_audit(
        session,
        ctx,
        environment_id=call.environment_id,
        event_type="call_transfer_initiated",
        result="success",
        call_id=call.id,
        detail={
            "destination": _redact_destination(destination),
            "reason_code": payload.reason if payload.reason in ALLOWED_TRANSFER_REASONS else "custom",
            "state": result.state.value,
        },
    )
    await session.commit()
    await session.refresh(call)
    _audit(
        "transfer.initiated",
        tenant_id=str(ctx.tenant_id),
        call_id=str(call.id),
        destination=_redact_destination(destination),
        reason_code=payload.reason if payload.reason in ALLOWED_TRANSFER_REASONS else "custom",
    )
    return TransferOut(
        id=str(call.id),
        call_id=str(call.id),
        destination=_redact_destination(destination),
        state=result.state.value if hasattr(result.state, "value") else str(result.state),
        reason=payload.reason or DEFAULT_TRANSFER_REASON,
        summary=payload.summary,
        whisper=safe_whisper or None,
        created_at=call.transfer_requested_at.isoformat() if call.transfer_requested_at else _now_iso(),
        updated_at=call.updated_at.isoformat() if call.updated_at else None,
        transfer_sid=None,
        duration_seconds=_transfer_duration(call),
        priority=payload.priority,
        metadata=metadata,
    )

@router.get("/{call_id}/transfer-details", response_model=TransferOut)
async def get_transfer(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/transfer-details — Get current transfer status."""
    call = await _get_call(session, ctx, call_id)
    if not hasattr(call, "transfer_state") or call.transfer_state == TransferState.NONE:
        raise HTTPException(status_code=404, detail="no transfer found for call")

    return TransferOut(
        id=str(call.id),
        call_id=str(call.id),
        destination=_redact_destination(call.transfer_destination or ""),
        state=call.transfer_state.value if hasattr(call.transfer_state, "value") else str(call.transfer_state),
        reason=_redact_credential_material(getattr(call, "transfer_reason", None) or "") or None,
        summary=_redact_credential_material(getattr(call, "summary", None) or "") or None,
        created_at=call.transfer_requested_at.isoformat() if hasattr(call, "transfer_requested_at") and call.transfer_requested_at else _now_iso(),
        updated_at=call.updated_at.isoformat() if hasattr(call, "updated_at") and call.updated_at else None,
        duration_seconds=_transfer_duration(call),
    )

@router.post("/{call_id}/transfer/cancel", status_code=501)
async def cancel_transfer(
    call_id: uuid.UUID,
    payload: TransferCancelRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Do not report cancellation until the provider supports/acknowledges it."""
    del call_id, payload, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "transfer_cancel_not_implemented", "message": "The configured transfer adapter has no verified cancel operation."},
    )

@router.post("/{call_id}/transfer/retry", response_model=TransferOut)
async def retry_transfer(
    call_id: uuid.UUID,
    payload: TransferRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Retry a failed transfer with a fresh idempotency key."""
    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    if call.transfer_state != TransferState.FAILED:
        raise HTTPException(status_code=409, detail="only a provider-confirmed failed transfer may be retried")
    if call.status in {
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.NO_ANSWER,
        CallStatus.CANCELLED,
    }:
        raise HTTPException(status_code=409, detail="call is terminal and cannot be retried")
    if not payload.idempotency_key or len(payload.idempotency_key.strip()) < MIN_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="a new Idempotency-Key is required for retry")
    return await initiate_transfer(call_id, payload, ctx, session, None)

@router.get("/transfers/history", response_model=TransferHistoryOut)
async def list_transfer_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/transfers/history — List transfer history in the selected environment."""
    scope = await _scope_for_transfer(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    filters = [
        Call.tenant_id == ctx.tenant_id,
        Call.environment_id == scope.id,
        Call.transfer_state != TransferState.NONE,
    ]
    if state:
        accepted_states = {item.value: item for item in TransferState}
        if state not in accepted_states:
            raise HTTPException(status_code=422, detail="invalid transfer state")
        filters.append(Call.transfer_state == accepted_states[state])

    total_q = await session.execute(select(func.count(Call.id)).where(*filters))
    total = total_q.scalar() or 0

    rows_q = await session.execute(
        select(Call).where(*filters).order_by(Call.transfer_requested_at.desc() if hasattr(Call, "transfer_requested_at") else Call.started_at.desc()).offset(offset).limit(limit)
    )
    rows = rows_q.scalars().all()

    transfers = []
    for r in rows:
        transfers.append(
            TransferOut(
                id=str(r.id),
                call_id=str(r.id),
                destination=_redact_destination(getattr(r, "transfer_destination", "") or ""),
                state=getattr(r, "transfer_state", "unknown").value if hasattr(getattr(r, "transfer_state", ""), "value") else str(getattr(r, "transfer_state", "unknown")),
                reason=_redact_credential_material(getattr(r, "transfer_reason", None) or "") or None,
                created_at=getattr(r, "transfer_requested_at", r.started_at).isoformat() if getattr(r, "transfer_requested_at", None) or r.started_at else _now_iso(),
                duration_seconds=_transfer_duration(r),
            )
        )

    return TransferHistoryOut(transfers=transfers, total=int(total), limit=limit, offset=offset)

# ---------------------------------------------------------------------------
# Warm-transfer context — summary + CRM context to human leg
# ---------------------------------------------------------------------------

@router.get("/{call_id}/transfer/context", response_model=WarmTransferContextOut)
async def get_warm_transfer_context(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    GET /api/calls/{id}/transfer/context — Warm-transfer context API.
    Returns generated summary + CRM context propagated to human leg.
    """
    call = await _get_call(session, ctx, call_id)

    crm_context = await _build_crm_context(session, ctx.tenant_id, call)
    transcript_excerpt = await _build_transcript_excerpt(session, call_id, max_turns=8)

    # Generate summary if not present
    summary = _redact_credential_material(getattr(call, "summary", None) or "") or None
    if not summary:
        summary = _generate_summary_from_context(
            crm_context,
            transcript_excerpt,
            _redact_credential_material(getattr(call, "transfer_reason", None) or "") or None,
        )

    # Extract intent/sentiment from call if available
    intent = None
    sentiment = None
    try:
        if hasattr(call, "intent"):
            intent = getattr(call, "intent")
        if hasattr(call, "sentiment"):
            sentiment = getattr(call, "sentiment")
    except Exception:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        pass

    return WarmTransferContextOut(
        call_id=str(call.id),
        summary=summary,
        crm_context=crm_context,
        transfer_reason=_redact_credential_material(getattr(call, "transfer_reason", None) or "") or None,
        customer_phone=call.from_number,
        customer_name=crm_context.get("lead_name"),
        agent_id=crm_context.get("agent_id"),
        transcript_excerpt=transcript_excerpt,
        call_duration_seconds=_transfer_duration(call),
        intent=intent,
        sentiment=sentiment,
        custom_fields=crm_context.get("custom_fields", {}),
        generated_at=_now_iso(),
    )

@router.post("/{call_id}/transfer/context", response_model=WarmTransferContextOut)
async def create_warm_transfer_context(
    call_id: uuid.UUID,
    summary: Optional[str] = Query(default=None, max_length=MAX_SUMMARY_LENGTH),
    crm_context: Optional[Dict[str, Any]] = None,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Persist bounded, tenant/environment-scoped warm-transfer context."""
    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    if summary is not None:
        _reject_credential_material(summary)
        call.summary = summary[:MAX_SUMMARY_LENGTH]
    if crm_context is not None:
        validated = _validate_context_data(crm_context)
        existing = dict(call.transfer_context or {})
        existing["crm_context"] = validated
        existing["updated_at"] = _now_iso()
        call.transfer_context = existing

    await _record_transfer_audit(
        session,
        ctx,
        environment_id=call.environment_id,
        event_type="call_transfer_context_updated",
        result="success",
        call_id=call.id,
        detail={
            "summary_updated": summary is not None,
            "crm_context_updated": crm_context is not None,
        },
    )
    await session.commit()
    await session.refresh(call)
    return await get_warm_transfer_context(call_id, ctx, session)

@router.get("/{call_id}/transfer/context/summary", response_model=dict)
async def get_transfer_summary(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/transfer/context/summary — Summary only for human leg."""
    context = await get_warm_transfer_context(call_id, ctx, session)
    return {
        "call_id": context.call_id,
        "summary": context.summary,
        "customer_phone": context.customer_phone,
        "customer_name": context.customer_name,
        "transfer_reason": context.transfer_reason,
        "generated_at": context.generated_at,
    }

@router.get("/{call_id}/transfer/analytics", response_model=TransferAnalyticsOut)
async def get_transfer_analytics(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/transfer/analytics — Transfer analytics for call."""
    call = await _get_call(session, ctx, call_id)

    # Count transfers for this call (should be 0 or 1, but support history)
    total = int(call.transfer_state != TransferState.NONE)
    successful = int(call.transfer_state == TransferState.CONNECTED)
    failed = int(call.transfer_state == TransferState.FAILED)

    return TransferAnalyticsOut(
        call_id=str(call.id),
        total_transfers=total,
        successful_transfers=successful,
        failed_transfers=failed,
        average_duration_seconds=(float(_transfer_duration(call)) if _transfer_duration(call) is not None else None),
        last_transfer_at=call.transfer_requested_at.isoformat() if hasattr(call, "transfer_requested_at") and call.transfer_requested_at else None,
        transfer_states={
            call.transfer_state.value if hasattr(call.transfer_state, "value") else str(call.transfer_state): 1
        } if total else {},
    )

# ---------------------------------------------------------------------------
# Additional endpoints — whisper, bridge status
# ---------------------------------------------------------------------------

@router.post("/{call_id}/transfer/whisper", status_code=501)
async def send_whisper(
    call_id: uuid.UUID,
    whisper: str = Query(..., min_length=1, max_length=MAX_WHISPER_LENGTH),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Do not report whisper delivery without a provider-confirmed operation."""
    del call_id, whisper, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "transfer_whisper_not_implemented", "message": "The active provider adapter does not support a verified whisper operation."},
    )

@router.get("/{call_id}/transfer/bridge", response_model=dict)
async def get_bridge_status(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/transfer/bridge — Bridge status between customer and human."""
    call = await _get_call(session, ctx, call_id)

    bridged = call.transfer_state.value == "connected" if hasattr(call.transfer_state, "value") else str(call.transfer_state) == "connected"

    return {
        "call_id": str(call.id),
        "bridged": bridged,
        "customer_leg": {"phone": call.from_number, "status": call.status.value if hasattr(call.status, "value") else str(call.status)},
        "human_leg": {"destination": _redact_destination(getattr(call, "transfer_destination", "") or ""), "state": str(getattr(call, "transfer_state", "unknown"))},
        "at": _now_iso(),
    }

@router.get("/transfers/stats", response_model=dict)
async def transfer_stats(
    days: int = Query(default=7, ge=1, le=90),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Count persisted transfer attempts in the selected environment."""
    scope = await _scope_for_transfer(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    since = _now() - timedelta(days=days)
    total = int(
        (await session.execute(
            select(func.count(Call.id)).where(
                Call.tenant_id == ctx.tenant_id,
                Call.environment_id == scope.id,
                Call.transfer_requested_at >= since,
                Call.transfer_state != TransferState.NONE,
            )
        )).scalar_one() or 0
    )
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "days": days,
        "since": since.isoformat(),
        "total_transfers": total,
        "at": _now_iso(),
    }

@router.get("/transfers/idempotency/stats")
async def idempotency_stats(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Return durable transfer receipt counts for the selected environment."""
    scope = await _scope_for_transfer(
        session, ctx, permission=Permission.TENANT_READ, for_write=False
    )
    rows = (
        await session.execute(
            select(RequestIdempotencyReceipt.status, func.count(RequestIdempotencyReceipt.id))
            .where(
                RequestIdempotencyReceipt.tenant_id == ctx.tenant_id,
                RequestIdempotencyReceipt.environment_scope == str(scope.id),
                RequestIdempotencyReceipt.operation == "legacy.call.transfer",
            )
            .group_by(RequestIdempotencyReceipt.status)
        )
    ).all()
    counts = {str(row_status): int(count) for row_status, count in rows}
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "total": sum(counts.values()),
        "in_progress": counts.get("in_progress", 0),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "at": _now_iso(),
    }


@router.delete("/transfers/idempotency/cache", status_code=410)
async def clear_idempotency(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
):
    """Do not delete receipts that prevent duplicate provider transfers."""
    del ctx
    raise HTTPException(
        status_code=410,
        detail={"code": "durable_idempotency_not_clearable", "message": "Transfer idempotency receipts are retained to prevent duplicate provider side effects."},
    )

# ---------------------------------------------------------------------------
# Additional endpoints — bulk, templates, health, config (expand to 1000+)
# ---------------------------------------------------------------------------

@router.get("/transfers/templates", response_model=dict)
async def transfer_templates(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
):
    return {
        "templates": [
            {"reason": "customer_requested", "label": "Customer requested human", "whisper_template": "Customer {name} requested human assistance"},
            {"reason": "escalation", "label": "Escalation to supervisor", "whisper_template": "Escalation: {summary}"},
            {"reason": "compliance", "label": "Compliance review", "whisper_template": "Compliance check required for {phone}"},
            {"reason": "technical", "label": "Technical issue", "whisper_template": "Technical issue reported: {reason}"},
        ],
        "allowed_reasons": sorted(ALLOWED_TRANSFER_REASONS),
        "at": _now_iso(),
    }

@router.post("/transfers/bulk", status_code=501)
async def bulk_transfer(
    call_ids: List[uuid.UUID],
    destination: str = Query(..., min_length=8, max_length=MAX_DESTINATION_LENGTH),
    reason: Optional[str] = Query(default=None, max_length=MAX_REASON_LENGTH),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Fail closed until each call can get an independent durable receipt."""
    del call_ids, destination, reason, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "bulk_transfer_not_implemented", "message": "Bulk transfer is disabled because this route cannot yet provide per-call provider confirmation and durable idempotency."},
    )

@router.get("/transfers/health", response_model=dict)
async def transfer_health(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Scoped database observability; not a claim about provider health."""
    scope = await _scope_for_transfer(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    filters = (Call.tenant_id == ctx.tenant_id, Call.environment_id == scope.id)
    total_calls = int(
        (await session.execute(select(func.count(Call.id)).where(*filters))).scalar_one() or 0
    )
    transfer_count = int(
        (await session.execute(
            select(func.count(Call.id)).where(*filters, Call.transfer_state != TransferState.NONE)
        )).scalar_one() or 0
    )
    in_flight = int(
        (await session.execute(
            select(func.count(Call.id)).where(
                *filters,
                Call.transfer_state.in_(tuple(TRANSFER_IN_FLIGHT)),
            )
        )).scalar_one() or 0
    )
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "total_calls": total_calls,
        "transfers": transfer_count,
        "transfers_in_flight": in_flight,
        "database_check": "query_succeeded",
        "provider_health": "not_checked",
        "at": _now_iso(),
    }

@router.get("/transfers/config", response_model=dict)
async def transfer_config(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    return {
        "max_destination_length": MAX_DESTINATION_LENGTH,
        "max_reason_length": MAX_REASON_LENGTH,
        "max_summary_length": MAX_SUMMARY_LENGTH,
        "max_whisper_length": MAX_WHISPER_LENGTH,
        "timeout_seconds": TRANSFER_TIMEOUT_SECONDS,
        "allowed_reasons": sorted(ALLOWED_TRANSFER_REASONS),
        "rate_limit_per_minute": 20,
        "idempotency_ttl_hours": 24,
        "at": _now_iso(),
    }

@router.post("/{call_id}/transfer/complete", status_code=501)
async def complete_transfer(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Only a verified provider callback may report transfer completion."""
    del call_id, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "client_transfer_completion_disabled", "message": "Transfer completion is accepted only from the authenticated provider callback path."},
    )


@router.post("/{call_id}/transfer/fail", status_code=501)
async def fail_transfer(
    call_id: uuid.UUID,
    reason: str = Query(..., min_length=1, max_length=MAX_REASON_LENGTH),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
):
    """Only a verified provider callback may report transfer failure."""
    del call_id, reason, ctx
    raise HTTPException(
        status_code=501,
        detail={"code": "client_transfer_failure_disabled", "message": "Transfer failure is accepted only from the authenticated provider callback path."},
    )

@router.get("/transfers/analytics/summary", response_model=dict)
async def transfers_analytics_summary(
    days: int = Query(default=7, ge=1, le=90),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = await _scope_for_transfer(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    since = _now() - timedelta(days=days)
    scoped = (Call.tenant_id == ctx.tenant_id, Call.environment_id == scope.id)
    total_calls = int(
        (await session.execute(
            select(func.count(Call.id)).where(*scoped, Call.started_at >= since)
        )).scalar_one() or 0
    )
    total_transfers = int(
        (await session.execute(
            select(func.count(Call.id)).where(
                *scoped,
                Call.transfer_requested_at >= since,
                Call.transfer_state != TransferState.NONE,
            )
        )).scalar_one() or 0
    )
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "days": days,
        "since": since.isoformat(),
        "total_calls": total_calls,
        "total_transfers": total_transfers,
        "at": _now_iso(),
    }


@router.get("/transfers/metrics", response_model=dict)
async def transfer_metrics(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = await _scope_for_transfer(
        session, ctx, permission=Permission.CALL_READ, for_write=False
    )
    scoped = (Call.tenant_id == ctx.tenant_id, Call.environment_id == scope.id)
    total_calls = int(
        (await session.execute(select(func.count(Call.id)).where(*scoped))).scalar_one() or 0
    )
    transfer_count = int(
        (await session.execute(
            select(func.count(Call.id)).where(*scoped, Call.transfer_state != TransferState.NONE)
        )).scalar_one() or 0
    )
    return {
        "tenant_id": str(ctx.tenant_id),
        "environment_id": str(scope.id),
        "total_calls": total_calls,
        "total_transfers": transfer_count,
        "allowed_reasons": sorted(ALLOWED_TRANSFER_REASONS),
        "config": {
            "max_destination_length": MAX_DESTINATION_LENGTH,
            "timeout_seconds": TRANSFER_TIMEOUT_SECONDS,
        },
        "at": _now_iso(),
    }


@router.delete("/transfers/cache", status_code=410)
async def clear_transfer_cache(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
):
    """Retained compatibility endpoint; authoritative receipts cannot be cleared."""
    del ctx
    raise HTTPException(
        status_code=410,
        detail={"code": "transfer_cache_removed", "message": "Process-local transfer caches were removed. Durable idempotency receipts are intentionally retained."},
    )


class WarmTransferBriefingRequest(_Strict):
    destination: str = Field(min_length=8, max_length=MAX_DESTINATION_LENGTH)
    reason: Optional[str] = Field(default="customer_requested", max_length=MAX_REASON_LENGTH)
    summary: Optional[str] = Field(default=None, max_length=MAX_SUMMARY_LENGTH)
    caller_name: Optional[str] = Field(default=None, max_length=120)
    sentiment: Optional[str] = Field(default=None, max_length=64)


@router.post("/{call_id}/transfer/warm", status_code=201)
async def start_warm_transfer_with_briefing(
    call_id: uuid.UUID,
    payload: WarmTransferBriefingRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Initiate a Warm Transfer with Conference Hold and Whispered Briefing to the human leg (2D)."""
    from app.telephony.transfer_service import initiate_warm_transfer

    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    transcript_excerpt = await _build_transcript_excerpt(session, call.id, max_turns=6)
    res = await initiate_warm_transfer(
        session,
        tenant=ctx.tenant,
        call=call,
        destination_override=_normalize_destination(payload.destination),
        reason=payload.reason or DEFAULT_TRANSFER_REASON,
        summary=payload.summary,
        caller_name=payload.caller_name,
        sentiment=payload.sentiment,
        transcript_excerpt=transcript_excerpt,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res)
    return res


@router.post("/{call_id}/transfer/warm/bridge")
async def bridge_warm_transfer(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Unhold the caller and bridge caller + human in the conference after briefing completes (2D)."""
    from app.telephony.transfer_service import complete_warm_transfer

    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    return await complete_warm_transfer(session, call=call)


@router.post("/{call_id}/transfer/warm/abort")
async def abort_warm_transfer(
    call_id: uuid.UUID,
    payload: TransferCancelRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Take caller off conference hold and return conversation to the AI agent when human declines/no-answers (2D)."""
    from app.telephony.transfer_service import abort_warm_transfer_to_ai

    call = await _get_call(
        session, ctx, call_id, permission=Permission.CALL_WRITE, for_write=True
    )
    return await abort_warm_transfer_to_ai(
        session,
        call=call,
        reason=payload.reason or "no_answer",
    )
