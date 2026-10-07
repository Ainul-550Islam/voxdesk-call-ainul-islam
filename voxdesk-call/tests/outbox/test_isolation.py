"""Tenant, environment, secret, and size boundaries for outbox events."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.outbox_routes import (
    TestEventIn as OutboxTestEventPayload,
    get_event,
    list_events,
    publish_test_event,
)
from app.outbox.models import OutboxPayloadRejected, validate_event_payload
from app.outbox.publisher import publish


@pytest.mark.asyncio
async def test_tenant_isolation_not_client_controlled(db, tenant_a, tenant_b):
    with pytest.raises(ValidationError):
        OutboxTestEventPayload.model_validate(
            {"note": "test", "tenant_id": str(tenant_b.id)}
        )

    own_result = await publish_test_event(
        payload=OutboxTestEventPayload(note="tenant A test"),
        ctx=SimpleNamespace(tenant_id=tenant_a.id),
        session=db,
    )
    assert own_result["created"] is True
    assert own_result["event"]["tenant_id"] == str(tenant_a.id)

    foreign_event, created = await publish(
        db,
        tenant_id=tenant_b.id,
        event_type="isolation.test",
        aggregate_type="test",
        aggregate_id=str(uuid.uuid4()),
        payload={"note": "tenant B only"},
        idempotency_key=f"tenant-b:{uuid.uuid4().hex}",
    )
    assert created is True
    await db.commit()

    own_listing = await list_events(
        ctx=SimpleNamespace(tenant_id=tenant_a.id),
        session=db,
        status=None,
        event_type=None,
        aggregate_type=None,
        aggregate_id=None,
        limit=50,
        offset=0,
    )
    own_ids = {row["id"] for row in own_listing["events"]}
    assert own_result["event"]["id"] in own_ids
    assert str(foreign_event.id) not in own_ids
    assert all(row["tenant_id"] == str(tenant_a.id) for row in own_listing["events"])

    with pytest.raises(HTTPException) as cross_tenant:
        await get_event(
            foreign_event.id,
            ctx=SimpleNamespace(tenant_id=tenant_a.id),
            session=db,
        )
    with pytest.raises(HTTPException) as missing:
        await get_event(
            uuid.uuid4(),
            ctx=SimpleNamespace(tenant_id=tenant_a.id),
            session=db,
        )
    assert cross_tenant.value.status_code == missing.value.status_code == 404
    assert cross_tenant.value.detail == missing.value.detail == {
        "code": "not_found",
        "message": "Not found",
    }


def test_payload_rejects_secrets():
    with pytest.raises(OutboxPayloadRejected):
        validate_event_payload({"api_key": "must-not-be-persisted"})


def test_payload_bounded():
    big = {"x": "y" * 30_000}
    with pytest.raises(OutboxPayloadRejected):
        validate_event_payload(big)
