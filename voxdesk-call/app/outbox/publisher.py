"""Same-transaction event publication (Batch 07).

The only supported pattern for durable business events::

    BEGIN
        mutate business row          (the caller's own code)
        publish(session, ...)        (INSERT into outbox_events — flush only)
    COMMIT                           (the caller's commit)

``publish`` never commits and never spawns anything: it inserts the event
row into *the caller's* transaction and flushes. That single fact is the
whole guarantee —

* business transaction rolls back → the outbox row rolls back with it (no
  phantom event is ever delivered for work that did not happen);
* business transaction commits → the outbox row survives every process
  crash afterwards, and the dispatcher will find it (no event is lost
  because a background task died).

The forbidden pattern — commit business state, then ``create_task(...)`` /
``BackgroundTasks.add_task(...)`` and hope — has no path through this
module. Delivery happens later, through the durable job system
(``app.outbox.dispatcher``), at-least-once.

Duplicate publication of the same logical fact is absorbed by the tenant
idempotency key: ``publish`` returns ``(existing_row, False)`` instead of
inserting a second event, whether the duplicate comes from a retried API
call, a second replica or a replayed provider webhook.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.outbox.models import (
    MAX_DELIVERY_ATTEMPTS,
    OutboxEvent,
    OutboxEventStatus,
    validate_event_payload,
)


def default_event_key(
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    event_version: int,
) -> str:
    """Deterministic identity for one logical event.

    A business fact has a deterministic identity, so its key does too: the
    same aggregate, type and version published twice (a retried
    transaction) is one event. Distinct facts differ in at least one
    component — or the caller passes an explicit ``idempotency_key``.
    """
    return f"{aggregate_type}:{aggregate_id}:{event_type}:v{event_version}"[:128]


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


async def publish(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict | None = None,
    aggregate_type: str = "",
    aggregate_id: str = "",
    environment_id: uuid.UUID | None = None,
    event_version: int = 1,
    idempotency_key: str | None = None,
    available_at: datetime | None = None,
    max_attempts: int = MAX_DELIVERY_ATTEMPTS,
) -> tuple[OutboxEvent, bool]:
    """Insert an event into the caller's transaction. Returns (event, created).

    ``tenant_id`` comes from the caller's authenticated context — never from
    an event payload and never from a client-supplied body field; the
    publisher has no parameter through which a payload could claim a
    tenant. The payload is validated (secrets rejected, size bounded)
    *before* the row exists, so a rejected event cannot leave a partial
    write behind.
    """
    if not event_type or not isinstance(event_type, str):
        raise ValueError("event_type must be a non-empty string")
    clean_payload = validate_event_payload(payload)
    key = (idempotency_key or default_event_key(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        event_version=event_version,
    ))[:128]
    event = OutboxEvent(
        tenant_id=tenant_id,
        environment_id=environment_id,
        event_type=event_type[:128],
        event_version=max(1, int(event_version)),
        aggregate_type=aggregate_type[:64],
        aggregate_id=aggregate_id[:128],
        payload=clean_payload,
        status=OutboxEventStatus.PENDING.value,
        max_attempts=max(1, int(max_attempts)),
        available_at=_now(available_at),
        idempotency_key=key,
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.tenant_id == tenant_id,
                    OutboxEvent.idempotency_key == key,
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise
        return found, False
    return event, True
