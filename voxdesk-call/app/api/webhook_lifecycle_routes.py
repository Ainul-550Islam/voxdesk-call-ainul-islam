"""Tenant/environment scoped control plane for the real webhook/outbox engine.

Legacy URL paths remain, but storage is exclusively WebhookSubscription and
WebhookDelivery. Acknowledged deliveries are never blindly resent. Test sends
use the same signed transport and durable ledger as customer lifecycle events.
"""
from __future__ import annotations

import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.exceptions import IdentitySecretsUnavailable
from app.auth.identity.secrets import encryption_available
from app.auth.permissions import Permission
from app.core.rate_limit import rate_limit
from app.core.ssrf import validate_outbound_url
from app.db.models import RequestIdempotencyReceipt, WebhookDelivery, WebhookSubscription
from app.db.session import get_session
from app.outbox.models import OutboxEvent
from app.resilience.idempotency import IdempotencyError, claim_request, complete_request
from app.tenancy.isolation import BoundaryDenied, HierarchyError, to_http
from app.webhooks import repository as repo
from app.webhooks.call_event_catalog import EVENT_NAMES, schema_for
from app.webhooks.delivery import validate_headers
from app.webhooks.replay import replay_delivery
from app.webhooks.signing import verify

router = APIRouter(prefix="/api/webhooks", tags=["webhooks-lifecycle"])
ALLOWED_EVENT_TYPES = EVENT_NAMES | {"webhook.test", "lead.created", "lead.updated", "lead.dnc", "lead.converted"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryPolicy(Strict):
    max_attempts: int = Field(default=8, ge=1, le=10)
    backoff_seconds: int = Field(default=30, ge=1, le=3600)
    max_backoff_seconds: int = Field(default=3600, ge=1, le=3600)


class WebhookCreate(Strict):
    url: str = Field(min_length=10, max_length=500)
    description: str = Field(default="", max_length=500)
    secret: str = Field(default="", max_length=128)
    events: list[str] = Field(default_factory=list, max_length=100)
    is_active: bool = True
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5, ge=0.1, le=60)
    enabled_events_only: bool = False

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        validate_outbound_url(value, require_https=True)
        return value

    @field_validator("events")
    @classmethod
    def valid_events(cls, value):
        if set(value) - ALLOWED_EVENT_TYPES:
            raise ValueError("Unknown event type; use /events/allowed")
        return list(dict.fromkeys(value))

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value):
        validate_headers(value)
        return value

    @field_validator("secret")
    @classmethod
    def valid_secret(cls, value):
        if value and len(value) < 16:
            raise ValueError("Secret must contain at least 16 characters")
        return value


class WebhookUpdate(Strict):
    url: str | None = Field(default=None, min_length=10, max_length=500)
    description: str | None = Field(default=None, max_length=500)
    events: list[str] | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    retry_policy: RetryPolicy | None = None
    headers: dict[str, str] | None = None
    timeout_seconds: float | None = Field(default=None, ge=0.1, le=60)


class Rotation(Strict):
    secret: str | None = Field(default=None, min_length=16, max_length=128)


class TestRequest(Strict):
    event_type: Literal["webhook.test"] = "webhook.test"
    payload: dict = Field(default_factory=lambda: {"test": True})


class ReplayRequest(Strict):
    delivery_id: uuid.UUID


async def _session(session: AsyncSession = Depends(get_session)):
    try:
        yield session
    except HierarchyError as exc:
        await session.rollback()
        raise to_http(exc) from None
    except IdempotencyError as exc:
        await session.rollback()
        raise HTTPException(409, detail={"code": exc.code}) from None
    except IdentitySecretsUnavailable:
        await session.rollback()
        raise HTTPException(501, detail={"code": "NOT_CONFIGURED", "capability": "webhook_encryption"}) from None
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(422, detail=str(exc)) from None


Session = Annotated[AsyncSession, Depends(_session)]
Reader = Annotated[TenantContext, Depends(require_permission(Permission.TENANT_READ))]
Writer = Annotated[TenantContext, Depends(require_permission(Permission.TENANT_UPDATE))]


