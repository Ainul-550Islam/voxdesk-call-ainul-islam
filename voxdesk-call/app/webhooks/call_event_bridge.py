"""Call facts published through the existing transactional outbox.

No new queue or delivery engine. The ordinary outbox dispatcher applies tenant,
optional environment, enabled and event-type subscription filters, then uses
signed HTTP delivery and its durable per-subscription delivery ledger.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Call
from app.outbox.publisher import publish
from app.webhooks.call_event_catalog import CALL_EVENTS, VERSION, validate_payload


async def publish_call_event(
    session: AsyncSession, call: Call, event: str, extra: dict | None = None,
):
    """Flush a logical fact, without committing the caller's transaction.

    Scope comes exclusively from the persisted call, never from extra fields.
    A repeated logical event uses the existing database unique constraint.
    This version defines one fact of each type per call (including DTMF and
    transfers); occurrence-by-occurrence streams need a separate versioned key.
    """
    if event not in CALL_EVENTS:
        raise ValueError("This publisher accepts only call events")
    if call.id is None or call.tenant_id is None:
        raise ValueError("Call must be flushed before event publication")
    payload = {
        "call_id": str(call.id),
        "status": call.status.value,
        "direction": call.direction.value,
        "duration_seconds": float(call.duration_seconds or 0),
    }
    if extra:
        if payload.keys() & extra.keys():
            raise ValueError("Extra fields cannot override call identity or state")
        payload.update(extra)
    validate_payload(event, payload)
    result = await publish(
        session,
        tenant_id=call.tenant_id,
        environment_id=call.environment_id,
        event_type=event,
        event_version=VERSION,
        aggregate_type="call",
        aggregate_id=str(call.id),
        idempotency_key=f"call:{call.id}:{event}:v{VERSION}",
        payload=payload,
    )
    if event == "call_ended":
        # The shared bridge also covers confirmed API ends and reconciliation,
        # not just one provider callback. Both writes use this same session.
        from app.telephony.post_call import enqueue_post_call
        await enqueue_post_call(session, call)
    return result
