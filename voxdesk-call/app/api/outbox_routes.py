"""Authorized operator APIs for the transactional outbox (Batch 07).

Inspection is ``INTEGRATION_READ``; cancellation and the test event are
``INTEGRATION_WRITE`` (they own outbound integration behaviour); replay is
``CAMPAIGN_RUN`` — the same operator permission the existing webhook replay
(``app.webhooks.replay``) and job replay already require, so every replay
surface in the product answers to one rule.

Impersonation is structurally impossible here:

* tenant identity comes from the authenticated ``TenantContext`` only — no
  route has a tenant parameter, and the request models are strict
  (``extra="forbid"``), so a body field like ``tenant_id`` is a 422, not a
  scope;
* the test endpoint publishes a fixed ``system.test`` event for the calling
  tenant — clients cannot inject arbitrary internal event types, aggregate
  identities or environments through it;
* another tenant's event does not exist: every lookup is tenant-scoped and
  misses render as 404 with no detail.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.logging import log
from app.db.models import WebhookDelivery
from app.db.session import get_session
from app.outbox import publisher
from app.outbox.models import (
    MAX_EVENT_REPLAYS,
    OutboxEvent,
    OutboxEventStatus,
)
from app.tenancy.isolation import BoundaryDenied, HierarchyError, LifecycleDenied, to_http

router = APIRouter(prefix="/api/outbox", tags=["outbox"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventOut(_Strict):
    id: uuid.UUID
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None
    event_type: str
    event_version: int
    aggregate_type: str
    aggregate_id: str
    payload: dict
    status: str
    attempt_count: int
    max_attempts: int
    replay_count: int
    available_at: object
    delivered_at: object | None
    last_error_category: str
    last_error: str
    idempotency_key: str
    created_at: object
    updated_at: object


class DeliveryOut(_Strict):
    id: uuid.UUID
    subscription_id: uuid.UUID
    event_id: str
    attempt: int
    status: str
    next_attempt_at: object | None
    completed_at: object | None
    last_error_category: str
    replay_count: int


class TestEventIn(_Strict):
    """Body of ``POST /api/outbox/test`` — a note, and nothing else.

    Every identity field (tenant, event type, aggregate) is server-fixed;
    there is deliberately no parameter through which a client could publish
    an internal event type or speak for another tenant.
    """

    note: str = Field("", max_length=200)


def _event_out(event: OutboxEvent) -> dict:
    return EventOut.model_validate(event, from_attributes=True).model_dump(mode="json")


@router.get("")
async def list_events(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None, pattern="^(pending|delivered|dead_letter|cancelled)$"),
    event_type: str | None = Query(None, max_length=128),
    aggregate_type: str | None = Query(None, max_length=64),
    aggregate_id: str | None = Query(None, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Tenant-scoped event listing, newest first. Read-only."""
    stmt = select(OutboxEvent).where(OutboxEvent.tenant_id == ctx.tenant_id)
    if status:
        stmt = stmt.where(OutboxEvent.status == status)
    if event_type:
        stmt = stmt.where(OutboxEvent.event_type == event_type)
    if aggregate_type:
        stmt = stmt.where(OutboxEvent.aggregate_type == aggregate_type)
    if aggregate_id:
        stmt = stmt.where(OutboxEvent.aggregate_id == aggregate_id)
    stmt = (
        stmt.order_by(OutboxEvent.created_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .offset(max(0, int(offset)))
    )
    events = list((await session.execute(stmt)).scalars())
    return {"events": [_event_out(event) for event in events], "count": len(events)}


@router.get("/{event_id}")
async def get_event(
    event_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    """One event with its per-subscription delivery ledger. 404 cross-tenant."""
    event = await session.get(OutboxEvent, event_id)
    if event is None or event.tenant_id != ctx.tenant_id:
        raise to_http(BoundaryDenied())
    deliveries = list(
        (
            await session.execute(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.tenant_id == ctx.tenant_id,
                    WebhookDelivery.event_id == str(event_id),
                )
                .order_by(WebhookDelivery.id)
            )
        ).scalars()
    )
    return {
        "event": _event_out(event),
        "deliveries": [
            DeliveryOut.model_validate(row, from_attributes=True).model_dump(mode="json")
            for row in deliveries
        ],
    }


@router.post("/{event_id}/replay")
async def replay_event(
    event_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Replay a dead-lettered event: guarded, budgeted, scope-preserving.

    One conditional UPDATE performs the whole transition (status, budget and
    tenant checked in the WHERE clause), so two operators replaying at once
    produce exactly one new dispatch round. The event keeps its tenant and
    environment; already-delivered subscriptions are skipped by the ledger
    on the new round, and the history (attempts, replay count) is preserved
    — nothing is rewritten into a false success.
    """
    moment = datetime.now(timezone.utc)
    result = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.tenant_id == ctx.tenant_id,
            OutboxEvent.status == OutboxEventStatus.DEAD_LETTER.value,
            OutboxEvent.replay_count < MAX_EVENT_REPLAYS,
        )
        .values(
            status=OutboxEventStatus.PENDING.value,
            replay_count=OutboxEvent.replay_count + 1,
            max_attempts=OutboxEvent.attempt_count + 3,
            available_at=moment,
            delivered_at=None,
        ), execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        event = await session.get(OutboxEvent, event_id)
        if event is None or event.tenant_id != ctx.tenant_id:
            raise to_http(BoundaryDenied())
        if event.status != OutboxEventStatus.DEAD_LETTER.value:
            raise to_http(LifecycleDenied("only a dead-letter event can be replayed"))
        raise to_http(LifecycleDenied("replay budget exhausted"))
    await session.flush()
    event = await session.get(OutboxEvent, event_id)
    await session.refresh(event)
    await session.commit()
    log.info(
        "outbox.replayed",
        event_id=str(event_id),
        tenant_id=str(ctx.tenant_id),
        replay_count=event.replay_count,
    )
    return {"event": _event_out(event)}


@router.post("/{event_id}/cancel")
async def cancel_event(
    event_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Durable cancellation of a pending event.

    Guarded on ``status='pending'``: a delivery round already in flight
    re-checks the status before sending, so the cancel wins the race, and a
    terminal event reports its state instead of being mutated.
    """
    result = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.tenant_id == ctx.tenant_id,
            OutboxEvent.status == OutboxEventStatus.PENDING.value,
        )
        .values(status=OutboxEventStatus.CANCELLED.value), execution_options={"synchronize_session": False})
    if result.rowcount == 1:
        await session.commit()
        return {"event_id": str(event_id), "status": OutboxEventStatus.CANCELLED.value}
    event = await session.get(OutboxEvent, event_id)
    if event is None or event.tenant_id != ctx.tenant_id:
        raise to_http(BoundaryDenied())
    await session.commit()
    return {"event_id": str(event_id), "status": f"already:{event.status}"}


@router.post("/test")
async def publish_test_event(
    payload: TestEventIn,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Publish a ``system.test`` event for the calling tenant, durably.

    The event is a real outbox row: it goes through the same publisher
    validation (bounded, secret-free), the same dispatcher and the same
    signed delivery as any business event — which is exactly what makes it
    a truthful endpoint test. Its idempotency key is unique per call, so
    repeated tests do not collapse into one event.
    """
    try:
        event, created = await publisher.publish(
            session,
            tenant_id=ctx.tenant_id,
            event_type="system.test",
            aggregate_type="tenant",
            aggregate_id=str(ctx.tenant_id),
            payload={"note": payload.note},
            idempotency_key=f"system-test:{uuid.uuid4().hex}"[:128],
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    await session.commit()
    return {"event": _event_out(event), "created": created}
