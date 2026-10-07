"""Database-backed request idempotency and tenant/environment isolation."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Environment, RequestIdempotencyReceipt
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyScopeError,
    RequestClaim,
    claim_request,
    complete_request,
)

pytestmark = pytest.mark.asyncio


async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def test_receipt_rejects_in_progress_retry_and_replays_success(db, tenant_a):
    environment = await _production(db, tenant_a)
    first = await claim_request(
        db,
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        operation="telephony.call.outbound",
        key="call-intent-0001",
        request_data={"to": "+14155550123", "provider": "TWILIO"},
    )
    assert isinstance(first, RequestClaim)
    assert first.created is True
    assert first.receipt.status == "in_progress"
    await db.commit()

    with pytest.raises(IdempotencyInProgress):
        await claim_request(
            db,
            tenant_id=tenant_a.id,
            environment_id=environment.id,
            operation="telephony.call.outbound",
            key="call-intent-0001",
            request_data={"to": "+14155550123", "provider": "TWILIO"},
        )

    await complete_request(
        db,
        first.receipt,
        resource_type="telephony_call_session",
        resource_id="call-123",
    )
    await db.commit()

    replay = await claim_request(
        db,
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        operation="telephony.call.outbound",
        key="call-intent-0001",
        request_data={"to": "+14155550123", "provider": "TWILIO"},
    )
    assert replay.created is False
    assert replay.replayed is True
    assert replay.receipt.resource_id == "call-123"


async def test_reusing_key_with_different_request_is_a_conflict(db, tenant_a):
    environment = await _production(db, tenant_a)
    await claim_request(
        db,
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        operation="telephony.call.outbound",
        key="call-intent-0002",
        request_data={"to": "+14155550123"},
    )
    await db.commit()

    with pytest.raises(IdempotencyConflict):
        await claim_request(
            db,
            tenant_id=tenant_a.id,
            environment_id=environment.id,
            operation="telephony.call.outbound",
            key="call-intent-0002",
            request_data={"to": "+14155550999"},
        )


async def test_receipts_are_scoped_to_tenant_environment_and_operation(db, tenant_a, tenant_b):
    env_a = await _production(db, tenant_a)
    env_b = await _production(db, tenant_b)
    first = await claim_request(
        db,
        tenant_id=tenant_a.id,
        environment_id=env_a.id,
        operation="telephony.call.outbound",
        key="shared-key-0003",
        request_data={"to": "+14155550123"},
    )
    await complete_request(
        db,
        first.receipt,
        resource_type="telephony_call_session",
        resource_id="call-a",
    )
    await db.commit()

    second = await claim_request(
        db,
        tenant_id=tenant_b.id,
        environment_id=env_b.id,
        operation="telephony.call.outbound",
        key="shared-key-0003",
        request_data={"to": "+14155550123"},
    )
    assert second.created is True
    assert second.receipt.tenant_id == tenant_b.id

    same_tenant_other_operation = await claim_request(
        db,
        tenant_id=tenant_a.id,
        environment_id=env_a.id,
        operation="telephony.call.dtmf",
        key="shared-key-0003",
        request_data={"digits": "123"},
    )
    assert same_tenant_other_operation.created is True


async def test_environment_scope_must_belong_to_tenant(db, tenant_a, tenant_b):
    wrong_environment = await _production(db, tenant_b)
    with pytest.raises(IdempotencyScopeError):
        await claim_request(
            db,
            tenant_id=tenant_a.id,
            environment_id=wrong_environment.id,
            operation="telephony.call.outbound",
            key="call-intent-0004",
            request_data={"to": "+14155550123"},
        )


async def test_only_a_key_digest_is_stored(db, tenant_a):
    environment = await _production(db, tenant_a)
    raw_key = "secret-idempotency-key-0005"
    claim = await claim_request(
        db,
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        operation="telephony.call.outbound",
        key=raw_key,
        request_data={"to": "+14155550123"},
    )
    await db.commit()

    row = await db.scalar(
        select(RequestIdempotencyReceipt).where(
            RequestIdempotencyReceipt.id == claim.receipt.id
        )
    )
    assert row is not None
    assert row.key_digest != raw_key
    assert len(row.key_digest) == 64