def _scope(ctx):
    return dict(tenant_id=ctx.tenant_id, environment_id=ctx.environment_id)


def _filter(ctx):
    return (WebhookSubscription.tenant_id == ctx.tenant_id,
            WebhookSubscription.environment_id == ctx.environment_id)


async def _owned(session, ctx, endpoint_id):
    row = await repo.get_subscription(session, **_scope(ctx), subscription_id=endpoint_id)
    if row is None:
        raise BoundaryDenied()
    return row


def _out(row):
    return {"id": str(row.id), "tenant_id": str(row.tenant_id),
            "environment_id": str(row.environment_id) if row.environment_id else None,
            "url": row.endpoint, "description": row.description, "events": row.event_types,
            "is_active": row.enabled, "retry_policy": row.delivery_options,
            "timeout_seconds": (row.delivery_options or {}).get("timeout_seconds", 5),
            "headers": {key: "***" for key in repo.subscription_headers(row)},
            "secret_version": row.secret_version, "created_at": row.created_at,
            "updated_at": row.updated_at}


def _delivery(row):
    return {"id": str(row.id), "endpoint_id": str(row.subscription_id),
            "event_id": row.event_id, "status": row.status, "http_status": row.http_status,
            "attempts": row.attempt, "attempt_limit": row.attempt_limit,
            "next_retry_at": row.next_attempt_at, "created_at": row.created_at,
            "delivered_at": row.completed_at if row.status == "succeeded" else None,
            "latency_ms": row.latency_ms, "last_error_category": row.last_error_category,
            "is_dlq": row.status in {"dead_letter", "permanent_failure", "failed"},
            "replay_count": row.replay_count}


async def _limit(ctx):
    try:
        allowed = await rate_limit(f"voxdesk:webhooks:{ctx.tenant_id}:{ctx.environment_id}", 100, 60)
    except RuntimeError:
        raise HTTPException(501, detail={"code": "NOT_CONFIGURED", "capability": "redis_rate_limit"}) from None
    if not allowed:
        raise HTTPException(429, detail="Webhook write rate exceeded or backend unavailable", headers={"Retry-After": "60"})


async def _audit(session, ctx, action, resource, *, result="success"):
    await record_event(session, **_scope(ctx), actor_user_id=ctx.user_id,
                       event_type=action, resource_type="webhook", resource_id=resource, result=result)


async def _finish(session, ctx, action, resource):
    await _audit(session, ctx, action, resource)
    await session.commit()


async def _claim(session, ctx, request, operation, data):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    return await claim_request(session, **_scope(ctx), operation=operation, key=key,
                               request_data={"path": request.url.path, "body": data})


async def _complete(session, claim, resource, resource_type="webhook_subscription"):
    if claim:
        await complete_request(session, claim.receipt, resource_type=resource_type, resource_id=resource)


@router.post("", status_code=201)
async def create_webhook(payload: WebhookCreate, request: Request, ctx: Writer, session: Session):
    await _limit(ctx)
    if not encryption_available():
        raise IdentitySecretsUnavailable("Webhook encryption is not configured")
    if payload.enabled_events_only and not payload.events:
        raise HTTPException(422, detail="enabled_events_only requires a nonempty filter")
    options = repo.validate_options({**payload.retry_policy.model_dump(), "timeout_seconds": payload.timeout_seconds})
    claim = await _claim(session, ctx, request, "webhook.create", payload.model_dump())
    if claim and claim.replayed:
        return _out(await _owned(session, ctx, uuid.UUID(claim.receipt.resource_id)))
    secret = payload.secret or secrets.token_urlsafe(32)
    row = await repo.create_subscription(session, **_scope(ctx), endpoint=payload.url,
                                         secret=secret, event_types=payload.events)
    row.description = payload.description
    row.enabled = payload.is_active
    row.delivery_options = options
    import json
    row.headers_envelope = repo.seal_secret(ctx.tenant_id, json.dumps(payload.headers))
    await _complete(session, claim, row.id)
    await _finish(session, ctx, "webhook.created", row.id)
    # Secrets are obtained through the explicit rotation operation, not lists.
    return _out(row)


