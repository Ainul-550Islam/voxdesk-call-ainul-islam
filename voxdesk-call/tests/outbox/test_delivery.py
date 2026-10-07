"""Transactional-outbox publication, payload safety, and idempotency tests."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.outbox.models import OutboxEvent, OutboxPayloadRejected, validate_event_payload
from app.outbox.publisher import default_event_key, publish


@pytest.mark.asyncio
async def test_publish_and_duplicate(db, tenant_a):
    idempotency_key = f"outbox-test:{uuid.uuid4().hex}"
    event, created = await publish(
        db,
        tenant_id=tenant_a.id,
        event_type="contact.updated",
        aggregate_type="contact",
        aggregate_id=str(uuid.uuid4()),
        payload={"changed_fields": ["name"]},
        idempotency_key=idempotency_key,
    )
    assert created is True
    await db.commit()

    duplicate, created = await publish(
        db,
        tenant_id=tenant_a.id,
        event_type="contact.updated",
        aggregate_type="contact",
        aggregate_id="different-retry-body-is-not-the-identity",
        payload={"changed_fields": ["name"]},
        idempotency_key=idempotency_key,
    )
    assert created is False
    assert duplicate.id == event.id
    assert duplicate.tenant_id == tenant_a.id
    assert duplicate.idempotency_key == idempotency_key

    count = await db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.tenant_id == tenant_a.id,
            OutboxEvent.idempotency_key == idempotency_key,
        )
    )
    assert count == 1


def test_payload_rejects_secret():
    with pytest.raises(OutboxPayloadRejected):
        validate_event_payload({"secret": "must-not-be-persisted"})


def test_default_key_deterministic():
    assert default_event_key(
        aggregate_type="t",
        aggregate_id="i",
        event_type="e",
        event_version=1,
    ) == "t:i:e:v1"
