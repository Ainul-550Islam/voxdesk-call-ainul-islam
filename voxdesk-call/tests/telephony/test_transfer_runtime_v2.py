"""Provider-backed transfer-v2 tests for the TelephonyCallSession runtime."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest

from app.db.models import Tenant
from app.db.telephony_models import TelephonyCallSession, TelephonyTransferRecord
from app.telephony.enums import TelephonyCallState, TransferLifecycleState, TransferMode
from app.telephony.providers.base import CallResult
from app.telephony.providers.twilio import TwilioAdapter
from app.telephony.schemas import CallTransferRequest
from app.telephony.transfer import CallTransferService
from tests.conftest import make_tenant


async def _live_call_session(db, tenant: Tenant) -> TelephonyCallSession:
    row = TelephonyCallSession(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        provider="TWILIO",
        provider_call_id=f"CA_PARENT_{uuid.uuid4().hex[:12]}",
        direction="INBOUND",
        from_number="+14155550123",
        to_number=tenant.twilio_number,
        status=TelephonyCallState.IN_PROGRESS.value,
        media_state="STREAMING",
        transfer_state=TransferLifecycleState.NONE.value,
        is_simulation=False,
        execution_kind="PRODUCTION",
        transcript_turns=[{"role": "user", "content": "Billing help"}],
        runtime_events=[],
        metadata_json={},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _transfer_query(call_id: uuid.UUID, transfer_id: uuid.UUID) -> str:
    return f"call_session_id={call_id}&transfer_id={transfer_id}"


@pytest.mark.asyncio
async def test_live_cold_transfer_uses_session_scoped_amd_webhook_and_waits_for_callback(
    db, monkeypatch
):
    tenant = await make_tenant(db, "Runtime Transfer Clinic")
    call_session = await _live_call_session(db, tenant)
    redirects: list[tuple[str, str]] = []

    async def redirect(self, external_id: str, twiml: str) -> CallResult:
        redirects.append((external_id, twiml))
        return CallResult("twilio", external_id, "transfer_requested")

    monkeypatch.setattr(TwilioAdapter, "redirect_transfer_twiml", redirect)
    request = CallTransferRequest(
        mode=TransferMode.COLD,
        target_destination="+14155550999",
        whisper_message="Billing escalation for caller +14155550123, password=secret123",
    )

    result = await CallTransferService(db).execute_call_transfer(
        tenant_id=tenant.id,
        call_session=call_session,
        payload=request,
    )

    assert result.status == TransferLifecycleState.RINGING_TARGET.value
    assert len(redirects) == 1
    parent_sid, xml = redirects[0]
    assert parent_sid == call_session.provider_call_id
    root = ET.fromstring(xml)
    dial = root.find("Dial")
    assert dial is not None
    number = dial.find("Number")
    assert number is not None
    assert number.text == "+14155550999"
    assert number.attrib["machineDetection"] == "Enable"
    assert "/telephony/transfer-session-target?" in number.attrib["url"]
    assert "Billing escalation" not in xml
    assert "secret123" not in xml

    persisted = await db.get(TelephonyCallSession, call_session.id)
    assert persisted is not None
    assert persisted.status == TelephonyCallState.TRANSFERRING.value
    assert persisted.transfer_state == TransferLifecycleState.RINGING_TARGET.value
    transfer = await db.get(TelephonyTransferRecord, result.id)
    assert transfer is not None
    plan = transfer.context_snapshot["transfer_v2"]["plan"]
    assert "secret123" not in (plan["whisper"] or "")
    assert "14155550123" not in (plan["whisper"] or "")


@pytest.mark.asyncio
async def test_live_warm_transfer_parks_caller_on_twilio_wav_and_amd_gates_target(
    client, db, monkeypatch
):
    tenant = await make_tenant(db, "Runtime Conference Clinic")
    call_session = await _live_call_session(db, tenant)
    redirects: list[tuple[str, str]] = []
    target_requests: list[dict] = []

    async def redirect(self, external_id: str, twiml: str) -> CallResult:
        redirects.append((external_id, twiml))
        return CallResult("twilio", external_id, "transfer_requested")

    async def create_target(self, **kwargs) -> CallResult:
        target_requests.append(kwargs)
        return CallResult(
            "twilio", "CA_WARM_TARGET_1", "queued", kwargs["target_number"], kwargs["from_number"]
        )

    monkeypatch.setattr(TwilioAdapter, "redirect_transfer_twiml", redirect)
    monkeypatch.setattr(TwilioAdapter, "create_transfer_target", create_target)
    result = await CallTransferService(db).execute_call_transfer(
        tenant_id=tenant.id,
        call_session=call_session,
        payload=CallTransferRequest(
            mode=TransferMode.WARM,
            target_destination="+14155550998",
            whisper_message="Billing context for the agent",
        ),
    )

    assert result.status == TransferLifecycleState.RINGING_TARGET.value
    assert len(redirects) == 1
    caller_xml = ET.fromstring(redirects[0][1])
    conference = caller_xml.find(".//Conference")
    assert conference is not None
    assert conference.attrib["startConferenceOnEnter"] == "false"
    assert "/telephony/transfer-hold-music" in conference.attrib["waitUrl"]
    assert conference.attrib["statusCallbackEvent"] == "start end join leave"
    assert len(target_requests) == 1
    target_args = target_requests[0]
    assert target_args["target_number"] == "+14155550998"
    assert target_args["machine_detection"] is True
    assert "/telephony/transfer-session-target?" in target_args["answer_url"]
    assert "/telephony/transfer-session-target-status?" in target_args["status_callback_url"]

    assert result.context_snapshot["transfer_v2"].get("target_call_sid") == "CA_WARM_TARGET_1"
    transfer = await db.get(TelephonyTransferRecord, result.id)
    assert transfer is not None
    await db.refresh(transfer)
    v2 = transfer.context_snapshot["transfer_v2"]
    assert v2["target_call_sid"] == "CA_WARM_TARGET_1"
    assert v2["conference_name"].startswith("voxdesk-xfer-")

    target_response = await client.post(
        f"/telephony/transfer-session-target?{_transfer_query(call_session.id, result.id)}",
        data={"CallSid": "CA_WARM_TARGET_1", "AnsweredBy": "human"},
    )
    assert target_response.status_code == 200
    target_xml = ET.fromstring(target_response.text)
    assert target_xml.find("Say") is not None
    target_conference = target_xml.find(".//Conference")
    assert target_conference is not None
    assert target_conference.attrib["startConferenceOnEnter"] == "true"
    assert "private" not in target_response.text.lower()

    conference_name = v2["conference_name"]
    join_response = await client.post(
        f"/telephony/transfer-session-conference?{_transfer_query(call_session.id, result.id)}",
        data={
            "StatusCallbackEvent": "participant-join",
            "CallSid": "CA_WARM_TARGET_1",
            "FriendlyName": conference_name,
        },
    )
    assert join_response.status_code == 200
    await db.refresh(call_session)
    await db.refresh(transfer)
    assert call_session.status == TelephonyCallState.TRANSFERRED.value
    assert transfer.status == TransferLifecycleState.COMPLETED.value
    assert any(
        event["event_type"] == "transfer.bridged"
        for event in call_session.runtime_events
    )


@pytest.mark.asyncio
async def test_live_transfer_callback_rejects_unverified_human_and_returns_ai_stream(
    client, db, monkeypatch
):
    tenant = await make_tenant(db, "Runtime Callback Clinic")
    call_session = await _live_call_session(db, tenant)
    async def redirect(self, external_id: str, twiml: str) -> CallResult:
        return CallResult("twilio", external_id, "transfer_requested")

    monkeypatch.setattr(TwilioAdapter, "redirect_transfer_twiml", redirect)
    result = await CallTransferService(db).execute_call_transfer(
        tenant_id=tenant.id,
        call_session=call_session,
        payload=CallTransferRequest(
            mode=TransferMode.COLD,
            target_destination="+14155550997",
            whisper_message="Private billing information",
        ),
    )

    query = _transfer_query(call_session.id, result.id)
    whisper_response = await client.post(
        f"/telephony/transfer-session-target?{query}",
        data={"CallSid": "CA_TARGET_MACHINE", "AnsweredBy": "machine_start"},
    )
    assert whisper_response.status_code == 200
    assert ET.fromstring(whisper_response.text).find("Hangup") is not None
    assert "Private billing information" not in whisper_response.text

    status_response = await client.post(
        f"/telephony/transfer-session-status?{query}",
        data={"CallSid": call_session.provider_call_id, "DialCallStatus": "completed"},
    )
    assert status_response.status_code == 200
    assert "/telephony/ws?token=" in status_response.text
    await db.refresh(call_session)
    transfer = await db.get(TelephonyTransferRecord, result.id)
    assert transfer is not None
    assert transfer.status == TransferLifecycleState.FAILED.value
    assert transfer.failure_reason == "machine_detected"
    assert call_session.status == TelephonyCallState.IN_PROGRESS.value
    assert call_session.transfer_state == TransferLifecycleState.FAILED.value


@pytest.mark.asyncio
async def test_three_way_ai_transfer_is_rejected_instead_of_reported_as_supported(db):
    tenant = await make_tenant(db, "Runtime Three-way Clinic")
    call_session = await _live_call_session(db, tenant)
    with pytest.raises(Exception) as unsupported:
        await CallTransferService(db).execute_call_transfer(
            tenant_id=tenant.id,
            call_session=call_session,
            payload=CallTransferRequest(
                mode=TransferMode.WARM,
                transfer_style="three_way",
                target_destination="+14155550996",
            ),
        )
    assert getattr(unsupported.value, "code", "") == "THREE_WAY_AI_PARTICIPANT_UNSUPPORTED"
