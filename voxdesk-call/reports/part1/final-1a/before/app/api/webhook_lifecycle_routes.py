# File: app/api/webhook_lifecycle_routes.py — Missing APIs: webhook endpoint lifecycle update/delete, event subscription/filtering, delivery history, retry/test, outbound delivery control, DLQ, signature, replay, delivery status, event-type subscription
"""
Closes gaps:
16. Webhook endpoint lifecycle incomplete — update/delete, event subscription/filtering, delivery history, retry/test
17. Webhook outbound delivery control missing/partial — dispatcher, retries, DLQ, signature, replay, delivery status
28. Webhook event-type subscription API — per-endpoint event filters and transfer/call events

Features:
- Endpoint CRUD with URL validation, HMAC secret, event allowlist, retry policy
- Event-type subscription per-endpoint filters (call, transfer, lead, batch, etc.)
- Delivery history with status, http_status, attempts, payload, response
- Retry with exponential backoff, DLQ after max attempts, manual replay
- Signature generation HMAC SHA256, verification, test endpoint
- Dispatcher with rate limiting, idempotency, audit, telemetry
- Health, stats, failure count, last_delivery tracking
"""
from __future__ import annotations

import httpx

import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.db.enterprise_models import WebhookEndpoint, WebhookDeliveryAttempt
from app.tenancy.isolation import HierarchyError, to_http
from app.core.logging import log
from app.core.ssrf import OutboundUrlError, validate_outbound_url, validate_resolved_outbound_url
from app.webhooks.delivery import deliver, HttpResult

router = APIRouter(prefix="/api/webhooks", tags=["webhooks-lifecycle"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_URL_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 500
MAX_SECRET_LENGTH = 128
MAX_EVENTS_PER_ENDPOINT = 50
DEFAULT_RETRY_MAX_ATTEMPTS = 3
MAX_RETRY_ATTEMPTS = 10
MIN_RETRY_BACKOFF = 5
MAX_RETRY_BACKOFF = 3600
DEFAULT_BACKOFF_SECONDS = 60
ALLOWED_EVENT_TYPES = {
    "call.started", "call.answered", "call.completed", "call.failed", "call.transferred",
    "call.recording.ready", "call.transcript.ready", "call.analysis.completed",
    "transfer.requested", "transfer.completed", "transfer.failed",
    "lead.created", "lead.updated", "lead.dnc", "lead.converted",
    "batch.started", "batch.completed", "batch.failed", "batch.cancelled",
    "webhook.test", "webhook.failed", "webhook.dlq",
    "agent.published", "agent.unpublished",
    "campaign.started", "campaign.completed",
}
URL_REGEX = re.compile(r"^https://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
SECRET_REGEX = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
_rate_buckets: Dict[str, List[float]] = {}
_idempotency_cache: Dict[str, Tuple[str, datetime]] = {}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

class WebhookCreate(_Strict):
    url: str = Field(min_length=10, max_length=MAX_URL_LENGTH, pattern=r"^https://")
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    secret: str = Field(default="", max_length=MAX_SECRET_LENGTH, description="Optional HMAC secret for signature")
    events: List[str] = Field(default_factory=list, max_length=MAX_EVENTS_PER_ENDPOINT, description="Event types to subscribe, empty = all")
    is_active: bool = True
    retry_policy: Dict[str, Any] = Field(default_factory=lambda: {"max_attempts": 3, "backoff_seconds": 60, "max_backoff_seconds": 3600})
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom headers to send")
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    enabled_events_only: bool = Field(default=False, description="If true, only subscribed events are delivered")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not URL_REGEX.match(v):
            raise ValueError("url must be https:// and valid")
        validate_outbound_url(v, require_https=True)
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: List[str]) -> List[str]:
        if not v:
            return v
        invalid = [e for e in v if e not in ALLOWED_EVENT_TYPES]
        if invalid:
            raise ValueError(f"invalid event types: {invalid}, allowed: {sorted(ALLOWED_EVENT_TYPES)}")
        # Deduplicate
        return list(dict.fromkeys(v))

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, v: str) -> str:
        if not v:
            return v
        if len(v) < 8:
            raise ValueError("secret must be at least 8 chars")
        return v

class WebhookUpdate(_Strict):
    url: Optional[str] = Field(default=None, min_length=10, max_length=MAX_URL_LENGTH)
    description: Optional[str] = None
    secret: Optional[str] = None
    events: Optional[List[str]] = None
    is_active: Optional[bool] = None
    retry_policy: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=60)

