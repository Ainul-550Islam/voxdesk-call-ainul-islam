# File: app/api/live_monitoring_routes.py — Live monitoring (listen, whisper_ai, takeover), durable sessions with heartbeat/expiry, RBAC (`calls.monitor`), idempotency & audit
"""
Live monitoring and human takeover API (Part 4 / Gate G5).
- Live monitoring modes: listen, whisper_ai, takeover
- Operator session lifecycle: start, heartbeat, expiry, pause, resume, end, leave, audit
- Whisper-to-AI guidance injection via MonitorTap / MonitorBus (`LLMMessagesAppendFrame`)
- Supervisor takeover via provider TwiML replacement (`app.telephony.takeover.takeover`)
- Distributed rate limiting, durable idempotency (1E), RBAC (`calls.monitor`), tenant isolation
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws.monitor_ws import DEFAULT_WS_TOKEN_TTL_SECONDS, issue_monitor_ws_token
from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, get_context
from app.auth.permissions import Permission
from app.auth.service import record_audit
from app.core.logging import log
from app.core.rate_limit import (
    allow_authenticated_request,
    client_ip,
    enforce_tenant_rate_limit,
)
from app.db.enterprise_models import LiveCallSession
from app.db.models import AuditAction, Call, CallStatus, RequestIdempotencyReceipt, UserRole
from app.db.session import get_session
from app.resilience.idempotency import get_idempotent_resource_id, store_idempotent_resource_id
from app.telephony.monitor_bus import get_monitor_bus
from app.telephony.provider_errors import UnsupportedCapability
from app.telephony.takeover import (
    is_takeover_pending,
    rollback_to_ai,
    takeover as execute_takeover,
)
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(prefix="/api/calls", tags=["live-monitoring"])

MAX_WHISPER_LENGTH = 1000
MAX_REASON_LENGTH = 500
MAX_META_SIZE = 5000
MONITOR_MODES = {"listen", "whisper_ai", "takeover"}
OWNERSHIP_TYPES = {"operator", "shared", "agent"}
SESSION_STATUSES = {"active", "ended", "paused", "expired"}
DEFAULT_SESSION_TTL_MINUTES = 60
MAX_CONCURRENT_SESSIONS_PER_CALL = 5
MAX_SESSIONS_PER_SUPERVISOR = 20
CALLS_MONITOR_PERMISSION = "calls.monitor"


def _has_monitor_access(ctx: TenantContext, *, write: bool) -> bool:
    """Verify whether ``ctx`` holds ``calls.monitor`` (or supervisor RBAC permission)."""
    if ctx.membership_status in ("revoked", "expired"):
        return False
    if ctx.membership_status == "suspended" and write:
        return False

    required_perm = Permission.SUPERVISOR_WRITE if write else Permission.SUPERVISOR_READ
    role_ok = ctx.role in (
        {UserRole.OWNER, UserRole.ADMIN}
        if write
        else {UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER}
    )
    if not role_ok:
        return False

    if ctx.scopes is None:
        return True
    allowed_scopes = {
        CALLS_MONITOR_PERMISSION,
        required_perm.value,
        Permission.SUPERVISOR_WRITE.value,
    }
    return bool(set(ctx.scopes) & allowed_scopes)


def require_calls_monitor(
    *, write: bool = True
) -> Callable[..., Coroutine[Any, Any, TenantContext]]:
    """Enforce ``calls.monitor`` permission per tenant and audit authorization denials."""

    async def _dependency(
        request: Request,
        ctx: TenantContext = Depends(get_context),
        session: AsyncSession = Depends(get_session),
    ) -> TenantContext:
        principal_id = str(ctx.api_key_id or ctx.service_account_id or ctx.user_id)
        if not await allow_authenticated_request(
            request,
            tenant_id=str(ctx.tenant_id),
            principal_id=principal_id,
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "rate_limited", "message": "Too many requests"},
                headers={"Retry-After": "60"},
            )

        if not _has_monitor_access(ctx, write=write):
            await record_audit(
                session,
                action=AuditAction.AUTHZ_DENIED,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                actor_email=ctx.user.email,
                ip_address=client_ip(request)[:64],
                detail={
                    "missing": [CALLS_MONITOR_PERMISSION],
                    "path": request.url.path,
                    "auth_method": ctx.auth_method,
                    "principal": ctx.describe_actor(),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires permission: {CALLS_MONITOR_PERMISSION}",
            )
        return ctx

    return _dependency


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class MonitorRequest(_Strict):
    mode: str = Field(
        default="listen",
        description="listen=audio+transcript fan-out, whisper_ai=inject guidance into AI context, takeover=bridge supervisor",
    )
    whisper_text: Optional[str] = Field(default=None, max_length=MAX_WHISPER_LENGTH)
    reason: Optional[str] = Field(default=None, max_length=MAX_REASON_LENGTH)
    supervisor_destination: Optional[str] = Field(default=None, max_length=160)
    webrtc_client: Optional[str] = Field(default=None, max_length=120)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ttl_minutes: int = Field(default=DEFAULT_SESSION_TTL_MINUTES, ge=1, le=240)

    @field_validator("mode")
    @classmethod
    def _normalize_mode(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned == "whisper":
            return "whisper_ai"
        if cleaned not in MONITOR_MODES:
            raise ValueError(f"mode must be one of {sorted(MONITOR_MODES)}")
        return cleaned


class LiveSessionOut(_Strict):
    id: str
    call_id: str
    supervisor_id: str
    mode: str
    status: str
    created_at: str
    updated_at: Optional[str] = None
    ended_at: Optional[str] = None
    expires_at: Optional[str] = None
    ws_url: Optional[str] = None
    ws_token: Optional[str] = None
    meta: Dict[str, Any]
    duration_seconds: Optional[int] = None


class LiveSessionListOut(_Strict):
    sessions: List[LiveSessionOut]
    total: int
    active: int
    limit: int
    offset: int


class TakeoverRequest(_Strict):
    reason: Optional[str] = Field(default=None, max_length=MAX_REASON_LENGTH)
    supervisor_destination: Optional[str] = Field(default=None, max_length=160)
    webrtc_client: Optional[str] = Field(default=None, max_length=120)
    conference_name: Optional[str] = Field(default=None, max_length=120)
    whisper_text: Optional[str] = Field(default=None, max_length=MAX_WHISPER_LENGTH)
    ownership: str = Field(default="operator", pattern="^(operator|shared|agent)$")
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    notify_customer: bool = Field(default=False)


class TakeoverOut(_Strict):
    id: str
    call_id: str
    owner: str
    status: str
    joined_at: str
    left_at: Optional[str] = None
    audit: Dict[str, Any]
    duration_seconds: Optional[int] = None


class WhisperRequest(_Strict):
    text: str = Field(min_length=1, max_length=MAX_WHISPER_LENGTH)
    target: str = Field(default="agent", pattern="^(agent|customer|both)$")
    priority: int = Field(default=0, ge=0, le=10)
    run_llm: bool = Field(default=True)


class MonitorAnalyticsOut(_Strict):
    call_id: str
    total_sessions: int
    active_sessions: int
    takeover_sessions: int
    average_duration_seconds: Optional[float] = None
    modes: Dict[str, int] = Field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(val: Any) -> Optional[datetime]:
    if not val or not isinstance(val, str):
        return None
    try:
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_session_expired(sess: LiveCallSession, now: Optional[datetime] = None) -> bool:
    if sess.status != "active":
        return False
    ref_now = now or _now()
    meta = sess.meta if isinstance(sess.meta, dict) else {}
    exp_dt = _parse_iso(meta.get("expires_at"))
    if exp_dt is not None and ref_now >= exp_dt:
        return True
    ttl_minutes = int(meta.get("ttl_minutes") or DEFAULT_SESSION_TTL_MINUTES)
    created = sess.created_at
    if created is not None:
        created_utc = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        if ref_now >= created_utc + timedelta(minutes=ttl_minutes):
            return True
    return False


async def _expire_stale_sessions(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    call_id: Optional[uuid.UUID] = None,
) -> int:
    filters = [
        LiveCallSession.tenant_id == tenant_id,
        LiveCallSession.status == "active",
    ]
    if call_id is not None:
        filters.append(LiveCallSession.call_id == call_id)
    rows = (await session.execute(select(LiveCallSession).where(*filters))).scalars().all()
    now = _now()
    expired_count = 0
    bus = get_monitor_bus()
    for row in rows:
        if _is_session_expired(row, now):
            row.status = "expired"
            row.ended_at = now
            meta = dict(row.meta or {})
            audit = list(meta.get("audit") or [])
            audit.append({"event": "session_expired", "at": now.isoformat()})
            meta["audit"] = audit
            row.meta = meta
            await bus.unregister_session(row.call_id, row.id)
            expired_count += 1
    if expired_count > 0:
        await session.flush()
    return expired_count


def _session_duration(sess: LiveCallSession) -> Optional[int]:
    if sess.created_at and sess.ended_at:
        created = sess.created_at if sess.created_at.tzinfo else sess.created_at.replace(tzinfo=timezone.utc)
        ended = sess.ended_at if sess.ended_at.tzinfo else sess.ended_at.replace(tzinfo=timezone.utc)
        return max(0, int((ended - created).total_seconds()))
    if sess.created_at:
        created = sess.created_at if sess.created_at.tzinfo else sess.created_at.replace(tzinfo=timezone.utc)
        return max(0, int((_now() - created).total_seconds()))
    return None


def _to_session_out(
    row: LiveCallSession,
    *,
    ws_token: Optional[str] = None,
    ws_url: Optional[str] = None,
) -> LiveSessionOut:
    meta = dict(row.meta or {})
    return LiveSessionOut(
        id=str(row.id),
        call_id=str(row.call_id),
        supervisor_id=str(row.supervisor_id),
        mode=row.mode,
        status=row.status,
        created_at=row.created_at.isoformat() if row.created_at else _now_iso(),
        updated_at=meta.get("last_heartbeat_at"),
        ended_at=row.ended_at.isoformat() if row.ended_at else None,
        expires_at=meta.get("expires_at"),
        ws_url=ws_url or meta.get("ws_url"),
        ws_token=ws_token,
        meta=meta,
        duration_seconds=_session_duration(row),
    )


def _to_takeover_out(row: LiveCallSession) -> TakeoverOut:
    return TakeoverOut(
        id=str(row.id),
        call_id=str(row.call_id),
        owner=row.meta.get("ownership", "operator") if isinstance(row.meta, dict) else "operator",
        status=row.status,
        joined_at=row.created_at.isoformat() if row.created_at else _now_iso(),
        left_at=row.ended_at.isoformat() if row.ended_at else None,
        audit=row.meta if isinstance(row.meta, dict) else {},
        duration_seconds=_session_duration(row),
    )


async def _get_call(session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID) -> Call:
    row = await session.get(Call, call_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="call not found")
    return row


async def _get_session_row(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID, session_id: uuid.UUID
) -> LiveCallSession:
    row = await session.get(LiveCallSession, session_id)
    if row is None or row.tenant_id != tenant_id or row.call_id != call_id:
        raise HTTPException(status_code=404, detail="monitoring session not found")
    if _is_session_expired(row):
        row.status = "expired"
        row.ended_at = _now()
        await get_monitor_bus().unregister_session(call_id, session_id)
        await session.flush()
    return row


@router.post("/{call_id}/monitor", response_model=LiveSessionOut, status_code=201)
async def start_monitoring(
    call_id: uuid.UUID,
    payload: MonitorRequest,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/calls/{id}/monitor — Start a live monitoring session (`listen`, `whisper_ai`, `takeover`)."""
    try:
        await enforce_tenant_rate_limit(ctx.tenant_id, "monitor_start", 20)
        call = await _get_call(session, ctx.tenant_id, call_id)
        if (
            call.status in (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELLED, CallStatus.NO_ANSWER)
            and not is_takeover_pending(call)
        ):
            raise HTTPException(status_code=409, detail="call not active for monitoring")

        await _expire_stale_sessions(session, ctx.tenant_id, call_id)

        idem_key = payload.idempotency_key or x_idempotency_key
        if idem_key:
            cached_id = await get_idempotent_resource_id(
                session,
                tenant_id=ctx.tenant_id,
                operation="live_monitor.start",
                key=idem_key,
            )
            if cached_id:
                existing_idem = await session.get(LiveCallSession, uuid.UUID(cached_id))
                if (
                    existing_idem is not None
                    and existing_idem.tenant_id == ctx.tenant_id
                    and existing_idem.call_id == call_id
                ):
                    token = issue_monitor_ws_token(
                        call_id=call_id,
                        tenant_id=ctx.tenant_id,
                        supervisor_id=ctx.user_id,
                        session_id=existing_idem.id,
                        mode=existing_idem.mode,
                        ttl_seconds=min(payload.ttl_minutes * 60, DEFAULT_WS_TOKEN_TTL_SECONDS),
                    )
                    return _to_session_out(
                        existing_idem,
                        ws_token=token,
                        ws_url=f"/ws/monitor/{call_id}?token={token}",
                    )

        active_count_q = await session.execute(
            select(func.count(LiveCallSession.id)).where(
                LiveCallSession.tenant_id == ctx.tenant_id,
                LiveCallSession.call_id == call_id,
                LiveCallSession.status == "active",
            )
        )
        active_count = active_count_q.scalar() or 0
        if active_count >= MAX_CONCURRENT_SESSIONS_PER_CALL:
            raise HTTPException(
                status_code=409,
                detail=f"max {MAX_CONCURRENT_SESSIONS_PER_CALL} concurrent monitoring sessions per call",
            )

        sup_count_q = await session.execute(
            select(func.count(LiveCallSession.id)).where(
                LiveCallSession.tenant_id == ctx.tenant_id,
                LiveCallSession.supervisor_id == ctx.user_id,
                LiveCallSession.status == "active",
            )
        )
        sup_count = sup_count_q.scalar() or 0
        if sup_count >= MAX_SESSIONS_PER_SUPERVISOR:
            raise HTTPException(
                status_code=409,
                detail=f"max {MAX_SESSIONS_PER_SUPERVISOR} active sessions per supervisor",
            )

        existing = (
            await session.execute(
                select(LiveCallSession).where(
                    LiveCallSession.tenant_id == ctx.tenant_id,
                    LiveCallSession.call_id == call_id,
                    LiveCallSession.supervisor_id == ctx.user_id,
                    LiveCallSession.status == "active",
                    LiveCallSession.mode == payload.mode,
                )
            )
        ).scalars().first()
        if existing:
            token = issue_monitor_ws_token(
                call_id=call_id,
                tenant_id=ctx.tenant_id,
                supervisor_id=ctx.user_id,
                session_id=existing.id,
                mode=existing.mode,
                ttl_seconds=min(payload.ttl_minutes * 60, DEFAULT_WS_TOKEN_TTL_SECONDS),
            )
            return _to_session_out(
                existing,
                ws_token=token,
                ws_url=f"/ws/monitor/{call_id}?token={token}",
            )

        now = _now()
        expires_at = now + timedelta(minutes=payload.ttl_minutes)
        session_uuid = uuid.uuid4()
        ws_path = f"/ws/monitor/{call_id}"
        ws_token = issue_monitor_ws_token(
            call_id=call_id,
            tenant_id=ctx.tenant_id,
            supervisor_id=ctx.user_id,
            session_id=session_uuid,
            mode=payload.mode,
            ttl_seconds=min(payload.ttl_minutes * 60, DEFAULT_WS_TOKEN_TTL_SECONDS),
        )

        bus = get_monitor_bus()
        takeover_meta: dict[str, Any] = {}
        if payload.mode == "takeover":
            destination = (
                payload.supervisor_destination
                or str(payload.metadata.get("supervisor_destination") or "")
                or getattr(ctx.tenant, "escalation_number", None)
            )
            webrtc_client = payload.webrtc_client or str(payload.metadata.get("webrtc_client") or "")
            if not destination and not webrtc_client:
                raise HTTPException(
                    status_code=422,
                    detail="supervisor_destination or webrtc_client is required for takeover mode",
                )
            try:
                tk_res = await execute_takeover(
                    call,
                    destination,
                    webrtc_client=webrtc_client or None,
                    session=session,
                    tenant=ctx.tenant,
                    whisper_text=payload.whisper_text,
                    reason=payload.reason or "supervisor_takeover",
                )
            except UnsupportedCapability as exc:
                raise HTTPException(status_code=501, detail=exc.as_dict()) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not tk_res.ok:
                await session.commit()
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "takeover_failed",
                        "message": "Supervisor takeover failed; rolled back to AI agent.",
                        "rolled_back_to_ai": tk_res.rolled_back_to_ai,
                    },
                )
            takeover_meta = tk_res.as_dict()

        await bus.register_session(
            call_id,
            session_uuid,
            supervisor_id=ctx.user_id,
            mode=payload.mode,
            ttl_seconds=payload.ttl_minutes * 60,
        )

        guidance_delivered = False
        if payload.mode == "whisper_ai" and payload.whisper_text:
            guidance_delivered = await bus.publish_guidance(
                call_id,
                payload.whisper_text,
                supervisor_id=ctx.user_id,
                session_id=session_uuid,
                run_llm=True,
            )

        sess = LiveCallSession(
            id=session_uuid,
            tenant_id=ctx.tenant_id,
            call_id=call_id,
            supervisor_id=ctx.user_id,
            mode=payload.mode,
            status="active",
            created_at=now,
            meta={
                "reason": payload.reason,
                "whisper_text": payload.whisper_text,
                "started_by": str(ctx.user_id),
                "ttl_minutes": payload.ttl_minutes,
                "expires_at": expires_at.isoformat(),
                "last_heartbeat_at": now.isoformat(),
                "ws_url": ws_path,
                "metadata": payload.metadata,
                "media_connected": True,
                "media_status": "BRIDGED" if payload.mode == "takeover" else "STREAM_READY",
                "guidance_delivered": guidance_delivered,
                **({"takeover": takeover_meta} if takeover_meta else {}),
                "audit": [
                    {
                        "event": "session_started",
                        "by": str(ctx.user_id),
                        "at": now.isoformat(),
                        "mode": payload.mode,
                    }
                ],
            },
        )
        session.add(sess)
        await session.flush()

        await record_enterprise_audit(
            session,
            ctx.tenant_id,
            ctx.user_id,
            "live_monitor.started",
            {
                "operation": f"live_monitor_{payload.mode}",
                "mode": payload.mode,
                "call_id": str(call_id),
                "session_id": str(sess.id),
            },
            resource_type="live_call_session",
            resource_id=sess.id,
        )

        if idem_key:
            await store_idempotent_resource_id(
                session,
                tenant_id=ctx.tenant_id,
                operation="live_monitor.start",
                key=idem_key,
                resource_type="live_call_session",
                resource_id=sess.id,
                request_data={"call_id": str(call_id), "mode": payload.mode},
            )

        await session.commit()
        await session.refresh(sess)

        log.info(
            "live_monitor.started",
            tenant_id=str(ctx.tenant_id),
            call_id=str(call_id),
            session_id=str(sess.id),
            mode=payload.mode,
            supervisor_id=str(ctx.user_id),
        )
        return _to_session_out(
            sess,
            ws_token=ws_token,
            ws_url=f"{ws_path}?token={ws_token}",
        )
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{call_id}/monitor", response_model=LiveSessionListOut)
async def list_monitoring_sessions(
    call_id: uuid.UUID,
    status: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    """List active and recent monitoring sessions for a call."""
    await _get_call(session, ctx.tenant_id, call_id)
    await _expire_stale_sessions(session, ctx.tenant_id, call_id)
    filters = [LiveCallSession.tenant_id == ctx.tenant_id, LiveCallSession.call_id == call_id]
    if status:
        filters.append(LiveCallSession.status == status)
    if mode:
        norm_mode = "whisper_ai" if mode == "whisper" else mode
        filters.append(LiveCallSession.mode == norm_mode)

    total_q = await session.execute(select(func.count(LiveCallSession.id)).where(*filters))
    total = total_q.scalar() or 0

    active_q = await session.execute(
        select(func.count(LiveCallSession.id)).where(*filters, LiveCallSession.status == "active")
    )
    active = active_q.scalar() or 0

    rows = (
        await session.execute(
            select(LiveCallSession)
            .where(*filters)
            .order_by(LiveCallSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    await session.commit()

    return LiveSessionListOut(
        sessions=[_to_session_out(r) for r in rows],
        total=int(total),
        active=int(active),
        limit=limit,
        offset=offset,
    )


@router.get("/{call_id}/monitor/{session_id}", response_model=LiveSessionOut)
async def get_monitoring_session(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    await session.commit()
    return _to_session_out(row)


@router.post("/{call_id}/monitor/{session_id}/heartbeat", response_model=LiveSessionOut)
async def heartbeat_monitoring_session(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """Refresh heartbeat and extend TTL on an active monitoring session."""
    await _get_call(session, ctx.tenant_id, call_id)
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    if row.status != "active":
        raise HTTPException(status_code=409, detail=f"session not active (status={row.status})")
    if row.supervisor_id != ctx.user_id and ctx.role not in {UserRole.OWNER, UserRole.ADMIN}:
        raise HTTPException(status_code=403, detail="only session owner or admin can heartbeat")

    now = _now()
    meta = dict(row.meta or {})
    ttl_minutes = int(meta.get("ttl_minutes") or DEFAULT_SESSION_TTL_MINUTES)
    expires_at = now + timedelta(minutes=ttl_minutes)
    meta["last_heartbeat_at"] = now.isoformat()
    meta["expires_at"] = expires_at.isoformat()
    row.meta = meta

    await get_monitor_bus().heartbeat_session(
        call_id,
        session_id,
        ttl_seconds=ttl_minutes * 60,
    )
    await session.commit()
    await session.refresh(row)
    return _to_session_out(row)


@router.post("/{call_id}/monitor/{session_id}/end", response_model=LiveSessionOut)
async def end_monitoring(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """End monitoring session."""
    await _get_call(session, ctx.tenant_id, call_id)
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    if row.status != "active":
        raise HTTPException(status_code=409, detail="session not active")
    if row.supervisor_id != ctx.user_id and ctx.role not in {UserRole.OWNER, UserRole.ADMIN}:
        raise HTTPException(status_code=403, detail="only session owner or admin can end")

    row.status = "ended"
    row.ended_at = _now()
    meta = dict(row.meta or {})
    meta["media_connected"] = False
    meta["media_status"] = "ENDED"
    audit = meta.get("audit", [])
    if isinstance(audit, list):
        audit.append({"event": "session_ended", "by": str(ctx.user_id), "at": _now_iso()})
        meta["audit"] = audit
    row.meta = meta

    await get_monitor_bus().unregister_session(call_id, session_id)

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "live_monitor.ended",
        {"call_id": str(call_id), "session_id": str(session_id)},
        resource_type="live_call_session",
        resource_id=session_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_session_out(row)


@router.post("/{call_id}/monitor/{session_id}/pause", response_model=LiveSessionOut)
async def pause_monitoring(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """Pause monitoring session."""
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    if row.status != "active":
        raise HTTPException(status_code=409, detail="session not active")
    row.status = "paused"
    meta = dict(row.meta or {})
    audit = meta.get("audit", [])
    if isinstance(audit, list):
        audit.append({"event": "session_paused", "by": str(ctx.user_id), "at": _now_iso()})
        meta["audit"] = audit
    row.meta = meta
    await get_monitor_bus().unregister_session(call_id, session_id)
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "live_monitor.paused",
        {"call_id": str(call_id), "session_id": str(session_id)},
        resource_type="live_call_session",
        resource_id=session_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_session_out(row)


@router.post("/{call_id}/monitor/{session_id}/resume", response_model=LiveSessionOut)
async def resume_monitoring(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """Resume paused monitoring session."""
    row = await session.get(LiveCallSession, session_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.call_id != call_id:
        raise HTTPException(status_code=404, detail="monitoring session not found")
    if row.status != "paused":
        raise HTTPException(status_code=409, detail="session not paused")
    row.status = "active"
    now = _now()
    meta = dict(row.meta or {})
    ttl_minutes = int(meta.get("ttl_minutes") or DEFAULT_SESSION_TTL_MINUTES)
    meta["last_heartbeat_at"] = now.isoformat()
    meta["expires_at"] = (now + timedelta(minutes=ttl_minutes)).isoformat()
    audit = meta.get("audit", [])
    if isinstance(audit, list):
        audit.append({"event": "session_resumed", "by": str(ctx.user_id), "at": now.isoformat()})
        meta["audit"] = audit
    row.meta = meta
    await get_monitor_bus().register_session(
        call_id,
        session_id,
        supervisor_id=row.supervisor_id,
        mode=row.mode,
        ttl_seconds=ttl_minutes * 60,
    )
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "live_monitor.resumed",
        {"call_id": str(call_id), "session_id": str(session_id)},
        resource_type="live_call_session",
        resource_id=session_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_session_out(row)


@router.post("/{call_id}/monitor/{session_id}/whisper")
async def whisper_to_agent(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: WhisperRequest,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """Inject whisper-to-AI guidance into the live agent context (`LLMMessagesAppendFrame`)."""
    await enforce_tenant_rate_limit(ctx.tenant_id, "monitor_whisper", 30)
    await _get_call(session, ctx.tenant_id, call_id)
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    if row.status != "active":
        raise HTTPException(status_code=409, detail="session not active")
    if row.mode not in ("whisper_ai", "whisper", "takeover"):
        raise HTTPException(status_code=422, detail="session mode does not support whisper")

    delivered = await get_monitor_bus().publish_guidance(
        call_id,
        payload.text,
        supervisor_id=ctx.user_id,
        session_id=session_id,
        run_llm=payload.run_llm,
    )

    now_iso = _now_iso()
    meta = dict(row.meta or {})
    whispers = meta.get("whispers", [])
    if not isinstance(whispers, list):
        whispers = []
    whispers.append(
        {
            "text": payload.text,
            "target": payload.target,
            "priority": payload.priority,
            "delivered": delivered,
            "by": str(ctx.user_id),
            "at": now_iso,
        }
    )
    meta["whispers"] = whispers[-20:]
    meta["last_whisper"] = payload.text
    meta["whisper_at"] = now_iso
    meta["last_heartbeat_at"] = now_iso
    audit = meta.get("audit", [])
    if isinstance(audit, list):
        audit.append(
            {
                "event": "whisper_sent",
                "by": str(ctx.user_id),
                "at": now_iso,
                "delivered": delivered,
            }
        )
        meta["audit"] = audit
    row.meta = meta
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "live_monitor.whisper",
        {
            "call_id": str(call_id),
            "session_id": str(session_id),
            "target": payload.target,
            "delivered": delivered,
        },
        resource_type="live_call_session",
        resource_id=session_id,
    )
    await session.commit()

    return {
        "id": str(row.id),
        "call_id": str(call_id),
        "whisper": payload.text,
        "target": payload.target,
        "status": "delivered" if delivered else "queued_for_pipeline",
        "delivery_status": "DELIVERED" if delivered else "PENDING_PIPELINE",
        "media_connected": delivered,
        "at": now_iso,
    }


@router.get("/{call_id}/monitor/{session_id}/whispers", response_model=dict)
async def list_whispers(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    meta = row.meta if isinstance(row.meta, dict) else {}
    whispers = meta.get("whispers", [])
    return {"session_id": str(session_id), "whispers": whispers, "total": len(whispers)}


@router.post("/{call_id}/takeover", response_model=TakeoverOut, status_code=201)
async def human_takeover(
    call_id: uuid.UUID,
    payload: TakeoverRequest,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/calls/{id}/takeover — Supervisor takeover via provider TwiML replacement."""
    await enforce_tenant_rate_limit(ctx.tenant_id, "takeover", 10)
    call = await _get_call(session, ctx.tenant_id, call_id)
    if call.status in (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELLED, CallStatus.NO_ANSWER):
        raise HTTPException(status_code=409, detail="call not active for takeover")

    await _expire_stale_sessions(session, ctx.tenant_id, call_id)

    idem_key = payload.idempotency_key or x_idempotency_key
    if idem_key:
        cached_id = await get_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation="live_monitor.takeover",
            key=idem_key,
        )
        if cached_id:
            existing_idem = await session.get(LiveCallSession, uuid.UUID(cached_id))
            if (
                existing_idem is not None
                and existing_idem.tenant_id == ctx.tenant_id
                and existing_idem.call_id == call_id
            ):
                return _to_takeover_out(existing_idem)

    existing_takeover = (
        await session.execute(
            select(LiveCallSession)
            .where(
                LiveCallSession.tenant_id == ctx.tenant_id,
                LiveCallSession.call_id == call_id,
                LiveCallSession.mode == "takeover",
                LiveCallSession.status == "active",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_takeover:
        raise HTTPException(status_code=409, detail="call already has active takeover session")

    destination = (
        payload.supervisor_destination
        or str(payload.metadata.get("supervisor_destination") or "")
        or getattr(ctx.tenant, "escalation_number", None)
    )
    webrtc_client = payload.webrtc_client or str(payload.metadata.get("webrtc_client") or "")
    conference_name = payload.conference_name or str(payload.metadata.get("conference_name") or "")
    if not destination and not webrtc_client and not conference_name:
        webrtc_client = f"supervisor_{ctx.user_id.hex[:12]}"

    try:
        tk_result = await execute_takeover(
            call,
            destination,
            webrtc_client=webrtc_client or None,
            conference_name=conference_name or None,
            session=session,
            tenant=ctx.tenant,
            whisper_text=payload.whisper_text,
            reason=payload.reason or "supervisor_takeover",
        )
    except UnsupportedCapability as exc:
        raise HTTPException(status_code=501, detail=exc.as_dict()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not tk_result.ok:
        await record_enterprise_audit(
            session,
            ctx.tenant_id,
            ctx.user_id,
            "live_monitor.takeover_failed",
            {
                "operation": "human_takeover",
                "call_id": str(call_id),
                "rolled_back_to_ai": tk_result.rolled_back_to_ai,
                "reason": payload.reason or "supervisor_takeover",
            },
            resource_type="call",
            resource_id=call.id,
        )
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "code": "takeover_failed",
                "message": "Provider rejected takeover; call rolled back to AI agent.",
                "rolled_back_to_ai": tk_result.rolled_back_to_ai,
            },
        )

    now = _now()
    sess = LiveCallSession(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        supervisor_id=ctx.user_id,
        mode="takeover",
        status="active",
        created_at=now,
        meta={
            "ownership": payload.ownership,
            "reason": payload.reason,
            "joined_by": str(ctx.user_id),
            "notify_customer": payload.notify_customer,
            "metadata": payload.metadata,
            "media_connected": True,
            "media_status": "BRIDGED",
            "takeover": tk_result.as_dict(),
            "expires_at": (now + timedelta(minutes=DEFAULT_SESSION_TTL_MINUTES)).isoformat(),
            "last_heartbeat_at": now.isoformat(),
            "audit": [
                {
                    "event": "takeover_started",
                    "by": str(ctx.user_id),
                    "at": now.isoformat(),
                    "ownership": payload.ownership,
                    "reason": payload.reason,
                    "destination": tk_result.destination,
                },
                {
                    "event": "takeover_completed",
                    "by": str(ctx.user_id),
                    "at": now.isoformat(),
                    "recording_continuity": tk_result.recording_continuity,
                },
            ],
        },
    )
    session.add(sess)
    await session.flush()

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "live_monitor.takeover",
        {
            "operation": "human_takeover",
            "call_id": str(call_id),
            "session_id": str(sess.id),
            "ownership": payload.ownership,
            "reason": payload.reason,
        },
        resource_type="live_call_session",
        resource_id=sess.id,
    )

    if idem_key:
        await store_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation="live_monitor.takeover",
            key=idem_key,
            resource_type="live_call_session",
            resource_id=sess.id,
            request_data={"call_id": str(call_id), "ownership": payload.ownership},
        )

    await session.commit()
    await session.refresh(sess)

    log.info(
        "takeover.joined",
        tenant_id=str(ctx.tenant_id),
        call_id=str(call_id),
        session_id=str(sess.id),
        ownership=payload.ownership,
        supervisor_id=str(ctx.user_id),
    )
    return _to_takeover_out(sess)


@router.get("/{call_id}/takeover", response_model=List[TakeoverOut])
async def list_takeovers(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    await _get_call(session, ctx.tenant_id, call_id)
    rows = (
        await session.execute(
            select(LiveCallSession)
            .where(
                LiveCallSession.tenant_id == ctx.tenant_id,
                LiveCallSession.call_id == call_id,
                LiveCallSession.mode == "takeover",
            )
            .order_by(LiveCallSession.created_at.desc())
        )
    ).scalars().all()
    return [_to_takeover_out(r) for r in rows]


@router.post("/{call_id}/takeover/{session_id}/leave", response_model=TakeoverOut)
async def leave_takeover(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """Operator leave takeover session — ownership returns to agent or shared."""
    call = await _get_call(session, ctx.tenant_id, call_id)
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    if row.mode != "takeover":
        raise HTTPException(status_code=422, detail="not a takeover session")
    if row.status != "active":
        raise HTTPException(status_code=409, detail="session not active")

    await rollback_to_ai(
        call,
        session=session,
        reconnect_stream=True,
        reason="supervisor_left_takeover",
    )

    row.status = "ended"
    row.ended_at = _now()
    meta = dict(row.meta or {})
    meta["left_by"] = str(ctx.user_id)
    meta["left_at"] = row.ended_at.isoformat()
    audit = meta.get("audit", [])
    if isinstance(audit, list):
        audit.append({"event": "takeover_left", "by": str(ctx.user_id), "at": _now_iso()})
        meta["audit"] = audit
    row.meta = meta

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "takeover.left",
        {"call_id": str(call_id), "session_id": str(session_id)},
        resource_type="live_call_session",
        resource_id=session_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_takeover_out(row)


@router.post("/{call_id}/takeover/{session_id}/transfer-ownership", response_model=TakeoverOut)
async def transfer_ownership(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    new_owner: str = Query(..., pattern="^(operator|shared|agent)$"),
    new_supervisor_id: Optional[uuid.UUID] = Query(default=None),
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/calls/{id}/takeover/{sid}/transfer-ownership — Transfer ownership."""
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    if row.mode != "takeover":
        raise HTTPException(status_code=422, detail="not a takeover session")
    if row.status != "active":
        raise HTTPException(status_code=409, detail="session not active")

    meta = dict(row.meta or {})
    old_owner = meta.get("ownership", "operator")
    meta["ownership"] = new_owner
    if new_supervisor_id:
        row.supervisor_id = new_supervisor_id
        meta["transferred_to"] = str(new_supervisor_id)
    audit = meta.get("audit", [])
    if isinstance(audit, list):
        audit.append(
            {
                "event": "ownership_transferred",
                "from": old_owner,
                "to": new_owner,
                "by": str(ctx.user_id),
                "at": _now_iso(),
            }
        )
        meta["audit"] = audit
    row.meta = meta

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "takeover.ownership_transferred",
        {
            "call_id": str(call_id),
            "session_id": str(session_id),
            "from_owner": old_owner,
            "to_owner": new_owner,
        },
        resource_type="live_call_session",
        resource_id=session_id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_takeover_out(row)


@router.get("/{call_id}/takeover/{session_id}/audit", response_model=dict)
async def takeover_audit(
    call_id: uuid.UUID,
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_session_row(session, ctx.tenant_id, call_id, session_id)
    meta = row.meta if isinstance(row.meta, dict) else {}
    audit = meta.get("audit", [])
    return {
        "session_id": str(session_id),
        "call_id": str(call_id),
        "audit": audit,
        "total_events": len(audit) if isinstance(audit, list) else 0,
    }


@router.get("/{call_id}/monitor/analytics", response_model=MonitorAnalyticsOut)
async def monitor_analytics(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    await _get_call(session, ctx.tenant_id, call_id)
    await _expire_stale_sessions(session, ctx.tenant_id, call_id)
    total_q = await session.execute(
        select(func.count(LiveCallSession.id)).where(
            LiveCallSession.tenant_id == ctx.tenant_id, LiveCallSession.call_id == call_id
        )
    )
    total = total_q.scalar() or 0

    active_q = await session.execute(
        select(func.count(LiveCallSession.id)).where(
            LiveCallSession.tenant_id == ctx.tenant_id,
            LiveCallSession.call_id == call_id,
            LiveCallSession.status == "active",
        )
    )
    active = active_q.scalar() or 0

    takeover_q = await session.execute(
        select(func.count(LiveCallSession.id)).where(
            LiveCallSession.tenant_id == ctx.tenant_id,
            LiveCallSession.call_id == call_id,
            LiveCallSession.mode == "takeover",
        )
    )
    takeover = takeover_q.scalar() or 0

    modes_q = await session.execute(
        select(LiveCallSession.mode, func.count(LiveCallSession.id))
        .where(LiveCallSession.tenant_id == ctx.tenant_id, LiveCallSession.call_id == call_id)
        .group_by(LiveCallSession.mode)
    )
    modes = {mode: int(cnt) for mode, cnt in modes_q.all()}

    avg_duration = None
    rows = (
        await session.execute(
            select(LiveCallSession).where(
                LiveCallSession.tenant_id == ctx.tenant_id,
                LiveCallSession.call_id == call_id,
                LiveCallSession.ended_at.isnot(None),
            )
        )
    ).scalars().all()
    durations = [d for d in (_session_duration(r) for r in rows) if d is not None]
    if durations:
        avg_duration = sum(durations) / len(durations)

    await session.commit()
    return MonitorAnalyticsOut(
        call_id=str(call_id),
        total_sessions=int(total),
        active_sessions=int(active),
        takeover_sessions=int(takeover),
        average_duration_seconds=avg_duration,
        modes=modes,
    )


@router.get("/monitor/health", response_model=dict)
async def monitor_health(
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    await _expire_stale_sessions(session, ctx.tenant_id)
    total_q = await session.execute(
        select(func.count(LiveCallSession.id)).where(LiveCallSession.tenant_id == ctx.tenant_id)
    )
    total = total_q.scalar() or 0
    active_q = await session.execute(
        select(func.count(LiveCallSession.id)).where(
            LiveCallSession.tenant_id == ctx.tenant_id, LiveCallSession.status == "active"
        )
    )
    active = active_q.scalar() or 0
    idem_q = await session.execute(
        select(func.count(RequestIdempotencyReceipt.id)).where(
            RequestIdempotencyReceipt.tenant_id == ctx.tenant_id,
            RequestIdempotencyReceipt.operation.like("live_monitor.%"),
        )
    )
    idem_total = idem_q.scalar() or 0
    await session.commit()
    return {
        "tenant_id": str(ctx.tenant_id),
        "total_sessions": int(total),
        "active_sessions": int(active),
        "status": "healthy",
        "idempotency_entries": int(idem_total),
        "idempotency_scope": "durable_database",
        "idempotency_authoritative": True,
        "at": _now_iso(),
    }


@router.get("/monitor/config", response_model=dict)
async def monitor_config(
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
):
    del ctx
    return {
        "modes": sorted(MONITOR_MODES),
        "ownership_types": sorted(OWNERSHIP_TYPES),
        "max_concurrent_per_call": MAX_CONCURRENT_SESSIONS_PER_CALL,
        "max_per_supervisor": MAX_SESSIONS_PER_SUPERVISOR,
        "default_ttl_minutes": DEFAULT_SESSION_TTL_MINUTES,
        "max_whisper_length": MAX_WHISPER_LENGTH,
        "required_permission": CALLS_MONITOR_PERMISSION,
        "at": _now_iso(),
    }


@router.get("/monitoring/idempotency/stats")
async def idempotency_stats(
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(RequestIdempotencyReceipt.status, func.count(RequestIdempotencyReceipt.id))
            .where(
                RequestIdempotencyReceipt.tenant_id == ctx.tenant_id,
                RequestIdempotencyReceipt.operation.like("live_monitor.%"),
            )
            .group_by(RequestIdempotencyReceipt.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    total = sum(counts.values())
    return {
        "scope": "durable_database",
        "authoritative": True,
        "total": total,
        "active": counts.get("succeeded", 0) + counts.get("in_progress", 0),
        "at": _now_iso(),
    }


@router.delete("/monitoring/idempotency/cache", status_code=410)
async def clear_monitoring_idempotency_cache(
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
):
    """Durable monitoring idempotency receipts cannot be cleared."""
    del ctx
    raise HTTPException(
        status_code=410,
        detail={
            "code": "durable_idempotency_not_clearable",
            "message": "Durable live-monitoring idempotency receipts are retained to prevent replayed side effects.",
        },
    )


# ===========================================================================
# PART 15 — live calls list, live scoring, auto-actions
#
# The supervisor endpoints above answer "may I listen to this call". The block
# below answers "which calls need me right now, and what should happen to them
# automatically". Keeping them in one router keeps `calls.monitor` as the single
# permission a supervisor needs.
# ===========================================================================

import asyncio as _asyncio  # noqa: E402  (grouped with the PART 15 block)
import json as _json  # noqa: E402

from fastapi.responses import StreamingResponse  # noqa: E402

from app.db.monitoring_models import AutoActionRule, AutoActionRun, CallLiveScore  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.telephony import auto_actions as _auto  # noqa: E402
from app.telephony import live_scoring as _scoring  # noqa: E402

#: How often the SSE stream re-reads the active-call set.
LIVE_STREAM_INTERVAL_SECONDS = 2.0
#: A stream closes itself after this long, so an abandoned tab cannot hold a
#: connection (and a database read every two seconds) forever.
LIVE_STREAM_MAX_SECONDS = 300.0
#: Active calls returned by the list endpoint.
LIVE_LIST_LIMIT = 200


def _mask_caller(number: str | None) -> str:
    """Last four digits. The live list is a working screen, not a data export."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def _call_age_seconds(call: Call, now: datetime) -> float:
    if call.started_at is None:
        return 0.0
    started = call.started_at
    if started.tzinfo is not None:
        started = started.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0.0, (now - started).total_seconds())


async def _live_call_rows(session: AsyncSession, tenant_id: uuid.UUID) -> List[Dict[str, Any]]:
    """Active calls with the last few transcript lines and the latest score."""
    calls = list(
        (
            await session.scalars(
                select(Call)
                .where(Call.tenant_id == tenant_id, Call.status == CallStatus.IN_PROGRESS)
                .order_by(Call.started_at.desc())
                .limit(LIVE_LIST_LIMIT)
            )
        ).all()
    )
    if not calls:
        return []

    call_ids = [call.id for call in calls]
    score_rows = (
        await session.scalars(
            select(CallLiveScore)
            .where(
                CallLiveScore.tenant_id == tenant_id,
                CallLiveScore.call_id.in_(call_ids),
            )
            .order_by(CallLiveScore.created_at.desc())
        )
    ).all()
    latest: Dict[uuid.UUID, CallLiveScore] = {}
    for row in score_rows:
        latest.setdefault(row.call_id, row)

    bus = get_monitor_bus()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out: List[Dict[str, Any]] = []
    for call in calls:
        score = latest.get(call.id)
        out.append(
            {
                "call_id": str(call.id),
                "direction": (
                    call.direction.value if hasattr(call.direction, "value") else str(call.direction or "")
                ),
                "caller": _mask_caller(call.from_number),
                "number": _mask_caller(call.to_number),
                "agent_id": str(call.agent_id) if call.agent_id else None,
                "started_at": call.started_at.isoformat() if call.started_at else None,
                "duration_seconds": round(_call_age_seconds(call, now), 1),
                "status": (
                    call.status.value if hasattr(call.status, "value") else str(call.status)
                ),
                "score": score.score if score else None,
                "band": score.band if score else None,
                "score_components": dict(score.components or {}) if score else {},
                "score_reasons": list((score.components or {}).get("reasons", [])) if score else [],
                "transcript_tail": bus.transcript_tail(call.id),
            }
        )
    return out


@router.get("/live")
async def list_live_calls(
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Every active call in the tenant, newest first (P15-01)."""
    calls = await _live_call_rows(session, ctx.tenant_id)
    return {
        "calls": calls,
        "total": len(calls),
        "at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/live/stream")
async def stream_live_calls(
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
) -> StreamingResponse:
    """Server-sent events: one snapshot of the active-call set every two seconds.

    The session is created inside the generator: a request-scoped session is
    closed when the response starts streaming, which would surface as a
    ``ResourceClosedError`` two seconds later on the first refresh.

    ``EventSource`` cannot send an Authorization header, so the browser client
    passes the access token as a query parameter on a first call that exchanges
    it for a short-lived stream; here we accept the standard header, and the
    dashboard's server-side proxy is what carries it.
    """
    tenant_id = ctx.tenant_id
    sessionmaker = get_sessionmaker()

    async def event_source():
        started = datetime.now(timezone.utc)
        while True:
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            if elapsed > LIVE_STREAM_MAX_SECONDS:
                yield "event: end\ndata: {\"reason\": \"max_duration\"}\n\n"
                return
            try:
                async with sessionmaker() as session:
                    calls = await _live_call_rows(session, tenant_id)
                payload = {
                    "calls": calls,
                    "total": len(calls),
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                yield f"data: {_json.dumps(payload, separators=(',', ':'))}\n\n"
            except _asyncio.CancelledError:  # client went away
                raise
            except Exception as exc:  # keep the stream alive, report the error
                log.warning(
                    "live_monitoring.stream_tick_failed",
                    error_type=type(exc).__name__,
                )
                yield "event: error\ndata: {\"code\": \"tick_failed\"}\n\n"
            await _asyncio.sleep(LIVE_STREAM_INTERVAL_SECONDS)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class LiveScoreTurn(BaseModel):
    speaker: str = Field(pattern="^(caller|agent)$")
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    latency_ms: float = Field(default=0.0, ge=0.0, le=120_000.0)
    text: str = Field(default="", max_length=4000)


class LiveScoreIn(BaseModel):
    turns: List[LiveScoreTurn] = Field(min_length=1, max_length=24)
    weights: Dict[str, float] | None = None
    agent_id: Optional[str] = Field(default=None, max_length=120)


@router.post("/{call_id}/live-score")
async def record_live_score(
    call_id: uuid.UUID,
    payload: LiveScoreIn,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Score a batch of turns for a call, store the explanation, fan it out.

    Scoring is computed by :mod:`app.telephony.live_scoring`; this route only
    decides where the result is kept. Nothing is stored until there are enough
    turns for a score to mean something.
    """
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "call not found"})

    scorer = _scoring.LiveScorer(weights=payload.weights)
    score = None
    for turn in payload.turns:
        score = scorer.observe(
            str(call_id),
            _scoring.Turn(
                speaker=turn.speaker,
                sentiment=turn.sentiment,
                latency_ms=turn.latency_ms,
                text=turn.text,
            ),
        )
    if score is None:
        return {
            "call_id": str(call_id),
            "scored": False,
            "reason": "insufficient_samples",
            "min_samples": _scoring.MIN_SAMPLES,
        }

    row = CallLiveScore(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        agent_id=payload.agent_id or (str(call.agent_id) if call.agent_id else None),
        score=score.score,
        components={**score.components, "reasons": list(score.reasons)},
        weights=score.weights,
        band=score.band,
        sample_count=score.sample_count,
    )
    session.add(row)
    await session.commit()
    published = await _scoring.publish_score(str(call_id), score)
    return {
        "call_id": str(call_id),
        "scored": True,
        "score": score.score,
        "band": score.band,
        "components": score.components,
        "reasons": list(score.reasons),
        "published_to": published,
        "sample_count": score.sample_count,
    }


class AutoActionRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: str = Field(pattern="^(score_below|sentiment_below|keyword|loop_detected|latency_above)$")
    action: str = Field(pattern="^(transfer|webhook|alert|end_call)$")
    threshold: Optional[float] = None
    keywords: List[str] = Field(default_factory=list, max_length=20)
    action_config: Dict[str, Any] = Field(default_factory=dict)
    scope_agent_id: Optional[str] = Field(default=None, max_length=120)
    max_per_call: int = Field(default=1, ge=1, le=5)
    enabled: bool = True


class AutoActionRulePatch(BaseModel):
    threshold: Optional[float] = None
    keywords: Optional[List[str]] = None
    action_config: Optional[Dict[str, Any]] = None
    max_per_call: Optional[int] = Field(default=None, ge=1, le=5)
    enabled: Optional[bool] = None


class AutoActionSignalIn(BaseModel):
    score: Optional[int] = Field(default=None, ge=0, le=100)
    sentiment: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    latency_ms: Optional[float] = Field(default=None, ge=0.0, le=120_000.0)
    text: str = Field(default="", max_length=8000)
    loop_detected: bool = False


def _auto_rule_dict(rule: AutoActionRule) -> Dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger": rule.trigger,
        "action": rule.action,
        "threshold": rule.threshold,
        "keywords": list(rule.keywords or []),
        "action_config": dict(rule.action_config or {}),
        "scope_agent_id": rule.scope_agent_id,
        "max_per_call": rule.max_per_call,
        "enabled": bool(rule.enabled),
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


@router.get("/auto-actions")
async def list_auto_actions(
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    rows = (
        await session.scalars(
            select(AutoActionRule)
            .where(AutoActionRule.tenant_id == ctx.tenant_id)
            .order_by(AutoActionRule.name)
        )
    ).all()
    return {
        "rules": [_auto_rule_dict(row) for row in rows],
        "triggers": list(_auto.TRIGGERS),
        "actions": list(_auto.ACTIONS),
    }


@router.post("/auto-actions", status_code=201)
async def create_auto_action(
    payload: AutoActionRuleIn,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    if payload.trigger in ("score_below", "sentiment_below", "latency_above") and payload.threshold is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "threshold_required", "message": f"{payload.trigger} needs a threshold"},
        )
    if payload.trigger == "keyword" and not payload.keywords:
        raise HTTPException(
            status_code=422,
            detail={"code": "keywords_required", "message": "keyword trigger needs at least one keyword"},
        )
    existing = await session.scalar(
        select(AutoActionRule).where(
            AutoActionRule.tenant_id == ctx.tenant_id, AutoActionRule.name == payload.name
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "name_taken", "message": f"a rule named {payload.name!r} exists"},
        )
    rule = AutoActionRule(
        tenant_id=ctx.tenant_id,
        name=payload.name.strip(),
        trigger=payload.trigger,
        threshold=payload.threshold,
        keywords=[word.strip() for word in payload.keywords if word.strip()],
        action=payload.action,
        action_config=dict(payload.action_config),
        scope_agent_id=payload.scope_agent_id or None,
        enabled=payload.enabled,
        max_per_call=payload.max_per_call,
        created_by=str(ctx.user_id) if ctx.user_id else None,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _auto_rule_dict(rule)


@router.patch("/auto-actions/{rule_id}")
async def patch_auto_action(
    rule_id: uuid.UUID,
    payload: AutoActionRulePatch,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    rule = await session.get(AutoActionRule, rule_id)
    if rule is None or rule.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return _auto_rule_dict(rule)


@router.delete("/auto-actions/{rule_id}", status_code=204)
async def delete_auto_action(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(AutoActionRule, rule_id)
    if rule is None or rule.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    await session.delete(rule)
    await session.commit()
    return None


@router.get("/{call_id}/auto-actions")
async def get_call_auto_actions(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_calls_monitor(write=False)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Every auto-action attempt recorded for one call, including skips."""
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    rows = (
        await session.scalars(
            select(AutoActionRun)
            .where(
                AutoActionRun.tenant_id == ctx.tenant_id,
                AutoActionRun.call_id == call_id,
            )
            .order_by(AutoActionRun.created_at)
        )
    ).all()
    return {
        "call_id": str(call_id),
        "runs": [
            {
                "id": str(row.id),
                "rule_id": str(row.rule_id) if row.rule_id else None,
                "action": row.action,
                "status": row.status,
                "sequence": row.sequence,
                "detail": dict(row.detail or {}),
                "last_error": row.last_error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


@router.post("/{call_id}/auto-actions/evaluate")
async def evaluate_auto_actions(
    call_id: uuid.UUID,
    payload: AutoActionSignalIn,
    ctx: TenantContext = Depends(require_calls_monitor(write=True)),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Evaluate the tenant's rules against a supplied signal for one call.

    No action handlers are registered on this path: the route reports what the
    engine *decided*, and a decision that needs a transfer is left ``skipped``
    with ``no_handler_registered`` rather than pretending to transfer. The voice
    pipeline registers the real handlers.
    """
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found"})

    signal = _auto.Signal(
        score=payload.score,
        sentiment=payload.sentiment,
        latency_ms=payload.latency_ms,
        text=payload.text,
        loop_detected=payload.loop_detected,
        agent_id=str(call.agent_id) if call.agent_id else None,
    )
    rules = await _auto.load_rules(
        session,
        ctx.tenant_id,
        agent_id=str(call.agent_id) if call.agent_id else None,
    )
    decisions = await _auto.run_auto_actions(
        session,
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        rules=rules,
        signal=signal,
        handlers={},
        context={"agent_id": str(call.agent_id) if call.agent_id else None},
    )
    await session.commit()
    return {
        "call_id": str(call_id),
        "matched": len(decisions),
        "summary": _auto.summarise(decisions),
        "decisions": [
            {
                "rule_id": str(decision.rule_id) if decision.rule_id else None,
                "rule_name": decision.rule_name,
                "action": decision.action,
                "sequence": decision.sequence,
                "status": decision.status,
                "reason": decision.reason,
                "detail": dict(decision.detail),
            }
            for decision in decisions
        ],
    }
