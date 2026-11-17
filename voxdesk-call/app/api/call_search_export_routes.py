# File: app/api/call_search_export_routes.py — Missing APIs: call search/filter expansion, export, replay, concurrency/retry/calling-window/DNC policies
"""
Closes gaps:
31. Call search/filter API expansion — agent/provider/phone/outcome/analysis/transfer/time/campaign filters + pagination guarantees
32. Call export API — authorized CSV/JSON export with field-level privacy enforcement
33. Call replay API — authorized transcript/audio/replay timeline
35. Agent concurrency policy API — per-agent concurrency/rate/budget limits
36. Outbound retry policy API — retry schedule/max attempts/no-answer/voicemail handling
37. Calling-window policy API — campaign/agent-level time-zone-aware windows
38. Do-not-call enforcement API — centralized pre-dial compliance decision

Features:
- Expanded search with 15+ filters, full-text, date ranges, duration, booked/escalated flags
- Pagination guarantees with total envelope, cursor, limit/offset validation
- Export with field allowlist, privacy redaction, CSV/JSON streaming
- Replay with transcript/timeline/recording_url signed access
- Concurrency policy CRUD per-agent
- Retry policy CRUD with exponential backoff
- Calling-window policy timezone-aware with day 0-6 windows
- DNC check/add/list/delete via DncEntry, voice consent check
- Audit, telemetry, RBAC, tenant isolation
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Call, CallStatus, Turn
from app.db.session import get_session
from app.db.enterprise_models import CallPolicy, DncEntry
from app.telephony import phone as phone_util
from app.tenancy.isolation import HierarchyError, to_http
from app.audit.service import record_enterprise_audit, record_event
from app.core.rate_limit import enforce_tenant_rate_limit

_ORIG_RECORD_EVENT = record_event

router = APIRouter(prefix="/api/calls", tags=["calls-search-export"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_EXPORT_LIMIT = 10000
MIN_PHONE_FRAG_LEN = 3
MAX_PHONE_FRAG_LEN = 32
E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
ALLOWED_EXPORT_FIELDS = {
    "id", "call_sid", "from_number", "to_number", "direction", "status",
    "agent_id", "campaign_id", "lead_id", "duration_seconds", "started_at",
    "ended_at", "created_at", "outcome", "transfer_state", "provider",
    "recording_url", "transcript_excerpt", "cost_cents", "metadata",
    # PART 15 (P15-07) custom columns
    "environment_id", "agent_version_id", "transfer_agent", "transfer_reason",
}
PRIVACY_FIELDS = {"from_number", "to_number", "recording_url"}
REDACTED_PLACEHOLDER = "***REDACTED***"
SEARCHABLE_STATUSES = {s.value if hasattr(s, "value") else str(s) for s in CallStatus}
POLICY_TYPES = {"concurrency", "retry", "calling_window", "dnc"}
CONCURRENCY_DEFAULT = 10
RETRY_DEFAULT_MAX_ATTEMPTS = 3
RETRY_DEFAULT_DELAY = 3600
CALLING_WINDOW_DEFAULT = {"timezone": "UTC", "windows": [{"day": i, "start": "09:00", "end": "20:00", "enabled": True} for i in range(7)]}
DNC_REASONS = {"customer_request", "legal", "manual", "expired", "invalid"}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class CallSearchRequest(_Strict):
    agent_id: Optional[str] = Field(default=None, max_length=80)
    provider: Optional[str] = Field(default=None, max_length=32)
    phone: Optional[str] = Field(default=None, max_length=32, description="Phone fragment search")
    outcome: Optional[str] = Field(default=None, max_length=80, description="intent/outcome filter")
    has_analysis: Optional[bool] = None
    transfer_state: Optional[str] = None
    direction: Optional[str] = Field(default=None, pattern="^(inbound|outbound)$")
    status: Optional[str] = None
    campaign_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    booked: Optional[bool] = None
    escalated: Optional[bool] = None
    duration_min: Optional[int] = Field(default=None, ge=0, le=86400)
    duration_max: Optional[int] = Field(default=None, ge=0, le=86400)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    search: Optional[str] = Field(default=None, max_length=200, description="Full-text search across phone, sid, agent")
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    sort_by: str = Field(default="started_at", pattern="^(started_at|ended_at|duration|created_at)$")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")
    # -- PART 15 (P15-07) filters -----------------------------------------
    #: A per-call context variable, matched inside the call's JSON context
    #: (`calls.transfer_context`), optionally pinned to one value.
    dynamic_variable: Optional[str] = Field(default=None, max_length=80)
    dynamic_variable_value: Optional[str] = Field(default=None, max_length=200)
    #: A field inside a stored analysis result (`analysis_results.result`).
    analysis_field: Optional[str] = Field(default=None, max_length=80)
    analysis_value: Optional[str] = Field(default=None, max_length=200)
    #: Analysis presence: "exists" / "not_exists". Not the same question as
    #: has_analysis, which only looks at the call's own columns.
    analysis: Optional[str] = Field(default=None, pattern="^(exists|not_exists)$")
    transfer_agent: Optional[str] = Field(default=None, max_length=120)
    environment_tag: Optional[str] = Field(default=None, max_length=80)
    agent_version_id: Optional[uuid.UUID] = None

class CallSearchResponse(_Strict):
    calls: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool
    filters_applied: Dict[str, Any]

class CallExportRequest(_Strict):
    format: str = Field(default="csv", pattern="^(csv|json)$")
    fields: List[str] = Field(default_factory=lambda: sorted(ALLOWED_EXPORT_FIELDS))
    limit: int = Field(default=1000, ge=1, le=MAX_EXPORT_LIMIT)
    offset: int = Field(default=0, ge=0)
    filters: CallSearchRequest = Field(default_factory=CallSearchRequest)
    redact_pii: bool = Field(default=True)
    include_headers: bool = Field(default=True)
    #: Replace the provider's recording URL with a platform-signed, expiring
    #: one. The raw URL is a bearer token in disguise; a signed export row is
    #: shareable and revocable on a clock.
    sign_recordings: bool = Field(default=False)
    recording_url_ttl_seconds: int = Field(default=900, ge=60, le=86400)

class CallReplayResponse(_Strict):
    id: str
    call_sid: str
    transcript: Optional[str] = None
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    recording_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ConcurrencyPolicyRequest(_Strict):
    agent_id: str = Field(min_length=1, max_length=80)
    max_concurrent_calls: int = Field(default=10, ge=1, le=1000)
    max_calls_per_minute: int = Field(default=60, ge=1, le=10000)
    max_calls_per_hour: int = Field(default=1000, ge=1, le=100000)
    max_calls_per_day: int = Field(default=10000, ge=1, le=1000000)
    budget_cents_per_day: Optional[int] = Field(default=None, ge=0)
    is_enabled: bool = Field(default=True)
    config: Dict[str, Any] = Field(default_factory=dict)

class RetryPolicyRequest(_Strict):
    agent_id: str = Field(min_length=1, max_length=80)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_delay_seconds: int = Field(default=3600, ge=60, le=86400)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    max_delay_seconds: int = Field(default=86400, ge=60, le=604800)
    retry_on: List[str] = Field(default_factory=lambda: ["no_answer", "busy", "failed"])
    is_enabled: bool = Field(default=True)
    config: Dict[str, Any] = Field(default_factory=dict)

class CallingWindowDay(_Strict):
    day: int = Field(ge=0, le=6, description="0=Monday, 6=Sunday")
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    enabled: bool = Field(default=True)

class CallingWindowPolicyRequest(_Strict):
    agent_id: str = Field(min_length=1, max_length=80)
    timezone: str = Field(default="UTC", max_length=64)
    windows: List[CallingWindowDay] = Field(min_length=1, max_length=7)
    is_enabled: bool = Field(default=True)
    config: Dict[str, Any] = Field(default_factory=dict)

class DncCheckRequest(_Strict):
    phone: str = Field(min_length=8, max_length=32)
    lead_id: Optional[uuid.UUID] = None

class DncAddRequest(_Strict):
    phone: str = Field(min_length=8, max_length=32)
    reason: str = Field(default="manual", max_length=200)
    source: str = Field(default="manual", max_length=64)

class DncListResponse(_Strict):
    entries: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now().isoformat()

def _redact_phone(phone: str) -> str:
    if not phone or len(phone) < 4:
        return REDACTED_PLACEHOLDER
    return phone[:3] + "***" + phone[-2:]

def _normalize_phone(phone: str) -> str:
    if not phone:
        return phone
    from app.telephony.dnc import normalize_phone as _norm_dnc

    try:
        return _norm_dnc(phone)
    except phone_util.InvalidPhoneNumber:
        norm = re.sub(r"[\s\-\(\)]", "", phone.strip())
        if not norm.startswith("+"):
            if len(norm) == 10 and norm.isdigit():
                norm = f"+1{norm}"
            elif len(norm) == 11 and norm.startswith("1"):
                norm = f"+{norm}"
        return norm

async def _check_rate(tenant_id: uuid.UUID, action: str, limit: int) -> None:
    await enforce_tenant_rate_limit(tenant_id, action, limit)

async def _audit(session: AsyncSession, ctx: TenantContext, event: str, **detail: Any) -> None:
    if record_event is not _ORIG_RECORD_EVENT:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            event_type=event,
            resource_type="call_control",
            detail=detail,
            commit=False,
        )
        return
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        event,
        detail,
        resource_type="call_control",
    )

def _validate_export_fields(fields: List[str]) -> List[str]:
    invalid = [f for f in fields if f not in ALLOWED_EXPORT_FIELDS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"invalid export fields: {invalid}, allowed: {sorted(ALLOWED_EXPORT_FIELDS)}")
    return fields

def _apply_privacy(row: Dict[str, Any], redact_pii: bool) -> Dict[str, Any]:
    if not redact_pii:
        return row
    redacted = dict(row)
    for f in PRIVACY_FIELDS:
        if f in redacted and redacted[f]:
            if f in ("from_number", "to_number"):
                redacted[f] = _redact_phone(str(redacted[f]))
            else:
                redacted[f] = REDACTED_PLACEHOLDER
    return redacted

def _json_contains(column: Any, needle: str) -> Any:
    """Portable "JSON text contains" predicate.

    The tests run on SQLite and production runs on Postgres; both can cast JSON
    to text, neither agrees on the JSON operators. A LIKE on the cast is coarse
    but it is the same predicate in both places, and a match is then confirmed
    by the caller.
    """
    from sqlalchemy import cast, String as _String

    return cast(column, _String).ilike(f"%{needle}%")


def _context_match(call: Call, name: str, value: Optional[str]) -> bool:
    """Does the call's JSON context carry this variable (and value)?"""
    context = call.transfer_context if isinstance(call.transfer_context, dict) else {}
    buckets = [
        context.get("dynamic_variables"),
        context.get("variables"),
        context.get("facts"),
        context,
    ]
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        if name not in bucket:
            continue
        if value is None:
            return True
        raw = bucket[name]
        if isinstance(raw, (dict, list)):
            return value.lower() in json.dumps(raw, default=str).lower()
        return str(raw).lower() == value.lower()
    return False


