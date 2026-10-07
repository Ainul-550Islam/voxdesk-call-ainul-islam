"""Transactional, replay and data-minimization contracts for call events."""
import uuid

import pytest
from jsonschema import ValidationError
from sqlalchemy import select

from app.db.models import Call, CallStatus
from app.outbox.models import OutboxEvent
from app.telephony.call_state import apply_status, publish_transition
from app.webhooks.call_event_bridge import publish_call_event
from app.webhooks.call_event_catalog import EVENT_NAMES, schema_for, validate_payload
from tests.conftest import make_call


@pytest.mark.asyncio
async def test_transition_and_replay_publish_one_fact(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    for _ in range(2):
        result = apply_status(call, CallStatus.COMPLETED)
        await publish_transition(db, call, result)
    event, created = await publish_call_event(db, call, "call_ended")
    assert not created
    await db.commit()
    rows = list((await db.scalars(select(OutboxEvent))).all())
    assert len(rows) == 1
    assert event.idempotency_key == f"call:{call.id}:call_ended:v1"
    assert event.tenant_id == tenant_a.id
    assert event.payload == {
        "call_id": str(call.id), "status": "completed",
        "direction": "inbound", "duration_seconds": 92.5,
    }


@pytest.mark.asyncio
async def test_rollback_removes_event_and_state(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    call_id = call.id
    result = apply_status(call, CallStatus.FAILED)
    await publish_transition(db, call, result)
    await db.rollback()
    assert list((await db.scalars(select(OutboxEvent))).all()) == []
    stored = await db.get(Call, call_id)
    assert stored.status is CallStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_rejected_transition_publishes_nothing(db, tenant_a):
    call = await make_call(db, tenant_a)
    await publish_transition(db, call, apply_status(call, CallStatus.IN_PROGRESS))
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
async def test_payload_cannot_override_scope_or_leak_transcript(db, tenant_a):
    call = await make_call(db, tenant_a)
    with pytest.raises(ValueError):
        await publish_call_event(db, call, "call_ended", {"call_id": str(uuid.uuid4())})
    with pytest.raises(ValidationError):
        await publish_call_event(db, call, "call_ended", {"transcript": "private"})
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.parametrize("event", sorted(EVENT_NAMES))
def test_catalog_schema_is_versioned_and_not_shared(event):
    first = schema_for(event)
    assert first["$id"].endswith(":v1")
    first["properties"].clear()
    assert schema_for(event)["properties"]


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        validate_payload("invented", {})


@pytest.mark.asyncio
async def test_real_status_route_is_replay_safe(client, db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    for _ in range(2):
        response = await client.post("/telephony/status", data={
            "CallSid": call.call_sid, "CallStatus": "completed", "CallDuration": "100",
        })
        assert response.status_code == 200
    events = list((await db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == str(call.id),
        OutboxEvent.event_type == "call_ended",
    ))).all())
    assert len(events) == 1
    assert events[0].payload["duration_seconds"] == 100


@pytest.mark.asyncio
async def test_subscription_fanout_is_tenant_environment_and_filter_scoped(db, tenant_a, tenant_b):
    from app.db.models import Environment
    from app.outbox.dispatcher import matching_subscriptions
    from app.webhooks.repository import create_subscription

    call = await make_call(db, tenant_a)
    event, _ = await publish_call_event(db, call, "call_ended")
    staging = Environment(tenant_id=tenant_a.id, name="Staging", slug="staging",
                          kind="staging", status="active", is_default=False)
    db.add(staging)
    await db.flush()
    expected = []
    for tenant, environment, events, enabled, matches in [
        (tenant_a, None, [], True, True),
        (tenant_a, call.environment_id, ["call_ended"], True, True),
        (tenant_a, staging.id, ["call_ended"], True, False),
        (tenant_a, None, ["call_started"], True, False),
        (tenant_a, None, [], False, False),
        (tenant_b, None, [], True, False),
    ]:
        subscription = await create_subscription(
            db, tenant_id=tenant.id, environment_id=environment,
            endpoint="https://receiver.example.com/hooks", secret="test-only-signing-secret",
            event_types=events,
        )
        subscription.enabled = enabled
        if matches:
            expected.append(subscription.id)
    await db.flush()
    assert {s.id for s in await matching_subscriptions(db, event)} == set(expected)


@pytest.mark.asyncio
async def test_callback_publication_failure_rolls_back_state(client, db, tenant_a, monkeypatch):
    from app.webhooks import call_event_bridge

    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)

    async def fail(*args, **kwargs):
        raise RuntimeError("publication failed")

    monkeypatch.setattr(call_event_bridge, "publish_call_event", fail)
    with pytest.raises(RuntimeError, match="publication failed"):
        await client.post("/telephony/status", data={
            "CallSid": call.call_sid, "CallStatus": "completed", "CallDuration": "100",
        })
    await db.refresh(call)
    assert call.status is CallStatus.IN_PROGRESS
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
async def test_dtmf_occurrence_is_private_scoped_and_replay_safe(client, db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    digits = "4929382716451029"
    for _ in range(2):
        response = await client.post("/telephony/ivr?node=menu", data={
            "To": tenant_a.twilio_number, "CallSid": call.call_sid, "Digits": digits,
        })
        assert response.status_code == 200
    events = list((await db.scalars(select(OutboxEvent))).all())
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "dtmf_received"
    assert event.tenant_id == tenant_a.id
    assert event.environment_id == call.environment_id
    assert digits not in str(event.payload)
    assert set(event.payload) == {"call_id", "status", "direction", "duration_seconds"}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/telephony/ivr", "/telephony/voice"])
async def test_call_sid_cannot_be_reused_under_another_tenant_number(client, db, tenant_a, tenant_b, route):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    payload = {"To": tenant_b.twilio_number, "CallSid": call.call_sid}
    payload.update({"Digits": "1"} if route.endswith("ivr") else {"From": "+15551230000"})
    response = await client.post(route, data=payload)
    assert response.status_code == 404
    assert "<Stream" not in response.text
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("digits, expected", [("", 200), ("not-dtmf", 422), ("1" * 65, 422)])
async def test_empty_or_invalid_dtmf_publishes_nothing(client, db, tenant_a, digits, expected):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    response = await client.post("/telephony/ivr", data={
        "To": tenant_a.twilio_number, "CallSid": call.call_sid, "Digits": digits,
    })
    assert response.status_code == expected
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other"])
async def test_machine_verdict_publishes_one_owned_event(client, db, tenant_a, verdict):
    from app.db.models import CallDirection
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS, direction=CallDirection.OUTBOUND)
    for _ in range(2):
        response = await client.post("/telephony/outbound-answer", data={
            "CallSid": call.call_sid, "AnsweredBy": verdict,
        })
        assert response.status_code == 200
        assert "<Hangup" in response.text
    rows = list((await db.scalars(select(OutboxEvent))).all())
    assert len(rows) == 1
    assert rows[0].event_type == "voicemail_detected"
    assert rows[0].tenant_id == tenant_a.id
    assert rows[0].aggregate_id == str(call.id)
    await db.refresh(call)
    assert call.status is CallStatus.IN_PROGRESS


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["human", "unknown", "machine_invented"])
async def test_non_machine_verdict_is_not_fabricated_voicemail(client, db, tenant_a, verdict):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    response = await client.post("/telephony/outbound-answer", data={
        "CallSid": call.call_sid, "AnsweredBy": verdict,
    })
    assert response.status_code == 200
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
async def test_unknown_machine_call_is_acknowledged_without_event(client, db):
    response = await client.post("/telephony/outbound-answer", data={
        "CallSid": "CA-unknown", "AnsweredBy": "machine_start",
    })
    assert response.status_code == 200
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
async def test_transfer_acceptance_does_not_publish_completion(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    result = apply_status(call, CallStatus.TRANSFERRED)
    await publish_transition(db, call, result)
    rows = list((await db.scalars(select(OutboxEvent))).all())
    assert [row.event_type for row in rows] == ["transfer_started"]


@pytest.mark.asyncio
async def test_transfer_ringing_then_provider_failure_then_late_answer(client, db, tenant_a, monkeypatch):
    from app.db.models import TransferState
    from app.telephony import twilio_handler

    call = await make_call(db, tenant_a, status=CallStatus.TRANSFERRED,
                           transfer_state=TransferState.DIALING)
    unexpected = []
    original = twilio_handler.crm_hooks.on_transfer_completed

    async def capture(*args, **kwargs):
        unexpected.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(twilio_handler.crm_hooks, "on_transfer_completed", capture)
    for outcome in ["ringing", "ringing", "busy", "busy", "answered"]:
        response = await client.post("/telephony/transfer-status", data={
            "CallSid": call.call_sid, "DialCallStatus": outcome,
        })
        assert response.status_code == 200
    await db.refresh(call)
    assert call.transfer_state is TransferState.FAILED
    assert call.transfer_error == "busy"
    assert unexpected == []
    rows = list((await db.scalars(select(OutboxEvent))).all())
    assert sorted(row.event_type for row in rows) == ["transfer_failed", "transfer_started"]


@pytest.mark.asyncio
async def test_authoritative_failure_replaces_inference_before_late_answer(client, db, tenant_a):
    from app.db.models import TransferState
    from app.telephony.transfer_service import INFERRED_PREFIX

    call = await make_call(db, tenant_a, status=CallStatus.COMPLETED,
                           transfer_state=TransferState.FAILED,
                           transfer_error=INFERRED_PREFIX + "call ended while dialing")
    for outcome in ["busy", "answered"]:
        response = await client.post("/telephony/transfer-status", data={
            "CallSid": call.call_sid, "DialCallStatus": outcome,
        })
        assert response.status_code == 200
    await db.refresh(call)
    assert call.transfer_state is TransferState.FAILED
    assert call.transfer_error == "busy"
    rows = list((await db.scalars(select(OutboxEvent))).all())
    assert [row.event_type for row in rows] == ["transfer_failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/telephony/ivr", "/telephony/outbound-answer"])
