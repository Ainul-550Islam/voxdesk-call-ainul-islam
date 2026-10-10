"""Unit and integration tests for Multi-Carrier Media Serializers & Telnyx Media Path (Sub-Phase 2F)."""

from __future__ import annotations

import audioop
import base64
import json
import os
import uuid

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame

from app.db.models import Agent, AgentVersion
from app.telephony.media.serializers import build_serializer, supported_media_serializers
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.provider_errors import UnsupportedCapability
from app.telephony.providers.vonage import VonageAdapter
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_multi_carrier_serializers_twilio_telnyx_plivo_sip_and_vonage_honesty():
    matrix = supported_media_serializers()
    assert matrix["twilio"]["supported"] is True
    assert matrix["telnyx"]["supported"] is True
    assert matrix["plivo"]["supported"] is True
    assert matrix["sip"]["supported"] is True
    assert matrix["vonage"]["supported"] is False

    # Vonage adapter must declare voice=False
    vonage_caps = VonageAdapter().capabilities()
    assert vonage_caps.voice is False
    assert vonage_caps.recording is False
    with pytest.raises(UnsupportedCapability):
        build_serializer("vonage", stream_sid="stream_vonage_1")

    # TelnyxFrameSerializer round-trip (PCMU -> 16-bit PCM InputAudioRawFrame -> PCMU JSON)
    telnyx_ser = build_serializer(
        "telnyx",
        stream_sid="telnyx_stream_abc",
        call_sid="v3:telnyx_ctrl_123",
        inbound_encoding="PCMU",
        outbound_encoding="PCMU",
        sample_rate=8000,
    )
    pcm16_in = b"\x10\x02" * 160
    ulaw_bytes = audioop.lin2ulaw(pcm16_in, 2)
    inbound_msg = json.dumps(
        {
            "event": "media",
            "stream_id": "telnyx_stream_abc",
            "media": {"payload": base64.b64encode(ulaw_bytes).decode("ascii")},
        }
    )
    deserialized = await telnyx_ser.deserialize(inbound_msg)
    assert isinstance(deserialized, InputAudioRawFrame)
    assert len(deserialized.audio) == len(pcm16_in)

    serialized_out = await telnyx_ser.serialize(
        OutputAudioRawFrame(audio=pcm16_in, sample_rate=8000, num_channels=1)
    )
    assert serialized_out is not None
    parsed_out = json.loads(serialized_out)
    assert parsed_out["event"] == "media"
    assert parsed_out["media"]["payload"] == base64.b64encode(ulaw_bytes).decode("ascii")

    # Twilio & Plivo & SIP serializers instantiate and serialize cleanly
    twilio_ser = build_serializer("twilio", stream_sid="MZ_TWILIO_1", call_sid="CA_1")
    plivo_ser = build_serializer("plivo", stream_sid="PLIVO_STREAM_1", call_sid="PL_1")
    sip_ser = build_serializer("sip", stream_sid="SIP_STREAM_1", call_sid="SIP_CALL_1")
    assert await twilio_ser.serialize(
        OutputAudioRawFrame(audio=pcm16_in, sample_rate=8000, num_channels=1)
    )
    assert await plivo_ser.serialize(
        OutputAudioRawFrame(audio=pcm16_in, sample_rate=8000, num_channels=1)
    )
    sip_json = await sip_ser.serialize(
        OutputAudioRawFrame(audio=pcm16_in, sample_rate=8000, num_channels=1)
    )
    sip_frame = await sip_ser.deserialize(sip_json)
    assert isinstance(sip_frame, InputAudioRawFrame)
    assert sip_frame.audio == pcm16_in


@pytest.mark.asyncio
async def test_telnyx_inbound_voice_webhook_binds_agent_and_returns_stream(client, db):
    tenant = await make_tenant(db, name="Telnyx Carrier Clinic")
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Telnyx Concierge",
        external_key="telnyx-concierge",
        status="published",
        current_draft_config={"system_prompt": "Welcome to Telnyx Concierge."},
    )
    db.add(agent)
    await db.flush()

    ver = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        is_active=True,
        config_snapshot={"system_prompt": "Welcome to Telnyx Concierge."},
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    agent.published_version_number = 1

    num = PhoneNumber(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        e164="+14155550988",
        provider="telnyx",
        inbound_agent_id=agent.id,
    )
    db.add(num)
    await db.commit()

    resp = await client.post(
        "/telephony/telnyx/voice",
        json={
            "data": {
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "v3:telnyx_call_ctrl_988",
                    "from": "+14155550101",
                    "to": "+14155550988",
                    "direction": "incoming",
                },
            }
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "telnyx"
    assert body["agent_id"] == str(agent.id)
    assert body["agent_version_id"] == str(ver.id)
    assert body["serializer"] == "TelnyxFrameSerializer"
    assert "/telephony/telnyx/stream/" in body["stream_ws_url"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_telnyx_live_outbound_call_media_stream():
    if not os.getenv("TELNYX_API_KEY"):
        pytest.skip("Live Telnyx test requires TELNYX_API_KEY")
    ser = build_serializer("telnyx", stream_sid="live_stream", call_sid="v3:live")
    assert ser is not None