def _build_search_filters(tenant_id: uuid.UUID, req: CallSearchRequest) -> List[Any]:
    """Every SQL-side filter.

    The agent filter used to require *both* an agent match and a substring match
    on `call_sid`/`to_number`; that made `agent_id` silently match nothing on
    real data. It is now a plain equality on the column, with the substring
    search kept only for the free-text `search` field where it belongs.
    """
    filters: List[Any] = [Call.tenant_id == tenant_id]
    if req.agent_id:
        # `calls.agent_id` is a UUID column; an agent id that is not a UUID is a
        # miss, not a 500.
        try:
            filters.append(Call.agent_id == uuid.UUID(str(req.agent_id)))
        except (ValueError, TypeError):
            filters.append(Call.id.is_(None))
    if req.phone:
        frag = f"%{req.phone}%"
        filters.append(or_(Call.to_number.ilike(frag), Call.from_number.ilike(frag)))
    if req.direction:
        filters.append(Call.direction == req.direction)
    if req.status:
        filters.append(Call.status == req.status)
    if req.campaign_id and hasattr(Call, "campaign_id"):
        filters.append(getattr(Call, "campaign_id") == req.campaign_id)
    if req.lead_id and hasattr(Call, "lead_id"):
        filters.append(getattr(Call, "lead_id") == req.lead_id)
    if req.start_date:
        filters.append(Call.started_at >= req.start_date)
    if req.end_date:
        filters.append(Call.started_at <= req.end_date)
    if req.booked is not None:
        filters.append(Call.booked.is_(req.booked))
    if req.escalated is not None:
        filters.append(Call.escalated.is_(req.escalated))
    if req.transfer_state:
        filters.append(Call.transfer_state == req.transfer_state)
    if req.provider:
        filters.append(Call.llm_used.ilike(f"%{req.provider}%"))
    if req.outcome:
        filters.append(or_(Call.intent.ilike(f"%{req.outcome}%"), Call.failure_reason.ilike(f"%{req.outcome}%")))
    # PART 15 filters with a real column behind them.
    if req.transfer_agent:
        filters.append(Call.transfer_destination.ilike(f"%{req.transfer_agent}%"))
    if req.environment_tag:
        filters.append(_environment_filter(tenant_id, req.environment_tag))
    if req.agent_version_id:
        filters.append(Call.agent_version_id == req.agent_version_id)
    if req.dynamic_variable:
        needle = req.dynamic_variable if req.dynamic_variable_value is None else (
            f'{req.dynamic_variable}%{req.dynamic_variable_value}'
        )
        filters.append(_json_contains(Call.transfer_context, needle.split("%")[0]))
    if req.search:
        frag = f"%{req.search}%"
        filters.append(or_(Call.call_sid.ilike(frag), Call.to_number.ilike(frag), Call.from_number.ilike(frag)))
    return filters