@router.get("")
async def list_webhooks(ctx: Reader, session: Session, limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0), is_active: bool | None = None,
                        event_type: str | None = None, search: str | None = Query(None, max_length=200)):
    query = select(WebhookSubscription).where(*_filter(ctx))
    if is_active is not None:
        query = query.where(WebhookSubscription.enabled == is_active)
    if search:
        query = query.where(WebhookSubscription.endpoint.icontains(search, autoescape=True))
    # Filters must run before pagination; totals must describe the filtered set.
    rows = list((await session.scalars(query.order_by(WebhookSubscription.created_at, WebhookSubscription.id))).all())
    if event_type:
        rows = [row for row in rows if not row.event_types or event_type in row.event_types]
    return {"endpoints": [_out(row) for row in rows[offset:offset + limit]],
            "total": len(rows), "limit": limit, "offset": offset}


@router.get("/{endpoint_id:uuid}")
async def get_webhook(endpoint_id: uuid.UUID, ctx: Reader, session: Session):
    return _out(await _owned(session, ctx, endpoint_id))


@router.patch("/{endpoint_id:uuid}")
async def update_webhook(endpoint_id: uuid.UUID, payload: WebhookUpdate, ctx: Writer, session: Session):
    await _limit(ctx)
    row = await _owned(session, ctx, endpoint_id)
    if payload.events is not None:
        WebhookCreate.valid_events(payload.events)
    options = {**(row.delivery_options or {})}
    if payload.retry_policy is not None:
        options.update(payload.retry_policy.model_dump())
    if payload.timeout_seconds is not None:
        options["timeout_seconds"] = payload.timeout_seconds
    row = await repo.update_subscription(session, **_scope(ctx), subscription_id=endpoint_id,
        endpoint=payload.url, enabled=payload.is_active, event_types=payload.events,
        description=payload.description, delivery_options=options, headers=payload.headers)
    await _finish(session, ctx, "webhook.updated", row.id)
    return _out(row)


@router.delete("/{endpoint_id:uuid}")
async def delete_webhook(endpoint_id: uuid.UUID, ctx: Writer, session: Session):
    await _limit(ctx)
    await repo.delete_subscription(session, **_scope(ctx), subscription_id=endpoint_id)
    await _finish(session, ctx, "webhook.deleted", endpoint_id)
    return {"id": str(endpoint_id), "deleted": True}


@router.post("/{endpoint_id:uuid}/enable")
async def enable_webhook(endpoint_id: uuid.UUID, ctx: Writer, session: Session):
    return await update_webhook(endpoint_id, WebhookUpdate(is_active=True), ctx, session)


@router.post("/{endpoint_id:uuid}/disable")
async def disable_webhook(endpoint_id: uuid.UUID, ctx: Writer, session: Session):
    return await update_webhook(endpoint_id, WebhookUpdate(is_active=False), ctx, session)


@router.post("/{endpoint_id:uuid}/rotate-secret")
async def rotate_secret(endpoint_id: uuid.UUID, payload: Rotation, request: Request, ctx: Writer, session: Session):
    await _limit(ctx)
    row = await _owned(session, ctx, endpoint_id)
    if not encryption_available():
        raise IdentitySecretsUnavailable("Webhook encryption is not configured")
    claim = await _claim(session, ctx, request, "webhook.rotate", payload.model_dump())
    if claim and claim.replayed:
        if claim.receipt.resource_id != f"{row.id}:{row.secret_version}":
            raise HTTPException(409, detail="This rotation has been superseded")
    else:
        row = await repo.rotate_secret(session, **_scope(ctx), subscription_id=endpoint_id,
                                       secret=payload.secret or secrets.token_urlsafe(32))
        await _complete(session, claim, f"{row.id}:{row.secret_version}", "webhook_secret")
        await _finish(session, ctx, "webhook.secret_rotated", row.id)
    return {"id": str(row.id), "secret_version": row.secret_version,
            "secret": repo.open_secret(row.tenant_id, row.secret_envelope)}