async def test_new_producers_reject_unverified_callbacks(client, db, tenant_a, monkeypatch, route):
    from app.core.config import settings
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    monkeypatch.setattr(settings, "twilio_skip_webhook_verify", False)
    response = await client.post(route, data={
        "CallSid": call.call_sid, "To": tenant_a.twilio_number,
        "Digits": "1", "AnsweredBy": "machine_start",
    })
    assert response.status_code == 403
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/telephony/ivr", "/telephony/outbound-answer"])
async def test_producer_publication_failure_is_not_acknowledged(client, db, tenant_a, monkeypatch, route):
    from app.telephony import twilio_handler
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)

    async def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(twilio_handler, "publish_call_event", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await client.post(route, data={
            "CallSid": call.call_sid, "To": tenant_a.twilio_number,
            "Digits": "1", "AnsweredBy": "machine_start",
        })
    assert list((await db.scalars(select(OutboxEvent))).all()) == []


@pytest.mark.asyncio
async def test_reconciliation_uses_the_same_customer_outbox(db, tenant_a):
    from app.telephony.callback_reconciliation import ingest_callback
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    envelope = {"provider": "twilio", "event_type": "call.status", "external_id": call.call_sid,
                "event_id": "contract-reconcile", "tenant_id": call.tenant_id, "status": "completed"}
    first = await ingest_callback(db, envelope)
    second = await ingest_callback(db, envelope)
    assert first["side_effect"] is True and second["duplicate"] is True
    rows = list((await db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(call.id)))).all())
    assert [row.event_type for row in rows] == ["call_ended"]


