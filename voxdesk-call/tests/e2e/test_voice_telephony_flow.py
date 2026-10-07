from __future__ import annotations

import json
import time

import pytest

from app.core.config import settings
from app.telephony.webhooks import DEFAULT_WEBHOOK_SECRET, compute_webhook_hmac_signature
from tests.acd_support import production
from tests.conftest import auth_headers
from tests.e2e._support import create_published_voice_agent


def _signed_payload(payload: dict, tenant_id: str) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    provider_secret = (settings.twilio_auth_token or "").strip() or DEFAULT_WEBHOOK_SECRET
    signature = compute_webhook_hmac_signature(
        secret=provider_secret, raw_body=raw, timestamp=timestamp
    )
    return raw, {
        "Content-Type": "application/json",
        "X-Voxdesk-Timestamp": timestamp,
        "X-Voxdesk-Signature": f"sha256={signature}",
        "X-Voxdesk-Organization-Id": tenant_id,
    }


@pytest.mark.asyncio
async def test_phone_sip_inbound_live_fail_closed_and_simulation_version_binding(
    client, db, tenant_a, owner_a
):
    environment = await production(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    agent, published, version = await create_published_voice_agent(
        client,
        headers,
        name="Telephony Inbound E2E",
        greeting="Hello from the inbound published snapshot.",
        system_prompt="PROMPT8_INBOUND_SIGNATURE: Assist the caller safely.",
    )
    agent_id = agent["agent_id"]
    assert published["version"] == 1

    number = await client.post(
        "/api/v1/telephony/phone-numbers",
        headers=headers,
        json={
            "number": "+14155550130",
            "provider": "TWILIO",
            "environment_id": str(environment.id),
        },
    )
    assert number.status_code == 201, number.text
    number_id = number.json()["id"]
    bind = await client.post(
        f"/api/v1/telephony/phone-numbers/{number_id}/bind-agent",
        headers=headers,
        json={"inbound_agent_id": agent_id, "outbound_agent_id": agent_id},
    )
    assert bind.status_code == 200, bind.text
    assert bind.json()["inbound_agent_id"] == agent_id

    sip = await client.post(
        "/api/v1/telephony/sip-connections",
        headers=headers,
        json={
            "name": "Prompt 8 SIP E2E",
            "termination_uri": "sip:carrier.example.test:5061",
            "origination_uri": "sip:voxdesk.example.test:5061",
            "phone_number": "+14155550130",
            "username": "test-trunk-user",
            "password_secret": "e2e-only-sip-secret-2026",
            "transport": "TLS",
        },
    )
    assert sip.status_code == 201, sip.text
    assert sip.json()["status"] == "CONFIGURED"
    assert sip.json()["has_credentials"] is True
    assert "e2e-only-sip-secret-2026" not in sip.text

    # A signed provider-shaped webhook creates a live (non-simulated) call with
    # the persisted current AgentVersion. No paid carrier is contacted here.
    live_event = {
        "provider_event_id": "prompt8-live-inbound-ring-001",
        "provider_call_id": "prompt8-live-inbound-call-001",
        "event_type": "call.ringing",
        "status": "ringing",
        "direction": "inbound",
        "from_number": "+14155558880",
        "to_number": "+14155550130",
    }
    raw, webhook_headers = _signed_payload(live_event, str(tenant_a.id))
    live_webhook = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw,
        headers=webhook_headers,
    )
    assert live_webhook.status_code == 200, live_webhook.text
    assert live_webhook.json()["accepted"] is True

    listed = await client.get(
        "/api/v1/telephony/calls?direction=INBOUND&agent_id=" + agent_id,
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    live_call = next(
        row for row in listed.json()["items"]
        if row["provider_call_id"] == "prompt8-live-inbound-call-001"
    )
    assert live_call["is_simulation"] is False
    assert live_call["agent_version_number"] == 1
    assert live_call["metadata"]["resolved_agent_version_id"] == version["id"]

    media_start = await client.post(
        f"/api/v1/telephony/calls/{live_call['id']}/media-event",
        headers=headers,
        json={"type": "media.start", "encoding": "mulaw", "sample_rate": 8000},
    )
    assert media_start.status_code == 200, media_start.text
    assert media_start.json()["synthetic_media"] is False
    assert media_start.json()["media_state"] != "SPEAKING"
    live_utterance = await client.post(
        f"/api/v1/telephony/calls/{live_call['id']}/media-event",
        headers=headers,
        json={"type": "media.utterance", "text": "I need help with my account."},
    )
    assert live_utterance.status_code == 503
    assert "LIVE_AGENT_RUNTIME_NOT_CONFIGURED" in live_utterance.text
    live_after = await client.get(
        f"/api/v1/telephony/calls/{live_call['id']}", headers=headers
    )
    assert live_after.status_code == 200
    assert live_after.json()["transcript_turns"] == []

    # A signed simulated callback is explicitly marked as simulation and uses
    # the same exact published pointer; only this path emits deterministic media.
    simulated_event = {
        "provider_event_id": "prompt8-sim-inbound-ring-001",
        "provider_call_id": "prompt8-sim-inbound-call-001",
        "event_type": "call.ringing",
        "status": "ringing",
        "direction": "inbound",
        "from_number": "+14155558881",
        "to_number": "+14155550130",
        "is_simulation": True,
    }
    sim_raw, sim_headers = _signed_payload(simulated_event, str(tenant_a.id))
    sim_webhook = await client.post(
        "/api/v1/telephony/webhooks/simulated/inbound",
        content=sim_raw,
        headers=sim_headers,
    )
    assert sim_webhook.status_code == 200, sim_webhook.text
    assert sim_webhook.json()["accepted"] is True
    sim_list = await client.get(
        "/api/v1/telephony/calls?direction=INBOUND&agent_id=" + agent_id,
        headers=headers,
    )
    sim_call = next(
        row for row in sim_list.json()["items"]
        if row["provider_call_id"] == "prompt8-sim-inbound-call-001"
    )
    assert sim_call["is_simulation"] is True
    assert sim_call["agent_version_number"] == 1
    assert sim_call["metadata"]["resolved_agent_version_id"] == version["id"]

    sim_media = await client.post(
        f"/api/v1/telephony/calls/{sim_call['id']}/media-event",
        headers=headers,
        json={"type": "media.start", "encoding": "mulaw", "sample_rate": 8000},
    )
    assert sim_media.status_code == 200, sim_media.text
    assert sim_media.json()["synthetic_media"] is True
    assert sim_media.json()["media_state"] == "SPEAKING"
    assert sim_media.json()["agent_profile"]["version_id"] == version["id"]