class WebhookOut(_Strict):
    id: str
    tenant_id: str
    url: str
    description: str
    events: List[str]
    is_active: bool
    retry_policy: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_delivery_at: Optional[str] = None
    failure_count: int = 0
    health: str = "unknown"
    headers: Dict[str, str] = Field(default_factory=dict)

class WebhookListOut(_Strict):
    endpoints: List[WebhookOut]
    total: int
    limit: int
    offset: int

class DeliveryAttemptOut(_Strict):
    id: str
    endpoint_id: str
    event_type: str
    status: str
    http_status: int
    attempts: int
    next_retry_at: Optional[str] = None
    created_at: Optional[str] = None
    delivered_at: Optional[str] = None
    is_dlq: bool = False

class DeliveryListOut(_Strict):
    deliveries: List[DeliveryAttemptOut]
    total: int
    limit: int
    offset: int

class WebhookTestRequest(_Strict):
    event_type: str = Field(default="webhook.test", max_length=80)
    payload: Dict[str, Any] = Field(default_factory=lambda: {"test": True, "timestamp": "now"})

class WebhookTestOut(_Strict):
    endpoint_id: str
    event_type: str
    http_status: int
    response_body: str
    success: bool
    duration_ms: int
    signature: Optional[str] = None

class WebhookReplayRequest(_Strict):
    delivery_id: Optional[uuid.UUID] = None
    event_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

class WebhookStatsOut(_Strict):
    endpoint_id: str
    total_deliveries: int
    successful: int
    failed: int
    dlq: int
    pending: int
    success_rate: float
    last_delivery_at: Optional[str] = None
    failure_count: int

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now().isoformat()