@router.get("/{endpoint_id:uuid}/events")
async def get_webhook_events(endpoint_id: uuid.UUID, ctx: Reader, session: Session):
    row = await _owned(session, ctx, endpoint_id)
    return {"endpoint_id": str(row.id), "events": row.event_types,
            "all_events": sorted(ALLOWED_EVENT_TYPES), "is_subscribed_to_all": not row.event_types}


@router.put("/{endpoint_id:uuid}/events")
async def set_webhook_events(endpoint_id: uuid.UUID, events: list[str], ctx: Writer, session: Session):
    return await update_webhook(endpoint_id, WebhookUpdate(events=events), ctx, session)


@router.post("/{endpoint_id:uuid}/events/{event_type}")
async def subscribe_event(endpoint_id: uuid.UUID, event_type: str, ctx: Writer, session: Session):
    row = await _owned(session, ctx, endpoint_id)
    return await set_webhook_events(endpoint_id, list(dict.fromkeys([*row.event_types, event_type])), ctx, session)


@router.delete("/{endpoint_id:uuid}/events/{event_type}")
async def unsubscribe_event(endpoint_id: uuid.UUID, event_type: str, ctx: Writer, session: Session):
    row = await _owned(session, ctx, endpoint_id)
    # Empty filter means all, so expanding it first prevents an accidental re-subscribe.
    wanted = set(row.event_types) if row.event_types else set(ALLOWED_EVENT_TYPES)
    wanted.discard(event_type)
    if not wanted:
        return await update_webhook(endpoint_id, WebhookUpdate(is_active=False), ctx, session)
    return await set_webhook_events(endpoint_id, sorted(wanted), ctx, session)


async def _owned_delivery(session, ctx, endpoint_id, delivery_id):
    await _owned(session, ctx, endpoint_id)
    row = await session.scalar(select(WebhookDelivery).where(
        WebhookDelivery.id == delivery_id, WebhookDelivery.subscription_id == endpoint_id,
        WebhookDelivery.tenant_id == ctx.tenant_id))
    if row is None:
        raise BoundaryDenied()
    return row


