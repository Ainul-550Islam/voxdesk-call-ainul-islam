"""Transactional outbox package (Batch 07).

The durable pattern this package exists for::

    BEGIN
        mutate business row          (the caller's service code)
        publish(session, ...)        (same transaction — flush only)
    COMMIT
    → dispatcher enqueues a durable ``outbox.delivery`` job per due round
    → the registered handler delivers through ``app.webhooks.delivery``
      (signed, SSRF-guarded) with the ``webhook_deliveries`` ledger
    → success acks the event; transient failures push ``available_at``;
      permanent failures or exhaustion dead-letter it for operator replay

Delivery is at-least-once; consumers deduplicate on the ``X-VoxDesk-Event``
id and ``event_version``. Nothing here keeps authoritative state in process
memory, and importing this package registers the delivery handler with the
job registry (the scheduler entrypoint relies on that import).
"""

from app.outbox.dispatcher import (
    deliver_event,
    dispatch_due,
    event_envelope,
    make_outbox_handler,
    matching_subscriptions,
)
from app.outbox.models import (
    MAX_DELIVERY_ATTEMPTS,
    MAX_EVENT_REPLAYS,
    OutboxEvent,
    OutboxEventStatus,
    OutboxPayloadRejected,
    validate_event_payload,
)
from app.outbox.publisher import default_event_key, publish
from app.outbox.retry import (
    classify_http,
    classify_kind,
    classify_network,
    is_exhausted,
    next_delivery_delay,
    should_retry_delivery,
)

__all__ = [
    "MAX_DELIVERY_ATTEMPTS",
    "MAX_EVENT_REPLAYS",
    "OutboxEvent",
    "OutboxEventStatus",
    "OutboxPayloadRejected",
    "classify_http",
    "classify_kind",
    "classify_network",
    "default_event_key",
    "deliver_event",
    "dispatch_due",
    "event_envelope",
    "is_exhausted",
    "make_outbox_handler",
    "matching_subscriptions",
    "next_delivery_delay",
    "publish",
    "should_retry_delivery",
    "validate_event_payload",
]