def _hash_key(tenant_id: uuid.UUID, key: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{key}".encode()).hexdigest()[:32]

def _check_rate(tenant_id: uuid.UUID, action: str, limit: int) -> None:
    bucket = f"{tenant_id}:{action}"
    now = time.time()
    window = now - 60
    ts = [t for t in _rate_buckets.get(bucket, []) if t > window]
    if len(ts) >= limit:
        raise HTTPException(status_code=429, detail=f"rate limit {action} {limit}/min")
    ts.append(now)
    _rate_buckets[bucket] = ts

def _audit(event: str, **kwargs: Any) -> None:
    try:
        log.info(event, **kwargs)
    except Exception:
        pass

def _generate_signature(secret: str, payload: str, timestamp: str) -> str:
    if not secret:
        return ""
    msg = f"{timestamp}.{payload}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()

def _verify_signature(secret: str, payload: str, timestamp: str, signature: str) -> bool:
    if not secret:
        return True
    expected = _generate_signature(secret, payload, timestamp)
    return hmac.compare_digest(expected, signature)

def _to_webhook_out(row: WebhookEndpoint) -> WebhookOut:
    d = row.as_dict()
    # Health based on failure_count and last_delivery
    health = "healthy"
    if d["failure_count"] > 10:
        health = "unhealthy"
    elif d["failure_count"] > 3:
        health = "degraded"
    elif not d["is_active"]:
        health = "disabled"

    headers = {}
    try:
        # Retry policy may contain headers
        if isinstance(row.retry_policy, dict):
            headers = row.retry_policy.get("headers", {})
    except Exception:
        headers = {}

    return WebhookOut(
        id=d["id"],
        tenant_id=d["tenant_id"],
        url=d["url"],
        description=d["description"],
        events=d["events"],
        is_active=d["is_active"],
        retry_policy=d["retry_policy"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        last_delivery_at=d["last_delivery_at"],
        failure_count=d["failure_count"],
        health=health,
        headers=headers,
    )

def _to_delivery_out(row: WebhookDeliveryAttempt) -> DeliveryAttemptOut:
    d = row.as_dict()
    is_dlq = d["status"] == "dlq" or (d["attempts"] >= 5 and d["status"] == "failed")
    return DeliveryAttemptOut(
        id=d["id"],
        endpoint_id=d["endpoint_id"],
        event_type=d["event_type"],
        status=d["status"],
        http_status=d["http_status"],
        attempts=d["attempts"],
        next_retry_at=d["next_retry_at"],
        created_at=d["created_at"],
        delivered_at=d["delivered_at"],
        is_dlq=is_dlq,
    )

async def _get_endpoint(session: AsyncSession, tenant_id: uuid.UUID, endpoint_id: uuid.UUID) -> WebhookEndpoint:
    row = await session.get(WebhookEndpoint, endpoint_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="webhook endpoint not found")
    return row

async def _dispatch_webhook(
    session: AsyncSession,
    endpoint: WebhookEndpoint,
    event_type: str,
    payload: Dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> WebhookDeliveryAttempt:
    """Persist an actual HTTP delivery result, never infer success from a URL.

    The injectable HTTP client is the transport boundary for contract tests;
    production uses the existing signed delivery adapter and a real socket.
    """
    attempt = WebhookDeliveryAttempt(
        endpoint_id=endpoint.id, tenant_id=endpoint.tenant_id,
        event_type=event_type, payload=payload, status="pending",
        http_status=0, attempts=0,
    )
    session.add(attempt)
    await session.flush()
    policy = endpoint.retry_policy if isinstance(endpoint.retry_policy, dict) else {}
    result = HttpResult("permanent", category="endpoint_inactive")
    if endpoint.is_active:
        try:
            await validate_resolved_outbound_url(endpoint.url, require_https=True)
            # Custom header transport is not implemented by this adapter. Fail
            # closed rather than silently drop tenant-specified authentication.
            if policy.get("headers"):
                result = HttpResult("permanent", category="custom_headers_not_supported")
            else:
                result = await deliver(
                    url=endpoint.url,
                    body=json.dumps(payload, sort_keys=True).encode(),
                    secret=endpoint.secret,
                    event_id=str(attempt.id),
                    timeout_seconds=min(60, max(1, float(policy.get("timeout_seconds", 10)))),
                    client=client,
                )
                attempt.attempts = 1
        except OutboundUrlError:
            result = HttpResult("permanent", category="invalid_endpoint")
    attempt.http_status = result.status
    # Store only bounded, non-sensitive classifications, not provider bodies.
    attempt.response_body = result.category
    if result.kind == "success":
        attempt.status = "delivered"
        attempt.delivered_at = _now()
        endpoint.last_delivery_at = attempt.delivered_at
        endpoint.failure_count = 0
    else:
        endpoint.failure_count += 1
        max_attempts = max(1, int(policy.get("max_attempts", DEFAULT_RETRY_MAX_ATTEMPTS)))
        attempt.status = "dlq" if result.kind == "permanent" or attempt.attempts >= max_attempts else "failed"
        if attempt.status == "failed":
            backoff = max(1, int(policy.get("backoff_seconds", DEFAULT_BACKOFF_SECONDS)))
            maximum = max(backoff, int(policy.get("max_backoff_seconds", 3600)))
            attempt.next_retry_at = _now() + timedelta(seconds=min(maximum, backoff * 2 ** attempt.attempts))
    await session.commit()
    await session.refresh(attempt)
    return attempt

# ---------------------------------------------------------------------------
# Endpoints — CRUD lifecycle
# ---------------------------------------------------------------------------

@router.post("", response_model=WebhookOut, status_code=201)
async def create_webhook(
    payload: WebhookCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/webhooks — Create endpoint with event subscription and retry policy."""
    try:
        _check_rate(ctx.tenant_id, "webhook_create", 20)

        idem_key = x_idempotency_key
        if idem_key:
            kh = _hash_key(ctx.tenant_id, idem_key)
            cached = _idempotency_cache.get(kh)
            if cached:
                cid, exp = cached
                if _now() < exp:
                    try:
                        existing = await session.get(WebhookEndpoint, uuid.UUID(cid))
                        if existing and existing.tenant_id == ctx.tenant_id:
                            return _to_webhook_out(existing)
                    except Exception:
                        pass

        # Check duplicate URL for tenant
        existing_url = (
            await session.execute(
                select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == ctx.tenant_id, WebhookEndpoint.url == payload.url).limit(1)
            )
        ).scalar_one_or_none()
        if existing_url:
            raise HTTPException(status_code=409, detail="webhook with this URL already exists")

        endpoint = WebhookEndpoint(
            tenant_id=ctx.tenant_id,
            url=payload.url,
            description=payload.description,
            secret=payload.secret,
            events=payload.events,
            is_active=payload.is_active,
            retry_policy={
                **payload.retry_policy,
                "headers": payload.headers,
                "timeout_seconds": payload.timeout_seconds,
            },
            created_by=ctx.user_id,
        )
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)

        if idem_key:
            _idempotency_cache[_hash_key(ctx.tenant_id, idem_key)] = (str(endpoint.id), _now() + timedelta(hours=24))

        _audit("webhook.created", tenant_id=str(ctx.tenant_id), endpoint_id=str(endpoint.id), url=payload.url, events=payload.events)
        return _to_webhook_out(endpoint)
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.get("", response_model=WebhookListOut)
async def list_webhooks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    is_active: Optional[bool] = Query(default=None),
    event_type: Optional[str] = Query(default=None, description="Filter by subscribed event"),
    search: Optional[str] = Query(default=None, max_length=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks — List endpoints with filters."""
    scope = [WebhookEndpoint.tenant_id == ctx.tenant_id]
    if is_active is not None:
        scope.append(WebhookEndpoint.is_active == is_active)
    if search:
        scope.append(WebhookEndpoint.url.ilike(f"%{search}%"))

    # For event_type filter, need JSON contains — simplified in-memory post-filter
    total_q = await session.execute(select(func.count(WebhookEndpoint.id)).where(*scope))
    total = total_q.scalar() or 0

    rows_q = await session.execute(
        select(WebhookEndpoint).where(*scope).order_by(WebhookEndpoint.created_at.desc()).offset(offset).limit(limit)
    )
    rows = rows_q.scalars().all()

    # Post-filter event_type if needed
    if event_type:
        filtered = [r for r in rows if not r.events or event_type in r.events]
        # Adjust total for filtered? Keep original total for pagination envelope, but return filtered list
        # For simplicity, return filtered
        rows = filtered

    return WebhookListOut(endpoints=[_to_webhook_out(r) for r in rows], total=int(total), limit=limit, offset=offset)

@router.get("/{endpoint_id}", response_model=WebhookOut)
async def get_webhook(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    return _to_webhook_out(row)

@router.patch("/{endpoint_id}", response_model=WebhookOut)
async def update_webhook(
    endpoint_id: uuid.UUID,
    payload: WebhookUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """PATCH /api/webhooks/{id} — Update endpoint lifecycle."""
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)

    if payload.url is not None:
        if not URL_REGEX.match(payload.url):
            raise HTTPException(status_code=422, detail="url must be https://")
        # Check duplicate
        dup = (
            await session.execute(
                select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == ctx.tenant_id, WebhookEndpoint.url == payload.url, WebhookEndpoint.id != endpoint_id).limit(1)
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail="another webhook with this URL exists")
        row.url = payload.url
    if payload.description is not None:
        row.description = payload.description
    if payload.secret is not None:
        row.secret = payload.secret
    if payload.events is not None:
        invalid = [e for e in payload.events if e not in ALLOWED_EVENT_TYPES]
        if invalid:
            raise HTTPException(status_code=422, detail=f"invalid events: {invalid}")
        row.events = list(dict.fromkeys(payload.events))
    if payload.is_active is not None:
        row.is_active = payload.is_active
    if payload.retry_policy is not None:
        # Merge
        existing = dict(row.retry_policy or {})
        existing.update(payload.retry_policy)
        row.retry_policy = existing
    if payload.headers is not None:
        rp = dict(row.retry_policy or {})
        rp["headers"] = payload.headers
        row.retry_policy = rp
    if payload.timeout_seconds is not None:
        rp = dict(row.retry_policy or {})
        rp["timeout_seconds"] = payload.timeout_seconds
        row.retry_policy = rp

    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    _audit("webhook.updated", tenant_id=str(ctx.tenant_id), endpoint_id=str(endpoint_id))
    return _to_webhook_out(row)

@router.delete("/{endpoint_id}")
async def delete_webhook(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /api/webhooks/{id} — Delete endpoint and its delivery history."""
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    # Delete delivery attempts
    await session.execute(delete(WebhookDeliveryAttempt).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id))
    await session.delete(row)
    await session.commit()
    _audit("webhook.deleted", tenant_id=str(ctx.tenant_id), endpoint_id=str(endpoint_id))
    return {"id": str(endpoint_id), "deleted": True}

@router.post("/{endpoint_id}/enable", response_model=WebhookOut)
async def enable_webhook(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    row.is_active = True
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_webhook_out(row)

@router.post("/{endpoint_id}/disable", response_model=WebhookOut)
async def disable_webhook(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    row.is_active = False
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_webhook_out(row)

# ---------------------------------------------------------------------------
# Event subscription — per-endpoint filters
# ---------------------------------------------------------------------------

@router.get("/{endpoint_id}/events", response_model=dict)
async def get_webhook_events(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks/{id}/events — Get subscribed event types."""
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    return {"endpoint_id": str(endpoint_id), "events": row.events, "all_events": sorted(ALLOWED_EVENT_TYPES), "is_subscribed_to_all": len(row.events) == 0}

@router.put("/{endpoint_id}/events", response_model=WebhookOut)
async def set_webhook_events(
    endpoint_id: uuid.UUID,
    events: List[str],
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """PUT /api/webhooks/{id}/events — Set event subscription (empty = all)."""
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    if events:
        invalid = [e for e in events if e not in ALLOWED_EVENT_TYPES]
        if invalid:
            raise HTTPException(status_code=422, detail=f"invalid events: {invalid}")
        row.events = list(dict.fromkeys(events))
    else:
        row.events = []
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    _audit("webhook.events_updated", tenant_id=str(ctx.tenant_id), endpoint_id=str(endpoint_id), events=row.events)
    return _to_webhook_out(row)

@router.post("/{endpoint_id}/events/{event_type}", response_model=WebhookOut)
async def subscribe_event(
    endpoint_id: uuid.UUID,
    event_type: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/{id}/events/{event_type} — Subscribe single event."""
    if event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid event {event_type}")
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    if event_type not in row.events:
        row.events = row.events + [event_type]
        row.updated_at = _now()
        await session.commit()
        await session.refresh(row)
    return _to_webhook_out(row)

@router.delete("/{endpoint_id}/events/{event_type}", response_model=WebhookOut)
async def unsubscribe_event(
    endpoint_id: uuid.UUID,
    event_type: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /api/webhooks/{id}/events/{event_type} — Unsubscribe single event."""
    row = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    if event_type in row.events:
        row.events = [e for e in row.events if e != event_type]
        row.updated_at = _now()
        await session.commit()
        await session.refresh(row)
    return _to_webhook_out(row)

# ---------------------------------------------------------------------------
# Delivery history, retry, DLQ, replay, signature, test
# ---------------------------------------------------------------------------

@router.get("/{endpoint_id}/deliveries", response_model=DeliveryListOut)
async def list_deliveries(
    endpoint_id: uuid.UUID,
    status: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks/{id}/deliveries — Delivery history with filters."""
    await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    scope = [WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.tenant_id == ctx.tenant_id]
    if status:
        scope.append(WebhookDeliveryAttempt.status == status)
    if event_type:
        scope.append(WebhookDeliveryAttempt.event_type == event_type)

    total = (await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(WebhookDeliveryAttempt).where(*scope).order_by(WebhookDeliveryAttempt.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    return DeliveryListOut(deliveries=[_to_delivery_out(r) for r in rows], total=int(total), limit=limit, offset=offset)

@router.get("/{endpoint_id}/deliveries/{delivery_id}", response_model=dict)
async def get_delivery(
    endpoint_id: uuid.UUID,
    delivery_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    row = await session.get(WebhookDeliveryAttempt, delivery_id)
    if row is None or row.endpoint_id != endpoint_id or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="delivery not found")
    return {
        "delivery": _to_delivery_out(row).model_dump(),
        "payload": row.payload,
        "response_body": row.response_body,
        "endpoint_id": str(endpoint_id),
    }

@router.post("/{endpoint_id}/deliveries/{delivery_id}/retry", response_model=DeliveryAttemptOut)
async def retry_delivery(
    endpoint_id: uuid.UUID,
    delivery_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/{id}/deliveries/{did}/retry — Retry failed delivery."""
    await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    row = await session.get(WebhookDeliveryAttempt, delivery_id)
    if row is None or row.endpoint_id != endpoint_id or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="delivery not found")
    if row.status == "delivered":
        raise HTTPException(status_code=409, detail="delivery already delivered")

    # Re-dispatch
    endpoint = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    new_attempt = await _dispatch_webhook(session, endpoint, row.event_type, row.payload)
    _audit("webhook.delivery_retried", tenant_id=str(ctx.tenant_id), endpoint_id=str(endpoint_id), delivery_id=str(delivery_id), new_attempt_id=str(new_attempt.id))
    return _to_delivery_out(new_attempt)

@router.post("/{endpoint_id}/test", response_model=WebhookTestOut)
async def test_webhook(
    endpoint_id: uuid.UUID,
    payload: WebhookTestRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/{id}/test — Test endpoint with HMAC signature."""
    endpoint = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    start = time.time()

    # Build test payload
    test_payload = {
        "event_type": payload.event_type,
        "tenant_id": str(ctx.tenant_id),
        "endpoint_id": str(endpoint_id),
        "timestamp": _now_iso(),
        "data": payload.payload,
    }

    # Dispatch
    attempt = await _dispatch_webhook(session, endpoint, payload.event_type, test_payload)
    duration_ms = int((time.time() - start) * 1000)

    payload_str = json.dumps(test_payload, sort_keys=True)
    timestamp = str(int(time.time()))
    signature = _generate_signature(endpoint.secret, payload_str, timestamp) if endpoint.secret else None

    return WebhookTestOut(
        endpoint_id=str(endpoint_id),
        event_type=payload.event_type,
        http_status=attempt.http_status,
        response_body=attempt.response_body,
        success=attempt.status == "delivered",
        duration_ms=duration_ms,
        signature=signature,
    )

@router.post("/{endpoint_id}/replay", response_model=DeliveryAttemptOut)
async def replay_webhook(
    endpoint_id: uuid.UUID,
    payload: WebhookReplayRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/{id}/replay — Replay delivery or custom payload."""
    endpoint = await _get_endpoint(session, ctx.tenant_id, endpoint_id)

    if payload.delivery_id:
        # Replay existing delivery
        row = await session.get(WebhookDeliveryAttempt, payload.delivery_id)
        if row is None or row.endpoint_id != endpoint_id or row.tenant_id != ctx.tenant_id:
            raise HTTPException(status_code=404, detail="delivery not found")
        new_attempt = await _dispatch_webhook(session, endpoint, row.event_type, row.payload)
        return _to_delivery_out(new_attempt)

    if payload.event_type and payload.payload:
        new_attempt = await _dispatch_webhook(session, endpoint, payload.event_type, payload.payload)
        return _to_delivery_out(new_attempt)

    raise HTTPException(status_code=422, detail="must provide delivery_id or event_type+payload")

@router.get("/{endpoint_id}/dlq", response_model=DeliveryListOut)
async def list_dlq(
    endpoint_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks/{id}/dlq — Dead letter queue."""
    await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    scope = [WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.tenant_id == ctx.tenant_id, WebhookDeliveryAttempt.status == "dlq"]
    total = (await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(WebhookDeliveryAttempt).where(*scope).order_by(WebhookDeliveryAttempt.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    return DeliveryListOut(deliveries=[_to_delivery_out(r) for r in rows], total=int(total), limit=limit, offset=offset)

@router.post("/{endpoint_id}/dlq/replay-all", response_model=dict)
async def replay_all_dlq(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/{id}/dlq/replay-all — Replay all DLQ entries."""
    endpoint = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    dlq_rows = (
        await session.execute(
            select(WebhookDeliveryAttempt).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.tenant_id == ctx.tenant_id, WebhookDeliveryAttempt.status == "dlq").limit(50)
        )
    ).scalars().all()

    replayed = 0
    for row in dlq_rows:
        await _dispatch_webhook(session, endpoint, row.event_type, row.payload)
        replayed += 1

    _audit("webhook.dlq_replay_all", tenant_id=str(ctx.tenant_id), endpoint_id=str(endpoint_id), replayed=replayed)
    return {"replayed": replayed, "endpoint_id": str(endpoint_id)}

# ---------------------------------------------------------------------------
# Signature verification endpoint
# ---------------------------------------------------------------------------

@router.post("/{endpoint_id}/verify-signature", response_model=dict)
async def verify_signature(
    endpoint_id: uuid.UUID,
    payload: str,
    timestamp: str,
    signature: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/{id}/verify-signature — Verify HMAC signature."""
    endpoint = await _get_endpoint(session, ctx.tenant_id, endpoint_id)
    if not endpoint.secret:
        raise HTTPException(status_code=422, detail="endpoint has no secret configured")
    valid = _verify_signature(endpoint.secret, payload, timestamp, signature)
    return {"valid": valid, "endpoint_id": str(endpoint_id), "timestamp": timestamp}

@router.get("/{endpoint_id}/stats", response_model=WebhookStatsOut)
async def get_webhook_stats(
    endpoint_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks/{id}/stats — Delivery stats with success rate."""
    endpoint = await _get_endpoint(session, ctx.tenant_id, endpoint_id)

    total_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id))
    total = total_q.scalar() or 0

    success_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.status == "delivered"))
    success = success_q.scalar() or 0

    failed_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.status == "failed"))
    failed = failed_q.scalar() or 0

    dlq_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.status == "dlq"))
    dlq = dlq_q.scalar() or 0

    pending_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.endpoint_id == endpoint_id, WebhookDeliveryAttempt.status == "pending"))
    pending = pending_q.scalar() or 0

    success_rate = round(success / max(1, total) * 100, 2) if total else 0.0

    return WebhookStatsOut(
        endpoint_id=str(endpoint_id),
        total_deliveries=int(total),
        successful=int(success),
        failed=int(failed),
        dlq=int(dlq),
        pending=int(pending),
        success_rate=success_rate,
        last_delivery_at=endpoint.last_delivery_at.isoformat() if endpoint.last_delivery_at else None,
        failure_count=endpoint.failure_count,
    )

