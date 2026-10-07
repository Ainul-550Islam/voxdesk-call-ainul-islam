"""Delivery idempotency for outbox events (Batch 07).

No second ledger: the existing ``webhook_deliveries`` table — unique on
``(subscription_id, event_id)`` — already records one delivery intent per
endpoint per event, and this module is the thin outbox-side contract around
it. Three layers keep delivery safely repeatable:

1. **Publication** dedupes on the tenant-unique event idempotency key
   (``publisher.default_event_key``), so one business fact is one event row.
2. **Per-endpoint attempts** dedupe through ``begin_delivery`` →
   ``webhooks.repository.create_delivery``: a retry, a replay or a duplicate
   dispatch finds the existing ledger row instead of inserting a second one,
   and ``already_delivered`` makes the dispatcher skip endpoints that
   already acknowledged. That is the guard behind "a timeout *after*
   provider acceptance must not blindly create duplicate business effects":
   the endpoint answered 2xx once, the ledger says ``succeeded``, and no
   later attempt re-sends to it.
3. **Consumers** get the deduplication key in the request itself:
   ``X-VoxDesk-Event: <event_id>`` (set by ``app.webhooks.delivery``) plus
   ``event_version`` in the signed body, so a receiver that sees the same
   event twice — which at-least-once delivery permits — can drop the
   duplicate. Exactly-once is *not* claimed anywhere in this batch; it is
   the consumer's contract to be idempotent on ``event_id``.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WebhookDelivery
from app.outbox.publisher import default_event_key
from app.webhooks.repository import create_delivery

#: One delivery round of one event (attempt 0 = first). Matches the job
#: idempotency key the dispatcher enqueues with, so a repeated dispatch tick
#: finds the same job row instead of making a second one.
def delivery_job_key(event_id: uuid.UUID | str, *, attempt: int) -> str:
    return f"outbox-delivery:{event_id}:{attempt}"[:128]


def event_reference(event_id: uuid.UUID | str) -> str:
    """The consumer-facing deduplication reference (``X-VoxDesk-Event``)."""
    return str(event_id)


async def begin_delivery(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: uuid.UUID | str,
    environment_id: uuid.UUID | None = None,
) -> tuple[WebhookDelivery, bool]:
    """Open (or find) the per-subscription delivery ledger row for an event.

    Returns ``(delivery, created)``. The unique constraint does the racing:
    two concurrent dispatchers get the same row, and only one inserts.
    """
    return await create_delivery(
        session,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        event_id=event_reference(event_id),
        environment_id=environment_id,
    )


def already_delivered(delivery: WebhookDelivery) -> bool:
    """Has this endpoint already acknowledged this event? Then never resend."""
    return delivery.status == "succeeded"


__all__ = [
    "already_delivered",
    "begin_delivery",
    "default_event_key",
    "delivery_job_key",
    "event_reference",
]