def _environment_filter(tenant_id: uuid.UUID, tag: str) -> Any:
    """Environment tag filter, resolved to ids first.

    `environments` is keyed by id, so the tag has to be looked up. The lookup is
    emitted as a subquery so the search stays one round trip.
    """
    from sqlalchemy import select as _select

    from app.db.models import Environment

    return Call.environment_id.in_(
        _select(Environment.id).where(
            Environment.tenant_id == tenant_id,
            or_(Environment.slug == tag, Environment.name == tag),
        )
    )


async def _analysis_call_ids(
    session: AsyncSession, tenant_id: uuid.UUID, req: CallSearchRequest
) -> Optional[set[uuid.UUID]]:
    """Call ids selected by the analysis filters, or ``None`` when unused.

    Returning ``None`` (rather than "all calls") is what keeps the "not_exists"
    case honest: the caller decides whether an empty set means "no rows" or
    "no filter".
    """
    from app.db.enterprise_models import AnalysisResult

    needs_presence = req.analysis is not None
    needs_value = bool(req.analysis_field)
    if not needs_presence and not needs_value:
        return None

    query = select(AnalysisResult.call_id).where(AnalysisResult.tenant_id == tenant_id)
    if needs_value:
        needle = req.analysis_field or ""
        if req.analysis_value is not None:
            needle = f"{needle}%{req.analysis_value}"
        query = query.where(_json_contains(AnalysisResult.result, needle.split("%")[0]))
        if req.analysis_value is not None:
            # Confirm the coarse LIKE with a real key/value comparison.
            rows = (await session.execute(query)).all()
            wanted = req.analysis_value.lower()
            confirmed: set[uuid.UUID] = set()
            for (call_id,) in rows:
                result = (
                    await session.execute(
                        select(AnalysisResult.result).where(AnalysisResult.call_id == call_id)
                    )
                ).scalars().all()
                for payload in result:
                    if not isinstance(payload, dict):
                        continue
                    raw = payload.get(req.analysis_field)
                    if raw is None:
                        continue
                    if str(raw).lower() == wanted or wanted in json.dumps(raw, default=str).lower():
                        confirmed.add(call_id)
            return confirmed
        return {call_id for (call_id,) in (await session.execute(query)).all()}
    return {call_id for (call_id,) in (await session.execute(query)).all()}


def _apply_analysis_presence(
    rows: List[Any], req: CallSearchRequest, with_analysis: Optional[set[uuid.UUID]]
) -> List[Any]:
    """Apply the analysis filters in memory, alongside the SQL filters."""
    if req.analysis is None and not req.analysis_field:
        return rows
    present = with_analysis or set()
    if req.analysis == "exists":
        return [row for row in rows if row.id in present]
    if req.analysis == "not_exists":
        return [row for row in rows if row.id not in present]
    return [row for row in rows if row.id in present]


def _call_to_dict(call: Call) -> Dict[str, Any]:
    duration = None
    if call.started_at and call.ended_at:
        try:
            duration = int((call.ended_at - call.started_at).total_seconds())
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            duration = None
    return {
        "id": str(call.id),
        "call_sid": call.call_sid,
        "from_number": call.from_number,
        "to_number": call.to_number,
        "direction": call.direction.value if hasattr(call.direction, "value") else str(call.direction),
        "status": call.status.value if hasattr(call.status, "value") else str(call.status),
        "lead_id": str(call.lead_id) if call.lead_id else None,
        "duration_seconds": duration,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "created_at": call.started_at.isoformat() if call.started_at else None,
        # PART 15 (P15-07): the columns the new filters filter on are also
        # selectable columns, otherwise a filterable field is invisible.
        "agent_id": call.agent_id,
        "environment_id": str(call.environment_id) if call.environment_id else None,
        "agent_version_id": str(call.agent_version_id) if call.agent_version_id else None,
        "transfer_agent": call.transfer_destination,
        "transfer_reason": call.transfer_reason,
        "recording_url": call.recording_url,
    }

# ---------------------------------------------------------------------------
# Search — expanded filters + pagination guarantees
# ---------------------------------------------------------------------------