@pytest.mark.asyncio
async def test_recording_ready_is_reference_only_and_replay_safe(db, tenant_a):
    from app.telephony.recording import CallRecording, apply_provider_event
    call = await make_call(db, tenant_a)
    recording = CallRecording(tenant_id=tenant_a.id, call_id=call.id, provider="twilio", state="processing",
                              external_recording_id="RE-contract", external_guard="twilio:RE-contract")
    db.add(recording)
    await db.flush()
    for expected in ["applied", "duplicate"]:
        row, outcome = await apply_provider_event(db, tenant_id=tenant_a.id, provider="twilio",
                                                  external_id="RE-contract", target_state="ready")
        assert outcome == expected
    rows = list((await db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(call.id)))).all())
    assert len(rows) == 1 and rows[0].event_type == "recording_ready"
    assert rows[0].payload["recording_id"] == str(row.id)
    assert "recording_url" not in rows[0].payload


def test_legacy_failed_filter_never_receives_completed_calls():
    from app.webhooks.call_event_catalog import matches_subscription
    assert matches_subscription("call_ended", ["call.failed"], {"status": "failed"})
    assert not matches_subscription("call_ended", ["call.failed"], {"status": "completed"})
    assert matches_subscription("call_ended", ["call.completed"], {"status": "completed"})


@pytest.mark.asyncio
async def test_provider_callback_deduplication_cannot_expose_another_tenant(db, tenant_a, tenant_b):
    from app.telephony.callback_reconciliation import ingest_callback
    from app.tenancy.isolation import NotFound
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    envelope = {"provider": "twilio", "event_type": "call.status", "external_id": call.call_sid,
                "event_id": "owned-provider-event", "tenant_id": tenant_a.id, "status": "completed"}
    await ingest_callback(db, envelope)
    with pytest.raises(NotFound):
        await ingest_callback(db, {**envelope, "tenant_id": tenant_b.id})
