"""Regression tests for outbound cancellation, retry policy, and batch recovery."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.auth.dependencies import TenantContext
from app.api import outbound_call_routes as routes
from app.api.outbound_call_routes import (
    BulkOutboundRequest,
    CallControlRequest,
    OutboundCallRequest,
    bulk_outbound_calls,
    cancel_outbound_call,
    retry_outbound_call,
)
from app.db.enterprise_models import DncEntry
from app.db.models import (
    AuditLog,
    Call,
    CallDirection,
    CallStatus,
    Environment,
    LeadStatus,
    RequestIdempotencyReceipt,
)
from app.leads.models import LeadConsent
from tests.conftest import make_lead


async def _production_environment(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def _stored_call(
    db,
    tenant,
    environment: Environment,
    *,
    status: CallStatus,
    to_number: str = "+14155552671",
    lead_id: uuid.UUID | None = None,
) -> Call:
    row = Call(
        tenant_id=tenant.id,
        environment_id=environment.id,
        call_sid=f"CA{uuid.uuid4().hex[:32]}",
        from_number=tenant.twilio_number,
        to_number=to_number,
        status=status,
        direction=CallDirection.OUTBOUND,
        lead_id=lead_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _context(user, tenant, environment: Environment) -> TenantContext:
    return TenantContext(
        user=user,
        tenant=tenant,
        environment_id=environment.id,
    )


@pytest.mark.asyncio
async def test_cancel_replay_precedes_terminal_status_and_provider_readiness(
    db, tenant_a, owner_a, monkeypatch
):
    environment = await _production_environment(db, tenant_a)
    call = await _stored_call(
        db,
        tenant_a,
        environment,
        status=CallStatus.RINGING,
    )
    provider_calls = []

    class FakeTwilioClient:
        def calls(self, sid):
            assert sid == call.call_sid
            return self

        def update(self, **kwargs):
            provider_calls.append(kwargs)
            return SimpleNamespace(status="canceled")

    monkeypatch.setattr(routes, "_require_twilio_ready", lambda: "https://example.test")
    monkeypatch.setattr(
        "app.telephony.outbound._twilio_client",
        lambda: FakeTwilioClient(),
    )
    ctx = _context(owner_a, tenant_a, environment)
    payload = CallControlRequest(idempotency_key="cancel-replay-key-01")

    first = await cancel_outbound_call(call.id, payload, ctx, db)
    assert first["action"] == "canceled"
    assert first["status"] == CallStatus.CANCELLED.value

    # A replay must resolve from its durable receipt even though the call is
    # now terminal and the carrier configuration may have changed.
    monkeypatch.setattr(
        routes,
        "_require_twilio_ready",
        lambda: (_ for _ in ()).throw(HTTPException(status_code=503, detail="not configured")),
    )
    second = await cancel_outbound_call(call.id, payload, ctx, db)
    assert second["action"] == "already_canceled"
    assert second["status"] == CallStatus.CANCELLED.value
    assert len(provider_calls) == 1
    assert provider_calls[0] == {"status": "canceled"}

    events = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.environment_id == environment.id,
                AuditLog.event_type == "call_ended",
                AuditLog.resource_id == str(call.id),
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].detail["operation"] == "cancel"


@pytest.mark.asyncio
async def test_no_answer_call_is_terminal_for_cancel(db, tenant_a, owner_a, monkeypatch):
    environment = await _production_environment(db, tenant_a)
    call = await _stored_call(
        db,
        tenant_a,
        environment,
        status=CallStatus.NO_ANSWER,
    )
    provider_calls = []

    class FakeTwilioClient:
        def calls(self, sid):
            provider_calls.append(sid)
            return self

        def update(self, **kwargs):
            provider_calls.append(kwargs)
            return SimpleNamespace(status="canceled")

    monkeypatch.setattr(routes, "_require_twilio_ready", lambda: "https://example.test")
    monkeypatch.setattr("app.telephony.outbound._twilio_client", lambda: FakeTwilioClient())

    with pytest.raises(HTTPException) as caught:
        await cancel_outbound_call(
            call.id,
            CallControlRequest(idempotency_key="cancel-no-answer-01"),
            _context(owner_a, tenant_a, environment),
            db,
        )

    assert caught.value.status_code == 409
    assert provider_calls == []
    receipt = (
        await db.execute(
            select(RequestIdempotencyReceipt).where(
                RequestIdempotencyReceipt.tenant_id == tenant_a.id,
                RequestIdempotencyReceipt.operation == "legacy.call.cancel",
            )
        )
    ).scalar_one()
    assert receipt.status == "failed"


@pytest.mark.asyncio
async def test_retry_rechecks_tenant_dnc_and_records_failed_receipt(
    db, tenant_a, owner_a, monkeypatch
):
    environment = await _production_environment(db, tenant_a)
    tenant_a.outbound_enabled = True
    await db.commit()
    lead = await make_lead(
        db,
        tenant_a,
        environment_id=environment.id,
        phone="+14155552671",
        status=LeadStatus.NEW,
    )
    call = await _stored_call(
        db,
        tenant_a,
        environment,
        status=CallStatus.NO_ANSWER,
        to_number=lead.phone,
        lead_id=lead.id,
    )
    db.add(DncEntry(tenant_id=tenant_a.id, phone=lead.phone, reason="user request"))
    await db.commit()
    dial_attempts = []

    async def fake_dial(*args, **kwargs):
        dial_attempts.append((args, kwargs))
        return "CA" + uuid.uuid4().hex[:32]

    async def no_rate_limit(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_check_rate_limit", no_rate_limit)
    monkeypatch.setattr(routes, "_require_twilio_ready", lambda: "https://example.test")
    monkeypatch.setattr(routes, "_attempt_provider_dial", fake_dial)

    with pytest.raises(HTTPException) as caught:
        await retry_outbound_call(
            call.id,
            CallControlRequest(idempotency_key="retry-dnc-key-0001"),
            _context(owner_a, tenant_a, environment),
            db,
        )

    assert caught.value.status_code == 403
    assert dial_attempts == []
    receipt = (
        await db.execute(
            select(RequestIdempotencyReceipt).where(
                RequestIdempotencyReceipt.tenant_id == tenant_a.id,
                RequestIdempotencyReceipt.operation == "legacy.call.retry",
            )
        )
    ).scalar_one()
    assert receipt.status == "failed"


@pytest.mark.asyncio
async def test_retry_rechecks_recorded_voice_consent(
    db, tenant_a, owner_a, monkeypatch
):
    environment = await _production_environment(db, tenant_a)
    tenant_a.outbound_enabled = True
    await db.commit()
    lead = await make_lead(
        db,
        tenant_a,
        environment_id=environment.id,
        phone="+14155552672",
        status=LeadStatus.NEW,
    )
    db.add(
        LeadConsent(
            lead_id=lead.id,
            tenant_id=tenant_a.id,
            environment_id=environment.id,
            channel="voice",
            decision="denied",
            source="test",
            version=1,
        )
    )
    await db.commit()
    call = await _stored_call(
        db,
        tenant_a,
        environment,
        status=CallStatus.FAILED,
        to_number=lead.phone,
        lead_id=lead.id,
    )
    dial_attempts = []

    async def fake_dial(*args, **kwargs):
        dial_attempts.append((args, kwargs))
        return "CA" + uuid.uuid4().hex[:32]

    async def no_rate_limit(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_check_rate_limit", no_rate_limit)
    monkeypatch.setattr(routes, "_require_twilio_ready", lambda: "https://example.test")
    monkeypatch.setattr(routes, "_attempt_provider_dial", fake_dial)

    with pytest.raises(HTTPException) as caught:
        await retry_outbound_call(
            call.id,
            CallControlRequest(idempotency_key="retry-consent-key-01"),
            _context(owner_a, tenant_a, environment),
            db,
        )

    assert caught.value.status_code == 403
    assert dial_attempts == []


@pytest.mark.asyncio
async def test_retry_rechecks_calling_window_before_provider_dial(
    db, tenant_a, owner_a, monkeypatch
):
    environment = await _production_environment(db, tenant_a)
    tenant_a.outbound_enabled = True
    await db.commit()
    call = await _stored_call(
        db,
        tenant_a,
        environment,
        status=CallStatus.NO_ANSWER,
    )
    dial_attempts = []

    async def no_rate_limit(*args, **kwargs):
        return None

    async def fake_dial(*args, **kwargs):
        dial_attempts.append((args, kwargs))
        return "CA" + uuid.uuid4().hex[:32]

    def closed_window(_tenant):
        raise HTTPException(status_code=422, detail="outside calling window")

    monkeypatch.setattr(routes, "_check_rate_limit", no_rate_limit)
    monkeypatch.setattr(routes, "_check_calling_window", closed_window)
    monkeypatch.setattr(routes, "_require_twilio_ready", lambda: "https://example.test")
    monkeypatch.setattr(routes, "_attempt_provider_dial", fake_dial)

    with pytest.raises(HTTPException) as caught:
        await retry_outbound_call(
            call.id,
            CallControlRequest(idempotency_key="retry-window-key-01"),
            _context(owner_a, tenant_a, environment),
            db,
        )

    assert caught.value.status_code == 422
    assert dial_attempts == []


@pytest.mark.asyncio
async def test_bulk_rolls_back_each_failed_item_and_returns_stable_batch_id(
    db, tenant_a, owner_a, monkeypatch
):
    await db.refresh(tenant_a)
    environment = await _production_environment(db, tenant_a)
    ctx = _context(owner_a, tenant_a, environment)
    payload = BulkOutboundRequest(
        calls=[
            OutboundCallRequest(to="+14155552673"),
            OutboundCallRequest(to="+14155552674"),
        ],
        idempotency_key="bulk-stability-key-01",
    )
    original_rollback = db.rollback
    rollbacks = []
    observed_after_failure = []
    call_count = 0

    async def tracked_rollback():
        rollbacks.append(True)
        await original_rollback()

    async def controlled_create(item, request, context, session, key):
        nonlocal call_count
        del request, context, key
        call_count += 1
        if call_count == 1:
            session.add(
                Call(
                    tenant_id=tenant_a.id,
                    environment_id=environment.id,
                    call_sid=f"CA{uuid.uuid4().hex[:32]}",
                    from_number=tenant_a.twilio_number,
                    to_number=item.to,
                    status=CallStatus.RINGING,
                    direction=CallDirection.OUTBOUND,
                )
            )
            await session.flush()
            raise RuntimeError("simulated item failure")
        observed_after_failure.append(
            int((await session.execute(select(Call.id))).scalars().all().__len__())
        )
        return SimpleNamespace(
            id=str(uuid.uuid4()),
            call_sid=f"CA{uuid.uuid4().hex[:32]}",
            status=CallStatus.RINGING.value,
        )

    monkeypatch.setattr(db, "rollback", tracked_rollback)
    monkeypatch.setattr(routes, "create_outbound_call", controlled_create)

    first = await bulk_outbound_calls(payload, ctx, db, None)
    second = await bulk_outbound_calls(payload, ctx, db, None)

    assert first.accepted == 1
    assert first.rejected == 1
    assert second.batch_id == first.batch_id
    assert rollbacks == [True]
    assert observed_after_failure == [0, 0, 0]
    assert call_count == 4