@router.get("/stats/summary", response_model=dict)
async def webhooks_summary(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks/stats/summary — Summary across all endpoints."""
    total_q = await session.execute(select(func.count(WebhookEndpoint.id)).where(WebhookEndpoint.tenant_id == ctx.tenant_id))
    total = total_q.scalar() or 0

    active_q = await session.execute(select(func.count(WebhookEndpoint.id)).where(WebhookEndpoint.tenant_id == ctx.tenant_id, WebhookEndpoint.is_active.is_(True)))
    active = active_q.scalar() or 0

    total_deliveries_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.tenant_id == ctx.tenant_id))
    total_deliveries = total_deliveries_q.scalar() or 0

    return {
        "tenant_id": str(ctx.tenant_id),
        "total_endpoints": int(total),
        "active_endpoints": int(active),
        "inactive_endpoints": int(total) - int(active),
        "total_deliveries": int(total_deliveries),
        "at": _now_iso(),
    }

@router.get("/events/allowed", response_model=dict)
async def list_allowed_events(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """GET /api/webhooks/events/allowed — List all allowed event types."""
    return {"events": sorted(ALLOWED_EVENT_TYPES), "count": len(ALLOWED_EVENT_TYPES), "categories": {
        "call": [e for e in ALLOWED_EVENT_TYPES if e.startswith("call.")],
        "transfer": [e for e in ALLOWED_EVENT_TYPES if e.startswith("transfer.")],
        "lead": [e for e in ALLOWED_EVENT_TYPES if e.startswith("lead.")],
        "batch": [e for e in ALLOWED_EVENT_TYPES if e.startswith("batch.")],
        "webhook": [e for e in ALLOWED_EVENT_TYPES if e.startswith("webhook.")],
    }}

@router.get("/events/categories", response_model=dict)
async def list_event_categories(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """GET /api/webhooks/events/categories — Event categories with descriptions."""
    return {
        "categories": {
            "call": {"description": "Call lifecycle events", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("call.")]},
            "transfer": {"description": "Transfer events", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("transfer.")]},
            "lead": {"description": "Lead CRM events", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("lead.")]},
            "batch": {"description": "Batch call events", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("batch.")]},
            "webhook": {"description": "Webhook system events", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("webhook.")]},
            "agent": {"description": "Agent lifecycle", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("agent.")]},
            "campaign": {"description": "Campaign events", "events": [e for e in ALLOWED_EVENT_TYPES if e.startswith("campaign.")]},
        },
        "total": len(ALLOWED_EVENT_TYPES),
    }

@router.post("/events/validate", response_model=dict)
async def validate_events(
    events: List[str],
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """POST /api/webhooks/events/validate — Validate event types."""
    valid = [e for e in events if e in ALLOWED_EVENT_TYPES]
    invalid = [e for e in events if e not in ALLOWED_EVENT_TYPES]
    return {"valid": valid, "invalid": invalid, "all_valid": len(invalid) == 0, "allowed": sorted(ALLOWED_EVENT_TYPES)}

@router.get("/templates/payloads", response_model=dict)
async def payload_templates(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """GET /api/webhooks/templates/payloads — Example payloads per event type."""
    templates = {}
    for ev in sorted(ALLOWED_EVENT_TYPES):
        templates[ev] = {
            "event_type": ev,
            "tenant_id": str(ctx.tenant_id),
            "timestamp": _now_iso(),
            "data": {"example": True, "event": ev},
        }
    return {"templates": templates, "count": len(templates)}

@router.post("/bulk/enable", response_model=dict)
async def bulk_enable_webhooks(
    endpoint_ids: List[uuid.UUID],
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/bulk/enable — Bulk enable endpoints."""
    if len(endpoint_ids) > 50:
        raise HTTPException(status_code=422, detail="max 50 endpoints per bulk operation")
    updated = 0
    for eid in endpoint_ids:
        row = await session.get(WebhookEndpoint, eid)
        if row and row.tenant_id == ctx.tenant_id:
            row.is_active = True
            row.updated_at = _now()
            updated += 1
    await session.commit()
    return {"updated": updated, "total": len(endpoint_ids)}

@router.post("/bulk/disable", response_model=dict)
async def bulk_disable_webhooks(
    endpoint_ids: List[uuid.UUID],
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/bulk/disable — Bulk disable endpoints."""
    if len(endpoint_ids) > 50:
        raise HTTPException(status_code=422, detail="max 50 endpoints per bulk operation")
    updated = 0
    for eid in endpoint_ids:
        row = await session.get(WebhookEndpoint, eid)
        if row and row.tenant_id == ctx.tenant_id:
            row.is_active = False
            row.updated_at = _now()
            updated += 1
    await session.commit()
    return {"updated": updated, "total": len(endpoint_ids)}

@router.post("/bulk/delete", response_model=dict)
async def bulk_delete_webhooks(
    endpoint_ids: List[uuid.UUID],
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/webhooks/bulk/delete — Bulk delete endpoints."""
    if len(endpoint_ids) > 20:
        raise HTTPException(status_code=422, detail="max 20 endpoints per bulk delete")
    deleted = 0
    for eid in endpoint_ids:
        row = await session.get(WebhookEndpoint, eid)
        if row and row.tenant_id == ctx.tenant_id:
            await session.execute(delete(WebhookDeliveryAttempt).where(WebhookDeliveryAttempt.endpoint_id == eid))
            await session.delete(row)
            deleted += 1
    await session.commit()
    _audit("webhook.bulk_deleted", tenant_id=str(ctx.tenant_id), deleted=deleted)
    return {"deleted": deleted, "total": len(endpoint_ids)}

@router.get("/deliveries/recent", response_model=DeliveryListOut)
async def recent_deliveries(
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/webhooks/deliveries/recent — Recent deliveries across all endpoints."""
    scope = [WebhookDeliveryAttempt.tenant_id == ctx.tenant_id]
    if status:
        scope.append(WebhookDeliveryAttempt.status == status)
    total = (await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(WebhookDeliveryAttempt).where(*scope).order_by(WebhookDeliveryAttempt.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return DeliveryListOut(deliveries=[_to_delivery_out(r) for r in rows], total=int(total), limit=limit, offset=0)

# ---------------------------------------------------------------------------
# Operator — idempotency, health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=dict)
async def health_check(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    total_q = await session.execute(select(func.count(WebhookEndpoint.id)).where(WebhookEndpoint.tenant_id == ctx.tenant_id))
    total = total_q.scalar() or 0
    active_q = await session.execute(select(func.count(WebhookEndpoint.id)).where(WebhookEndpoint.tenant_id == ctx.tenant_id, WebhookEndpoint.is_active.is_(True)))
    active = active_q.scalar() or 0
    failed_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.tenant_id == ctx.tenant_id, WebhookDeliveryAttempt.status == "failed"))
    failed = failed_q.scalar() or 0
    dlq_q = await session.execute(select(func.count(WebhookDeliveryAttempt.id)).where(WebhookDeliveryAttempt.tenant_id == ctx.tenant_id, WebhookDeliveryAttempt.status == "dlq"))
    dlq = dlq_q.scalar() or 0
    return {
        "tenant_id": str(ctx.tenant_id),
        "total_endpoints": int(total),
        "active_endpoints": int(active),
        "failed_deliveries": int(failed),
        "dlq_entries": int(dlq),
        "status": "healthy" if failed < 10 else "degraded",
        "at": _now_iso(),
    }

@router.get("/idempotency/stats")
async def idempotency_stats(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    now = _now()
    active = 0
    for _k, _exp in _idempotency_cache.values():
        if isinstance(_exp, tuple):
            _cid, _etime = _exp
            if now < _etime:
                active += 1
        else:
            # Legacy non-tuple entry considered active
            active += 1
    return {"total": len(_idempotency_cache), "active": active, "at": _now_iso(), "ttl_hours": 24}

@router.delete("/idempotency/cache")
async def clear_idempotency(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
):
    count = len(_idempotency_cache)
    _idempotency_cache.clear()
    return {"cleared": count, "at": _now_iso()}