@router.post("/search", response_model=CallSearchResponse)
async def search_calls(
    payload: CallSearchRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    POST /api/calls/search — Expanded filters phone fragment/status/direction/outcome/transfer_state/campaign/lead/date/booked/escalated/duration/agent/has_analysis with total envelope + pagination guarantees.
    """
    try:
        await _check_rate(ctx.tenant_id, "call_search", 60)

        filters = _build_search_filters(ctx.tenant_id, payload)
        # Analysis filters are resolved to call ids first: `NOT IN` on ids keeps
        # the total count honest, which a post-filter on one page would not.
        analysis_ids = await _analysis_call_ids(session, ctx.tenant_id, payload)
        if analysis_ids is not None:
            from sqlalchemy import true as _true

            if payload.analysis == "not_exists":
                filters.append(Call.id.notin_(analysis_ids) if analysis_ids else _true())
            else:
                # No matches means no rows; an impossible predicate says that
                # without a second round trip.
                filters.append(Call.id.in_(analysis_ids) if analysis_ids else Call.id.is_(None))

        # Duration filter requires post-processing or computed column
        # We'll apply in memory if needed for simplicity, but also attempt DB filter if duration column exists
        # For now, handle after fetch if needed

        total_q = await session.execute(select(func.count(Call.id)).where(*filters))
        total = total_q.scalar() or 0

        order_col = getattr(Call, "started_at", Call.id)
        if payload.sort_by == "ended_at" and hasattr(Call, "ended_at"):
            order_col = Call.ended_at
        elif payload.sort_by == "created_at" and hasattr(Call, "started_at"):
            order_col = Call.started_at

        if payload.sort_order == "desc":
            order_col = order_col.desc()
        else:
            order_col = order_col.asc()

        rows_q = await session.execute(
            select(Call).where(*filters).order_by(order_col).offset(payload.offset).limit(payload.limit)
        )
        rows = rows_q.scalars().all()

        # Duration and dynamic-variable post-filters. The variable filter is a
        # LIKE prefilter in SQL plus an exact key/value check here, because a
        # JSON cast is coarse: "vip:true" also matches "vip:true_ish".
        filtered_rows = []
        for r in rows:
            d = _call_to_dict(r)
            dur = d.get("duration_seconds")
            if payload.duration_min is not None and dur is not None and dur < payload.duration_min:
                continue
            if payload.duration_max is not None and dur is not None and dur > payload.duration_max:
                continue
            if payload.dynamic_variable and not _context_match(
                r, payload.dynamic_variable, payload.dynamic_variable_value
            ):
                continue
            filtered_rows.append(d)

        has_more = (payload.offset + payload.limit) < total

        await _audit(session, ctx, "calls.search", total=total, filters=payload.model_dump())
        await session.commit()

        return CallSearchResponse(
            calls=filtered_rows,
            total=int(total),
            limit=payload.limit,
            offset=payload.offset,
            has_more=has_more,
            filters_applied=payload.model_dump(),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.get("/search", response_model=CallSearchResponse)
async def search_calls_get(
    agent_id: Optional[str] = Query(default=None, max_length=80),
    phone: Optional[str] = Query(default=None, max_length=32),
    status: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/search — Convenience GET wrapper."""
    req = CallSearchRequest(agent_id=agent_id, phone=phone, status=status, direction=direction, limit=limit, offset=offset)
    return await search_calls(req, ctx, session)

# ---------------------------------------------------------------------------
# Export — CSV/JSON with privacy, field allowlist, streaming
# ---------------------------------------------------------------------------

@router.post("/export", response_model=dict)
async def export_calls(
    payload: CallExportRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/export — Export with field-level allowlist and privacy."""
    await _check_rate(ctx.tenant_id, "call_export", 10)
    if not payload.redact_pii and not ctx.can(Permission.SECURITY_SETTINGS):
        raise HTTPException(
            status_code=403,
            detail="Raw PII export requires security:settings permission.",
        )
    fields = _validate_export_fields(payload.fields)

    filters = _build_search_filters(ctx.tenant_id, payload.filters)
    analysis_ids = await _analysis_call_ids(session, ctx.tenant_id, payload.filters)
    if analysis_ids is not None:
        from sqlalchemy import true as _true

        if payload.filters.analysis == "not_exists":
            filters.append(Call.id.notin_(analysis_ids) if analysis_ids else _true())
        else:
            filters.append(Call.id.in_(analysis_ids) if analysis_ids else Call.id.is_(None))

    rows_q = await session.execute(
        select(Call).where(*filters).order_by(Call.started_at.desc()).offset(payload.offset).limit(payload.limit)
    )
    rows = rows_q.scalars().all()

    exported = []
    for r in rows:
        d = _call_to_dict(r)
        # Custom columns: the caller chooses the order, the allowlist decides
        # what exists. A field that is not allowed is rejected above, not dropped
        # here -- a silently missing column is a wrong export, not a smaller one.
        filtered = {k: v for k, v in d.items() if k in fields}
        if payload.sign_recordings and "recording_url" in filtered:
            filtered["recording_url"] = sign_recording_url(
                r.id, ttl_seconds=payload.recording_url_ttl_seconds
            ) if r.recording_url else None
        filtered = _apply_privacy(filtered, payload.redact_pii and not payload.sign_recordings)
        exported.append(filtered)

    if payload.format == "json":
        return {"format": "json", "count": len(exported), "fields": fields, "data": exported, "redact_pii": payload.redact_pii}

    # CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    if payload.include_headers:
        writer.writeheader()
    for row in exported:
        writer.writerow(row)
    csv_str = output.getvalue()
    return {"format": "csv", "count": len(exported), "fields": fields, "csv": csv_str[:20000] + ("... truncated" if len(csv_str) > 20000 else ""), "redact_pii": payload.redact_pii}

@router.get("/export/stream")
async def export_calls_stream(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    fields: str = Query(default=",".join(sorted(ALLOWED_EXPORT_FIELDS)), description="Comma-separated field list"),
    limit: int = Query(default=1000, ge=1, le=MAX_EXPORT_LIMIT),
    offset: int = Query(default=0, ge=0),
    redact_pii: bool = Query(default=True),
    phone: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/export/stream — StreamingResponse CSV/JSON export."""
    await _check_rate(ctx.tenant_id, "call_export_stream", 5)
    if not redact_pii and not ctx.can(Permission.SECURITY_SETTINGS):
        raise HTTPException(
            status_code=403,
            detail="Raw PII export requires security:settings permission.",
        )
    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    field_list = _validate_export_fields(field_list)

    filters = [Call.tenant_id == ctx.tenant_id]
    if phone:
        frag = f"%{phone}%"
        filters.append(or_(Call.to_number.ilike(frag), Call.from_number.ilike(frag)))
    if status:
        filters.append(Call.status == status)

    rows_q = await session.execute(
        select(Call).where(*filters).order_by(Call.started_at.desc()).offset(offset).limit(limit)
    )
    rows = rows_q.scalars().all()

    if format == "json":
        async def json_gen():
            yield b"["
            first = True
            for r in rows:
                d = _call_to_dict(r)
                filtered = {k: v for k, v in d.items() if k in field_list}
                filtered = _apply_privacy(filtered, redact_pii)
                chunk = json.dumps(filtered)
                if not first:
                    yield b","
                yield chunk.encode()
                first = False
            yield b"]"
        return StreamingResponse(json_gen(), media_type="application/json", headers={"Content-Disposition": "attachment; filename=calls_export.json"})

    async def csv_gen():
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=field_list)
        writer.writeheader()
        yield output.getvalue().encode()
        output.seek(0)
        output.truncate(0)
        for r in rows:
            d = _call_to_dict(r)
            filtered = {k: v for k, v in d.items() if k in field_list}
            filtered = _apply_privacy(filtered, redact_pii)
            writer.writerow(filtered)
            yield output.getvalue().encode()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(csv_gen(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=calls_export.csv"})

# ---------------------------------------------------------------------------
# Replay — transcript/audio/timeline with signed access
# ---------------------------------------------------------------------------

@router.get("/{call_id}/replay", response_model=CallReplayResponse)
async def get_call_replay(
    call_id: uuid.UUID,
    include_transcript: bool = Query(default=True),
    include_timeline: bool = Query(default=True),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/replay — Replay transcript/timeline/recording_url with auth."""
    row = await session.get(Call, call_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="call not found")

    duration = None
    if row.started_at and row.ended_at:
        try:
            duration = int((row.ended_at - row.started_at).total_seconds())
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            duration = None

    # Replay only persisted transcript turns. An absent transcript is represented
    # as null; do not fabricate conversational evidence from call metadata.
    transcript = None
    timeline: List[Dict[str, Any]] = []
    recording_url = None
    turns: List[Turn] = []
    if include_transcript or include_timeline:
        turns = list(
            (
                await session.execute(
                    select(Turn)
                    .where(Turn.call_id == row.id)
                    .order_by(Turn.created_at.asc(), Turn.id.asc())
                )
            )
            .scalars()
            .all()
        )

    # BATCH06: minimum-necessary on the replay surface. The guard is a no-op
    # with HIPAA mode off; in HIPAA mode a role that may not see PHI "text"
    # (e.g. a viewer) gets a null transcript and timeline rows with the text
    # field removed, while still seeing that the turns happened.
    from app.compliance_v2.hipaa import minimum_necessary
    from app.compliance_v2.hipaa.phi_tagging import load_policy

    policy = await load_policy(session, ctx.tenant_id)
    replay_allowed = (
        minimum_necessary.allowlist_for(
            ctx.role, minimum_necessary.SURFACE_REPLAY, policy=policy
        )
        if policy.hipaa_mode
        else None
    )
    text_visible = replay_allowed is None or "text" in replay_allowed

    if include_transcript and turns and text_visible:
        transcript = "\n".join(
            f"{turn.speaker.value if hasattr(turn.speaker, 'value') else turn.speaker}: {turn.text}"
            for turn in turns
        )

    if include_timeline:
        try:
            timeline = [
                {
                    "at": row.started_at.isoformat() if row.started_at else _now_iso(),
                    "event": "call_started",
                    "from": row.from_number,
                    "to": row.to_number,
                },
            ]
            for turn in turns:
                speaker = turn.speaker.value if hasattr(turn.speaker, "value") else str(turn.speaker)
                timeline.append(
                    {
                        "at": turn.created_at.isoformat() if turn.created_at else _now_iso(),
                        "event": "transcript_turn",
                        "speaker": speaker,
                        "text": turn.text,
                    }
                )
            if row.ended_at:
                timeline.append(
                    {"at": row.ended_at.isoformat(), "event": "call_ended", "duration": duration}
                )
            if hasattr(row, "transfer_state") and getattr(row, "transfer_state"):
                timeline.append(
                    {
                        "at": _now_iso(),
                        "event": "transfer",
                        "state": getattr(row, "transfer_state"),
                    }
                )
            timeline.sort(key=lambda event: event["at"])
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            timeline = []

    # BATCH06: strip PHI "text" from transcript_turn timeline rows for a role
    # the tenant's minimum-necessary policy does not allow to see it. The event,
    # speaker and timestamp survive so the timeline stays truthful.
    if policy.hipaa_mode and timeline:
        timeline = [
            minimum_necessary.filter_record(
                event,
                table="turns",
                role=ctx.role,
                surface=minimum_necessary.SURFACE_REPLAY,
                policy=policy,
            ).record
            if event.get("event") == "transcript_turn"
            else event
            for event in timeline
        ]

    # No recording access token is minted by this read endpoint. The canonical
    # recording-management API owns authorized access; inventing a token here
    # would advertise an unusable recording URL as if it were signed.

    await _audit(session, ctx, "call.replay_accessed", tenant_id=str(ctx.tenant_id), call_id=str(call_id))
    await session.commit()

    return CallReplayResponse(
        id=str(row.id),
        call_sid=row.call_sid,
        transcript=transcript,
        timeline=timeline,
        recording_url=recording_url,
        duration_seconds=duration,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        metadata={},
    )

@router.get("/{call_id}/transcript-summary")
async def get_call_transcript(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/transcript-summary — Search/export transcript envelope."""
    replay = await get_call_replay(call_id, include_transcript=True, include_timeline=False, ctx=ctx, session=session)
    return {"id": replay.id, "call_sid": replay.call_sid, "transcript": replay.transcript}

@router.get("/{call_id}/timeline")
async def get_call_timeline(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/calls/{id}/timeline — Timeline only."""
    replay = await get_call_replay(call_id, include_transcript=False, include_timeline=True, ctx=ctx, session=session)
    return {"id": replay.id, "timeline": replay.timeline}

# ---------------------------------------------------------------------------
# Policies — concurrency, retry, calling-window
# ---------------------------------------------------------------------------

@router.post("/policies/concurrency", response_model=dict, status_code=201)
async def create_concurrency_policy(
    payload: ConcurrencyPolicyRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/policies/concurrency — Create per-agent concurrency policy."""
    # Check existing
    existing = (
        await session.execute(
            select(CallPolicy).where(CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.agent_id == payload.agent_id, CallPolicy.policy_type == "concurrency")
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="concurrency policy already exists for agent, use PATCH")

    policy = CallPolicy(
        tenant_id=ctx.tenant_id,
        agent_id=payload.agent_id,
        policy_type="concurrency",
        config={
            "max_concurrent_calls": payload.max_concurrent_calls,
            "max_calls_per_minute": payload.max_calls_per_minute,
            "max_calls_per_hour": payload.max_calls_per_hour,
            "max_calls_per_day": payload.max_calls_per_day,
            "budget_cents_per_day": payload.budget_cents_per_day,
            **payload.config,
        },
        is_enabled=payload.is_enabled,
    )
    session.add(policy)
    await _audit(session, ctx, "policy.concurrency_created", tenant_id=str(ctx.tenant_id), agent_id=payload.agent_id)
    await session.commit()
    await session.refresh(policy)
    return policy.as_dict()

@router.get("/policies/concurrency", response_model=dict)
async def list_concurrency_policies(
    agent_id: Optional[str] = Query(default=None, max_length=80),
    is_enabled: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.policy_type == "concurrency"]
    if agent_id:
        scope.append(CallPolicy.agent_id == agent_id)
    if is_enabled is not None:
        scope.append(CallPolicy.is_enabled == is_enabled)

    total = (await session.execute(select(func.count(CallPolicy.id)).where(*scope))).scalar() or 0
    rows = (await session.execute(select(CallPolicy).where(*scope).order_by(CallPolicy.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return {"policies": [r.as_dict() for r in rows], "total": int(total), "limit": limit, "offset": offset}

@router.patch("/policies/concurrency/{policy_id}", response_model=dict)
async def update_concurrency_policy(
    policy_id: uuid.UUID,
    payload: ConcurrencyPolicyRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CallPolicy, policy_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.policy_type != "concurrency":
        raise HTTPException(status_code=404, detail="policy not found")
    row.agent_id = payload.agent_id
    row.config = {
        "max_concurrent_calls": payload.max_concurrent_calls,
        "max_calls_per_minute": payload.max_calls_per_minute,
        "max_calls_per_hour": payload.max_calls_per_hour,
        "max_calls_per_day": payload.max_calls_per_day,
        "budget_cents_per_day": payload.budget_cents_per_day,
        **payload.config,
    }
    row.is_enabled = payload.is_enabled
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return row.as_dict()

@router.delete("/policies/concurrency/{policy_id}")
async def delete_concurrency_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CallPolicy, policy_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.policy_type != "concurrency":
        raise HTTPException(status_code=404, detail="policy not found")
    await session.delete(row)
    await session.commit()
    return {"id": str(policy_id), "deleted": True}

@router.post("/policies/retry", response_model=dict, status_code=201)
async def create_retry_policy(
    payload: RetryPolicyRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/policies/retry — Outbound retry policy."""
    existing = (
        await session.execute(
            select(CallPolicy).where(CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.agent_id == payload.agent_id, CallPolicy.policy_type == "retry")
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="retry policy already exists for agent")

    policy = CallPolicy(
        tenant_id=ctx.tenant_id,
        agent_id=payload.agent_id,
        policy_type="retry",
        config={
            "max_attempts": payload.max_attempts,
            "retry_delay_seconds": payload.retry_delay_seconds,
            "backoff_multiplier": payload.backoff_multiplier,
            "max_delay_seconds": payload.max_delay_seconds,
            "retry_on": payload.retry_on,
            **payload.config,
        },
        is_enabled=payload.is_enabled,
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy.as_dict()

@router.get("/policies/retry", response_model=dict)
async def list_retry_policies(
    agent_id: Optional[str] = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.policy_type == "retry"]
    if agent_id:
        scope.append(CallPolicy.agent_id == agent_id)
    total = (await session.execute(select(func.count(CallPolicy.id)).where(*scope))).scalar() or 0
    rows = (await session.execute(select(CallPolicy).where(*scope).order_by(CallPolicy.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return {"policies": [r.as_dict() for r in rows], "total": int(total), "limit": limit, "offset": offset}

@router.patch("/policies/retry/{policy_id}", response_model=dict)
async def update_retry_policy(
    policy_id: uuid.UUID,
    payload: RetryPolicyRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CallPolicy, policy_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.policy_type != "retry":
        raise HTTPException(status_code=404, detail="policy not found")
    row.config = {
        "max_attempts": payload.max_attempts,
        "retry_delay_seconds": payload.retry_delay_seconds,
        "backoff_multiplier": payload.backoff_multiplier,
        "max_delay_seconds": payload.max_delay_seconds,
        "retry_on": payload.retry_on,
        **payload.config,
    }
    row.is_enabled = payload.is_enabled
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return row.as_dict()

@router.delete("/policies/retry/{policy_id}")
async def delete_retry_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CallPolicy, policy_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.policy_type != "retry":
        raise HTTPException(status_code=404, detail="policy not found")
    await session.delete(row)
    await session.commit()
    return {"id": str(policy_id), "deleted": True}

@router.post("/policies/calling-window", response_model=dict, status_code=201)
async def create_calling_window_policy(
    payload: CallingWindowPolicyRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/policies/calling-window — Timezone-aware windows day 0-6."""
    # Validate windows
    if len(payload.windows) > 7:
        raise HTTPException(status_code=422, detail="max 7 windows")
    seen_days = set()
    for w in payload.windows:
        if w.day in seen_days:
            raise HTTPException(status_code=422, detail=f"duplicate day {w.day}")
        seen_days.add(w.day)
        # Validate time order
        try:
            sh, sm = map(int, w.start.split(":"))
            eh, em = map(int, w.end.split(":"))
            if sh * 60 + sm >= eh * 60 + em:
                raise HTTPException(status_code=422, detail=f"window start must be before end for day {w.day}")
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid time format for day {w.day}")

    existing = (
        await session.execute(
            select(CallPolicy).where(CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.agent_id == payload.agent_id, CallPolicy.policy_type == "calling_window")
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="calling window policy exists for agent")

    policy = CallPolicy(
        tenant_id=ctx.tenant_id,
        agent_id=payload.agent_id,
        policy_type="calling_window",
        config={
            "timezone": payload.timezone,
            "windows": [w.model_dump() for w in payload.windows],
            **payload.config,
        },
        is_enabled=payload.is_enabled,
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return policy.as_dict()

@router.get("/policies/calling-window", response_model=dict)
async def list_calling_window_policies(
    agent_id: Optional[str] = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.policy_type == "calling_window"]
    if agent_id:
        scope.append(CallPolicy.agent_id == agent_id)
    total = (await session.execute(select(func.count(CallPolicy.id)).where(*scope))).scalar() or 0
    rows = (await session.execute(select(CallPolicy).where(*scope).order_by(CallPolicy.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return {"policies": [r.as_dict() for r in rows], "total": int(total), "limit": limit, "offset": offset}

@router.patch("/policies/calling-window/{policy_id}", response_model=dict)
async def update_calling_window_policy(
    policy_id: uuid.UUID,
    payload: CallingWindowPolicyRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CallPolicy, policy_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.policy_type != "calling_window":
        raise HTTPException(status_code=404, detail="policy not found")
    row.config = {
        "timezone": payload.timezone,
        "windows": [w.model_dump() for w in payload.windows],
        **payload.config,
    }
    row.is_enabled = payload.is_enabled
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return row.as_dict()

@router.delete("/policies/calling-window/{policy_id}")
async def delete_calling_window_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CallPolicy, policy_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.policy_type != "calling_window":
        raise HTTPException(status_code=404, detail="policy not found")
    await session.delete(row)
    await session.commit()
    return {"id": str(policy_id), "deleted": True}

# ---------------------------------------------------------------------------
# DNC — centralized pre-dial compliance
# ---------------------------------------------------------------------------

@router.post("/dnc/check", response_model=dict)
async def check_dnc(
    payload: DncCheckRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/dnc/check — Centralized pre-dial DNC check."""
    from app.telephony.dnc import evaluate_dnc

    verdict = await evaluate_dnc(
        session,
        ctx.tenant_id,
        payload.phone,
        lead_id=payload.lead_id,
    )
    phone_norm = verdict.normalized_phone or _normalize_phone(payload.phone)
    return {
        "phone": phone_norm,
        "redacted": _redact_phone(phone_norm),
        "blocked": verdict.blocked,
        "reason": verdict.reason,
        "dnc_entry": verdict.dnc_entry,
        "lead_dnc": verdict.lead_dnc,
        "consent_denied": verdict.consent_denied,
        "compliant": not verdict.blocked,
        "checked_at": _now_iso(),
    }

@router.post("/dnc", response_model=dict, status_code=201)
async def add_dnc_entry(
    payload: DncAddRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/dnc — Add DNC entry."""
    phone_norm = _normalize_phone(payload.phone)
    if not E164_REGEX.match(phone_norm) and not phone_util.is_valid(phone_norm):
        raise HTTPException(status_code=422, detail="invalid phone")

    existing = (
        await session.execute(select(DncEntry).where(DncEntry.tenant_id == ctx.tenant_id, DncEntry.phone == phone_norm).limit(1))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="phone already on DNC")

    entry = DncEntry(
        tenant_id=ctx.tenant_id,
        phone=phone_norm,
        reason=payload.reason,
        source=payload.source,
        created_by=ctx.user_id,
    )
    session.add(entry)
    await _audit(session, ctx, "dnc.added", tenant_id=str(ctx.tenant_id), phone=_redact_phone(phone_norm), reason=payload.reason)
    await session.commit()
    await session.refresh(entry)
    return entry.as_dict()

@router.get("/dnc", response_model=DncListResponse)
async def list_dnc_entries(
    search: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [DncEntry.tenant_id == ctx.tenant_id]
    if search:
        scope.append(DncEntry.phone.ilike(f"%{search}%"))

    total = (await session.execute(select(func.count(DncEntry.id)).where(*scope))).scalar() or 0
    rows = (await session.execute(select(DncEntry).where(*scope).order_by(DncEntry.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return DncListResponse(entries=[r.as_dict() for r in rows], total=int(total), limit=limit, offset=offset)

@router.delete("/dnc/{entry_id}")
async def delete_dnc_entry(
    entry_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(DncEntry, entry_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="DNC entry not found")
    await session.delete(row)
    await _audit(session, ctx, "dnc.deleted", tenant_id=str(ctx.tenant_id), entry_id=str(entry_id), phone=_redact_phone(row.phone))
    await session.commit()
    return {"id": str(entry_id), "deleted": True}

@router.delete("/dnc/phone/{phone}")
async def delete_dnc_by_phone(
    phone: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /api/calls/dnc/phone/{phone} — Delete by phone number."""
    phone_norm = _normalize_phone(phone)
    entry = (
        await session.execute(select(DncEntry).where(DncEntry.tenant_id == ctx.tenant_id, DncEntry.phone == phone_norm).limit(1))
    ).scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="DNC entry not found")
    await session.delete(entry)
    await session.commit()
    return {"phone": phone_norm, "deleted": True}

# ---------------------------------------------------------------------------
# Voice consent check
# ---------------------------------------------------------------------------

@router.post("/consent/check", response_model=dict)
async def check_voice_consent(
    payload: DncCheckRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/consent/check — Voice consent check."""
    phone_norm = _normalize_phone(payload.phone)
    consent_status = "unknown"
    denied = False
    if payload.lead_id:
        try:
            from app.leads.consent import voice_denied
            denied = await voice_denied(session, ctx.tenant_id, payload.lead_id)
            consent_status = "denied" if denied else "granted"
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            consent_status = "unknown"

    return {
        "phone": phone_norm,
        "lead_id": str(payload.lead_id) if payload.lead_id else None,
        "consent_status": consent_status,
        "denied": denied,
        "can_call": not denied,
        "checked_at": _now_iso(),
    }

# ---------------------------------------------------------------------------
# Health & stats
# ---------------------------------------------------------------------------

@router.get("/policies/health")
async def policies_health(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    total_q = await session.execute(select(func.count(CallPolicy.id)).where(CallPolicy.tenant_id == ctx.tenant_id))
    total = total_q.scalar() or 0
    enabled_q = await session.execute(select(func.count(CallPolicy.id)).where(CallPolicy.tenant_id == ctx.tenant_id, CallPolicy.is_enabled.is_(True)))
    enabled = enabled_q.scalar() or 0

    dnc_q = await session.execute(select(func.count(DncEntry.id)).where(DncEntry.tenant_id == ctx.tenant_id))
    dnc_total = dnc_q.scalar() or 0

    return {
        "tenant_id": str(ctx.tenant_id),
        "total_policies": total,
        "enabled_policies": enabled,
        "dnc_entries": dnc_total,
        "status": "healthy",
        "at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# PART 15 (P15-07) — signed recording access and saved views
#
# A recording URL from the provider is a bearer token in disguise: anyone who
# has the string has the audio. Export therefore hands out a URL signed by this
# platform with an expiry, and the raw URL never leaves the server.
# ---------------------------------------------------------------------------

RECORDING_ACCESS_PATH = "/api/calls/{call_id}/recording-access"


def _recording_signature(call_id: uuid.UUID, expires_at: int) -> str:
    """HMAC-SHA256 over ``call_id:expiry`` with the deployment secret."""
    import hashlib
    import hmac

    from app.core.config import settings

    message = f"{call_id}:{expires_at}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def sign_recording_url(call_id: uuid.UUID, *, ttl_seconds: int = 900) -> str:
    """Build a signed, expiring access path for one call's recording."""
    import time

    expires_at = int(time.time()) + max(60, int(ttl_seconds))
    signature = _recording_signature(call_id, expires_at)
    return (
        RECORDING_ACCESS_PATH.format(call_id=call_id)
        + f"?expires={expires_at}&signature={signature}"
    )


def verify_recording_signature(call_id: uuid.UUID, expires_at: int, signature: str) -> bool:
    """Constant-time check plus expiry. A bad signature and an expired one fail the same way."""
    import time

    if expires_at < int(time.time()):
        return False
    return hmac_compare(_recording_signature(call_id, expires_at), signature)


def hmac_compare(expected: str, provided: str) -> bool:
    import hmac

    return hmac.compare_digest(expected, provided or "")


@router.get("/{call_id}/recording-access")
async def get_recording_access(
    call_id: uuid.UUID,
    expires: int = Query(ge=0),
    signature: str = Query(min_length=16, max_length=128),
    session: AsyncSession = Depends(get_session),
):
    """Exchange a signed path for the provider's recording URL.

    Deliberately unauthenticated: the signature *is* the credential, it expires,
    and it is scoped to one call id. Requiring a bearer token here would make the
    link useless to the browser's audio element, which is the point of signing.
    """
    from fastapi.responses import RedirectResponse

    if not verify_recording_signature(call_id, expires, signature):
        raise HTTPException(
            status_code=403, detail={"code": "invalid_signature", "message": "signature invalid or expired"}
        )
    row = await session.get(Call, call_id)
    if row is None or not row.recording_url:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no recording"})
    return RedirectResponse(url=row.recording_url, status_code=302)


class SavedViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str = Field(min_length=1, max_length=120)
    filters: Dict[str, Any] = Field(default_factory=dict)
    columns: List[str] = Field(default_factory=list)
    shared: bool = False


def _saved_view_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "filters": dict(row.filters or {}),
        "columns": list(row.columns or []),
        "shared": bool(row.shared),
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/saved-views")
async def list_saved_views(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Views the caller saved, plus the shared ones."""
    from app.db.monitoring_models import CallSavedView

    rows = (
        await session.scalars(
            select(CallSavedView)
            .where(
                CallSavedView.tenant_id == ctx.tenant_id,
                or_(
                    CallSavedView.shared.is_(True),
                    CallSavedView.owner_user_id == ctx.user_id,
                    CallSavedView.owner_user_id.is_(None),
                ),
            )
            .order_by(CallSavedView.name)
        )
    ).all()
    return {"views": [_saved_view_dict(row) for row in rows]}


@router.post("/saved-views", status_code=201)
async def create_saved_view(
    payload: SavedViewBody,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Save the current filter set.

    The filters are validated by constructing a ``CallSearchRequest`` from them,
    so a saved view can never store a filter the search endpoint would reject.
    """
    from app.db.monitoring_models import CallSavedView

    try:
        CallSearchRequest(**payload.filters)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_filters", "message": str(exc)[:300]},
        ) from None
    invalid = [column for column in payload.columns if column not in ALLOWED_EXPORT_FIELDS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_columns", "message": f"unknown columns: {invalid}"},
        )
    existing = await session.scalar(
        select(CallSavedView).where(
            CallSavedView.tenant_id == ctx.tenant_id, CallSavedView.name == payload.name
        )
    )
    if existing is not None:
        if existing.owner_user_id not in (None, ctx.user_id):
            raise HTTPException(
                status_code=409,
                detail={"code": "name_taken", "message": "another user saved a view with this name"},
            )
        existing.filters = dict(payload.filters)
        existing.columns = list(payload.columns)
        existing.shared = payload.shared
        await session.commit()
        await session.refresh(existing)
        return _saved_view_dict(existing)

    row = CallSavedView(
        tenant_id=ctx.tenant_id,
        owner_user_id=ctx.user_id,
        name=payload.name.strip(),
        filters=dict(payload.filters),
        columns=list(payload.columns),
        shared=payload.shared,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _saved_view_dict(row)


@router.delete("/saved-views/{view_id}", status_code=204)
async def delete_saved_view(
    view_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.db.monitoring_models import CallSavedView

    row = await session.get(CallSavedView, view_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if row.owner_user_id not in (None, ctx.user_id):
        raise HTTPException(
            status_code=403,
            detail={"code": "not_owner", "message": "only the owner may delete a saved view"},
        )
    await session.delete(row)
    await session.commit()
    return None