async def _deliveries(session, ctx, endpoint_id=None, *, status=None, event_type=None, dlq=False, limit=50, offset=0):
    if endpoint_id:
        await _owned(session, ctx, endpoint_id)
    query = select(WebhookDelivery).join(WebhookSubscription).where(*_filter(ctx))
    if endpoint_id:
        query = query.where(WebhookDelivery.subscription_id == endpoint_id)
    if dlq:
        query = query.where(WebhookDelivery.status.in_(["dead_letter", "failed", "permanent_failure"]))
    elif status:
        query = query.where(WebhookDelivery.status == status)
    if event_type:
        # Portable UUID-to-string matching, without database-specific JSON operators.
        from sqlalchemy import String, cast
        query = query.join(OutboxEvent, WebhookDelivery.event_id == cast(OutboxEvent.id, String)).where(
            OutboxEvent.tenant_id == ctx.tenant_id, OutboxEvent.event_type == event_type)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await session.scalars(query.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id).limit(limit).offset(offset))).all()
    return {"deliveries": [_delivery(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/{endpoint_id:uuid}/deliveries")
async def list_deliveries(endpoint_id: uuid.UUID, ctx: Reader, session: Session,
                         status: str | None = None, event_type: str | None = None,
                         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await _deliveries(session, ctx, endpoint_id, status=status, event_type=event_type, limit=limit, offset=offset)


@router.get("/{endpoint_id:uuid}/deliveries/{delivery_id:uuid}")
async def get_delivery(endpoint_id: uuid.UUID, delivery_id: uuid.UUID, ctx: Reader, session: Session):
    row = await _owned_delivery(session, ctx, endpoint_id, delivery_id)
    return {"delivery": _delivery(row), "response_body": repo.open_secret(row.tenant_id, row.response_envelope) if row.response_envelope else ""}


@router.post("/{endpoint_id:uuid}/test")
async def test_webhook(endpoint_id: uuid.UUID, payload: TestRequest, request: Request, ctx: Writer, session: Session):
    await _limit(ctx)
    row = await _owned(session, ctx, endpoint_id)
    claim = await _claim(session, ctx, request, "webhook.test", payload.model_dump())
    if claim and claim.replayed:
        delivery = await _owned_delivery(session, ctx, endpoint_id, uuid.UUID(claim.receipt.resource_id))
    else:
        await _audit(session, ctx, "webhook.test_requested", endpoint_id, result="pending")
        delivery = await repo.send_test(session, row, payload.payload)
        await _complete(session, claim, delivery.id, "webhook_delivery")
        await _audit(session, ctx, "webhook.test_observed", delivery.id,
                     result="success" if delivery.status == "succeeded" else "failure")
        await session.commit()
    return {**_delivery(delivery), "success": delivery.status == "succeeded",
            "response_body": repo.open_secret(ctx.tenant_id, delivery.response_envelope) if delivery.response_envelope else "",
            "duration_ms": delivery.latency_ms, "event_type": "webhook.test"}


@router.post("/{endpoint_id:uuid}/deliveries/{delivery_id:uuid}/retry")
async def retry_delivery(endpoint_id: uuid.UUID, delivery_id: uuid.UUID, ctx: Writer, session: Session):
    await _limit(ctx)
    await _owned_delivery(session, ctx, endpoint_id, delivery_id)
    row = await replay_delivery(session, tenant_id=ctx.tenant_id, subscription_id=endpoint_id,
                                delivery_id=delivery_id, role=ctx.user.role if ctx.user else None)
    await _finish(session, ctx, "webhook.replayed", delivery_id)
    return _delivery(row)


@router.post("/{endpoint_id:uuid}/replay")
async def replay_webhook(endpoint_id: uuid.UUID, payload: ReplayRequest, ctx: Writer, session: Session):
    return await retry_delivery(endpoint_id, payload.delivery_id, ctx, session)


@router.get("/{endpoint_id:uuid}/dlq")
async def list_dlq(endpoint_id: uuid.UUID, ctx: Reader, session: Session,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await _deliveries(session, ctx, endpoint_id, dlq=True, limit=limit, offset=offset)


@router.post("/{endpoint_id:uuid}/dlq/replay-all")
async def replay_all_dlq(endpoint_id: uuid.UUID, ctx: Writer, session: Session):
    await _limit(ctx)
    page = await _deliveries(session, ctx, endpoint_id, dlq=True, limit=100)
    for item in page["deliveries"]:
        await replay_delivery(session, tenant_id=ctx.tenant_id, subscription_id=endpoint_id,
                              delivery_id=uuid.UUID(item["id"]), role=ctx.user.role if ctx.user else None)
        await _audit(session, ctx, "webhook.replayed", item["id"], result="pending")
    await session.commit()
    return {"queued": len(page["deliveries"]), "remaining": max(0, page["total"] - 100)}


@router.post("/{endpoint_id:uuid}/verify-signature")
async def verify_signature(endpoint_id: uuid.UUID, payload: str, timestamp: str, signature: str, ctx: Reader, session: Session):
    row = await _owned(session, ctx, endpoint_id)
    return {"valid": verify(repo.open_secret(ctx.tenant_id, row.secret_envelope), payload.encode(),
                            f"t={timestamp},v1={signature}"), "endpoint_id": str(endpoint_id)}


@router.get("/{endpoint_id:uuid}/stats")
async def get_webhook_stats(endpoint_id: uuid.UUID, ctx: Reader, session: Session):
    await _owned(session, ctx, endpoint_id)
    counts = dict((await session.execute(select(WebhookDelivery.status, func.count()).where(
        WebhookDelivery.tenant_id == ctx.tenant_id, WebhookDelivery.subscription_id == endpoint_id,
    ).group_by(WebhookDelivery.status))).all())
    total = sum(counts.values())
    return {"endpoint_id": str(endpoint_id), "total_deliveries": total, "by_status": counts,
            "success_rate": 100 * counts.get("succeeded", 0) / total if total else None}


@router.get("/health")
@router.get("/stats/summary")
async def webhooks_summary(ctx: Reader, session: Session):
    total = await session.scalar(select(func.count()).select_from(WebhookSubscription).where(*_filter(ctx)))
    active = await session.scalar(select(func.count()).select_from(WebhookSubscription).where(*_filter(ctx), WebhookSubscription.enabled.is_(True)))
    deliveries = await _deliveries(session, ctx, limit=1)
    return {"total_endpoints": total, "active_endpoints": active, "total_deliveries": deliveries["total"],
            "status": "not_probed"}


@router.get("/events/allowed")
async def list_allowed_events(ctx: Reader):
    return {"events": sorted(ALLOWED_EVENT_TYPES), "count": len(ALLOWED_EVENT_TYPES)}


@router.get("/events/categories")
async def list_event_categories(ctx: Reader):
    return {"categories": {category: sorted(event for event in ALLOWED_EVENT_TYPES if event.startswith(category))
                           for category in ["call", "transfer", "voicemail", "dtmf", "recording", "batch", "agent", "lead", "webhook"]}}


@router.post("/events/validate")
async def validate_events(events: list[str], ctx: Reader):
    invalid = sorted(set(events) - ALLOWED_EVENT_TYPES)
    return {"valid": sorted(set(events) & ALLOWED_EVENT_TYPES), "invalid": invalid, "all_valid": not invalid}


@router.get("/templates/payloads")
async def payload_templates(ctx: Reader):
    return {"schemas": {event: schema_for(event) for event in sorted(EVENT_NAMES)}, "event_version": 1}


async def _bulk(endpoint_ids, ctx, session, action):
    await _limit(ctx)
    if len(endpoint_ids) > 50:
        raise HTTPException(422, detail="At most 50 endpoints per operation")
    for endpoint_id in dict.fromkeys(endpoint_ids):
        if action == "delete":
            await repo.delete_subscription(session, **_scope(ctx), subscription_id=endpoint_id)
        else:
            await repo.update_subscription(session, **_scope(ctx), subscription_id=endpoint_id, enabled=action == "enable")
        await _audit(session, ctx, f"webhook.{action}", endpoint_id)
    await session.commit()
    return {"changed": len(set(endpoint_ids))}


@router.post("/bulk/enable")
async def bulk_enable_webhooks(endpoint_ids: list[uuid.UUID], ctx: Writer, session: Session):
    return await _bulk(endpoint_ids, ctx, session, "enable")


@router.post("/bulk/disable")
async def bulk_disable_webhooks(endpoint_ids: list[uuid.UUID], ctx: Writer, session: Session):
    return await _bulk(endpoint_ids, ctx, session, "disable")


@router.post("/bulk/delete")
async def bulk_delete_webhooks(endpoint_ids: list[uuid.UUID], ctx: Writer, session: Session):
    return await _bulk(endpoint_ids, ctx, session, "delete")


@router.get("/deliveries/recent")
async def recent_deliveries(ctx: Reader, session: Session, limit: int = Query(50, ge=1, le=200), status: str | None = None):
    return await _deliveries(session, ctx, status=status, limit=limit)


@router.get("/idempotency/stats")
async def idempotency_stats(ctx: Reader, session: Session):
    counts = dict((await session.execute(select(RequestIdempotencyReceipt.status, func.count()).where(
        RequestIdempotencyReceipt.tenant_id == ctx.tenant_id,
        RequestIdempotencyReceipt.environment_id == ctx.environment_id,
        RequestIdempotencyReceipt.operation.startswith("webhook."),
    ).group_by(RequestIdempotencyReceipt.status))).all())
    return {"total": sum(counts.values()), "by_status": counts, "storage": "database"}


@router.delete("/idempotency/cache")
async def clear_idempotency(ctx: Writer, session: Session):
    # Clearing durable receipts would admit duplicate external effects. A 409
    # (permanent rule) rather than 501: nothing an operator configures makes
    # this endpoint's answer change, and a 501 would read as "not yet".
    raise HTTPException(
        409,
        detail={
            "code": "UNSUPPORTED_CAPABILITY",
            "capability": "idempotency_cache_clear",
            "message": "Durable receipts cannot be cleared as a cache; deleting them would admit duplicate external effects.",
        },
    )
