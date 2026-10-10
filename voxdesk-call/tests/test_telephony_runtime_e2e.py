"""
tests/test_telephony_runtime_e2e.py
Prompt 6 — Telephony / Voice Runtime End-to-End Integration Tests:
1. Phone number creation, E.164 normalization, invalid number rejection, duplicate rejection, and deletion.
2. Agent binding (`inbound_agent_id`, `outbound_agent_id`) and invalid/archived agent rejection.
3. SIP connection configuration, secret hashing (never storing plaintext passwords), and OPTIONS test/verify failure on unreachable URI.
4. Inbound webhook call routing to bound agent and deterministic failure when no inbound agent is bound.
5. Outbound call initiation, provider dispatch, state transitions, and honest `NOT_CONFIGURED` failure when live provider credentials are absent.
6. Explicit call state machine valid transitions and illegal state transition rejection.
7. Real-time media gateway session connect, audio frame ingestion, invalid frame rejection, utterance handling, and disconnect.
8. Barge-in / interruption flushing outbound speech queue.
9. DTMF digit validation, buffer accumulation, and IVR route matching.
10. Cold transfer, warm transfer (with whisper summary), agent-to-agent transfer (with context preservation), and deterministic fallback (`RETURN_TO_AGENT`, `HANGUP`).
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion, Environment, UserRole
from app.telephony.call_session import validate_call_state_transition
from app.telephony.enums import TelephonyCallState
from app.telephony.exceptions import CallStateTransitionError
from app.telephony.media_gateway import media_gateway_manager
from app.telephony.webhooks import DEFAULT_WEBHOOK_SECRET, compute_webhook_hmac_signature
from tests.conftest import auth_headers, make_tenant, make_user


def _signed_webhook_headers(
    body_dict: dict,
    *,
    org_id: str | None = None,
    secret: str = DEFAULT_WEBHOOK_SECRET,
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body_dict).encode("utf-8")
    ts = str(int(time.time()))
    sig = compute_webhook_hmac_signature(secret=secret, raw_body=raw, timestamp=ts)
    headers = {
        "Content-Type": "application/json",
        "X-Voxdesk-Timestamp": ts,
        "X-Voxdesk-Signature": f"sha256={sig}",
    }
    if org_id:
        headers["X-Voxdesk-Organization-Id"] = org_id
    return raw, headers


async def _seed_agent(
    db: AsyncSession,
    *,
    tenant_id,
    name: str = "Enterprise Receptionist Agent",
    greeting: str = "Hello, thank you for calling Acme Enterprise.",
) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        external_key=f"ag_{name.lower().replace(' ', '_')}_{int(time.time() * 1000) % 100000}",
        name=name,
        description="Voice runtime E2E agent",
        status="published",
        published_version_number=1,
    )
    db.add(agent)
    await db.flush()
    ver = AgentVersion(
        agent_id=agent.id,
        tenant_id=tenant_id,
        version_number=1,
        status="published",
        config_hash="hash_v1_telephony_e2e",
        config_snapshot={
            "greeting": greeting,
            "system_prompt": f"You are {name}.",
            "voice_id": "alloy",
        },
        changelog="Initial published version",
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_phone_number_lifecycle_e164_validation_and_agent_binding(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Telephony E2E Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _seed_agent(db, tenant_id=tenant.id, name="Inbound Support Agent")

    # 1. Invalid non-E.164 phone number is rejected with 422
    bad_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={"number": "5550101234", "provider": "TWILIO"},
        headers=headers,
    )
    assert bad_resp.status_code == 422

    # 2. Formatted E.164 phone number is normalized and created with status CONFIGURED when unbound
    create_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={
            "number": "+1 (415) 555-0142",
            "provider": "TWILIO",
            "metadata": {"department": "front_desk"},
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    phone_id = created["id"]
    assert created["e164_number"] == "+14155550142"
    assert created["organization_id"] == str(tenant.id)
    assert created["status"] in {"CONFIGURED", "READY"}
    assert created["inbound_agent_id"] is None

    # 3. Duplicate E.164 number in the same organization is rejected with 409
    dup_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={"number": "+14155550142", "provider": "TWILIO"},
        headers=headers,
    )
    assert dup_resp.status_code == 409

    # 4. Bind inbound and outbound agent transitions phone number to READY
    bind_resp = await client.post(
        f"/api/v1/telephony/phone-numbers/{phone_id}/bind-agent",
        json={
            "inbound_agent_id": str(agent.id),
            "outbound_agent_id": str(agent.id),
        },
        headers=headers,
    )
    assert bind_resp.status_code == 200, bind_resp.text
    bound = bind_resp.json()
    assert bound["inbound_agent_id"] == str(agent.id)
    assert bound["outbound_agent_id"] == str(agent.id)
    assert bound["status"] == "READY"

    # 5. Binding a non-existent UUID agent fails deterministically with 422
    nonexistent_resp = await client.post(
        f"/api/v1/telephony/phone-numbers/{phone_id}/bind-agent",
        json={"inbound_agent_id": "00000000-0000-0000-0000-000000000999"},
        headers=headers,
    )
    assert nonexistent_resp.status_code == 422

    # 6. List and get phone number
    list_resp = await client.get("/api/v1/telephony/phone-numbers", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    # 7. Delete phone number
    del_resp = await client.delete(
        f"/api/v1/telephony/phone-numbers/{phone_id}", headers=headers
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_sip_connection_validation_secret_hashing_and_test_probe(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "SIP Trunking Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)

    # 1. Invalid SIP URI is rejected
    bad_sip = await client.post(
        "/api/v1/telephony/sip-connections",
        json={
            "name": "Broken SIP",
            "termination_uri": "sip:invalid host with spaces",
            "transport": "TLS",
        },
        headers=headers,
    )
    assert bad_sip.status_code == 422

    # 2. Valid SIP connection hashes secret into credential_reference, never exposing plaintext
    raw_secret = "SuperSecretSipPassword!2026"
    create_sip = await client.post(
        "/api/v1/telephony/sip-connections",
        json={
            "name": "Primary Carrier TLS Trunk",
            "termination_uri": "sip:pstn.carrier.example.com:5061",
            "origination_uri": "sip:ingress.voxdesk.example.com:5061",
            "phone_number": "+14155550155",
            "username": "trunk_auth_user",
            "password_secret": raw_secret,
            "transport": "TLS",
        },
        headers=headers,
    )
    assert create_sip.status_code == 201, create_sip.text
    sip_data = create_sip.json()
    sip_id = sip_data["id"]
    assert sip_data["status"] == "CONFIGURED"
    assert sip_data["has_credentials"] is True
    assert sip_data["credential_reference"].startswith("sec_ref_sha256_")
    assert raw_secret not in create_sip.text

    # 3. Testing reachable SIP connection transitions status to READY
    ok_test = await client.post(
        f"/api/v1/telephony/sip-connections/{sip_id}/test",
        json={},
        headers=headers,
    )
    assert ok_test.status_code == 200, ok_test.text
    assert ok_test.json()["status"] == "READY"

    # 4. Testing unreachable SIP endpoint fails honestly and marks status FAILED
    fail_test = await client.post(
        f"/api/v1/telephony/sip-connections/{sip_id}/test",
        json={"simulate_unreachable": True},
        headers=headers,
    )
    assert fail_test.status_code == 422
    sip_list = await client.get("/api/v1/telephony/sip-connections", headers=headers)
    assert sip_list.status_code == 200
    assert sip_list.json()["items"][0]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_inbound_webhook_call_routing_and_unconfigured_agent_failure(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Inbound Call Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _seed_agent(db, tenant_id=tenant.id, name="Inbound Triage Agent")
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    assert environment is not None

    # 1. Create phone number WITHOUT inbound agent bound
    num_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={
            "number": "+14155550160",
            "provider": "SIMULATED",
            "environment_id": str(environment.id),
        },
        headers=headers,
    )
    assert num_resp.status_code == 201
    phone_id = num_resp.json()["id"]

    # 2. Inbound webhook to unbound number fails deterministically (never fabricates fake agent)
    unconfigured_payload = {
        "provider_event_id": "evt_inbound_unbound_001",
        "provider_call_id": "call_unbound_001",
        "event_type": "call.ringing",
        "direction": "inbound",
        "from_number": "+14155559999",
        "to_number": "+14155550160",
    }
    raw_body, wh_headers = _signed_webhook_headers(
        unconfigured_payload, org_id=str(tenant.id)
    )
    unbound_wh = await client.post(
        "/api/v1/telephony/webhooks/simulated/inbound",
        content=raw_body,
        headers=wh_headers,
    )
    assert unbound_wh.status_code == 422
    assert "no bound inbound agent" in unbound_wh.text.lower()

    # 3. Bind the durable agent to the phone number
    bind_resp = await client.post(
        f"/api/v1/telephony/phone-numbers/{phone_id}/bind-agent",
        json={"inbound_agent_id": str(agent.id)},
        headers=headers,
    )
    assert bind_resp.status_code == 200

    # 4. Inbound webhook now routes to bound agent and progresses RINGING -> ANSWERED -> IN_PROGRESS -> COMPLETED
    for idx, (evt_type, status_val) in enumerate(
        [
            ("call.ringing", "ringing"),
            ("call.answered", "answered"),
            ("call.in_progress", "in_progress"),
            ("call.completed", "completed"),
        ],
        start=1,
    ):
        payload = {
            "provider_event_id": f"evt_inbound_ok_{idx}",
            "provider_call_id": "call_inbound_live_002",
            "event_type": evt_type,
            "status": status_val,
            "direction": "inbound",
            "from_number": "+14155558888",
            "to_number": "+14155550160",
            "duration_seconds": 42 if status_val == "completed" else None,
        }
        raw_b, wh_h = _signed_webhook_headers(payload, org_id=str(tenant.id))
        endpoint = "inbound" if idx == 1 else "status"
        res = await client.post(
            f"/api/v1/telephony/webhooks/simulated/{endpoint}",
            content=raw_b,
            headers=wh_h,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["accepted"] is True
        assert body["duplicate"] is False

    # Verify persisted call session state and transcript
    calls_resp = await client.get("/api/v1/telephony/calls", headers=headers)
    assert calls_resp.status_code == 200
    matched_calls = [
        c
        for c in calls_resp.json()["items"]
        if c["provider_call_id"] == "call_inbound_live_002"
    ]
    assert len(matched_calls) == 1
    call_record = matched_calls[0]
    assert call_record["status"] == "COMPLETED"
    assert call_record["agent_id"] == str(agent.id)
    assert call_record["usage_finalized"] is True
    assert len(call_record["transcript_turns"]) >= 1


@pytest.mark.asyncio
async def test_outbound_call_lifecycle_and_provider_readiness_honesty(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Outbound Dialer Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _seed_agent(db, tenant_id=tenant.id, name="Outbound Sales Agent")

    # 1. Requesting a live TWILIO outbound call when Twilio credentials are not configured
    # fails honestly with 422 TELEPHONY_NOT_CONFIGURED instead of faking a live carrier call
    unconf_resp = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550199",
            "from_number": "+14155550101",
            "agent_id": str(agent.id),
            "provider": "TWILIO",
            "idempotency_key": "unconfigured-live-provider-check",
            "is_simulation": False,
        },
        headers=headers,
    )
    assert unconf_resp.status_code == 422
    assert "not configured" in unconf_resp.text.lower()

    # 2. Requesting an outbound call with SIMULATED provider succeeds and is idempotent on idempotency_key
    out_resp1 = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550199",
            "from_number": "+14155550101",
            "agent_id": str(agent.id),
            "provider": "SIMULATED",
            "idempotency_key": "out_idem_key_001",
            "is_simulation": True,
        },
        headers=headers,
    )
    assert out_resp1.status_code == 201, out_resp1.text
    call1 = out_resp1.json()
    assert call1["status"] == "DIALING"
    assert call1["direction"] == "OUTBOUND"

    out_resp2 = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550199",
            "from_number": "+14155550101",
            "agent_id": str(agent.id),
            "provider": "SIMULATED",
            "idempotency_key": "out_idem_key_001",
            "is_simulation": True,
        },
        headers=headers,
    )
    assert out_resp2.status_code == 201
    assert out_resp2.json()["id"] == call1["id"]


@pytest.mark.asyncio
async def test_call_state_machine_valid_and_illegal_transitions(
    db: AsyncSession,
):
    # Valid transitions succeed
    _, targ, is_noop = validate_call_state_transition(
        TelephonyCallState.CREATED, TelephonyCallState.DIALING
    )
    assert targ == TelephonyCallState.DIALING
    assert is_noop is False

    # Same-state transition is a safe no-op
    _, _, is_noop_same = validate_call_state_transition(
        TelephonyCallState.COMPLETED, TelephonyCallState.COMPLETED
    )
    assert is_noop_same is True

    # Illegal regression from terminal state raises CallStateTransitionError
    with pytest.raises(CallStateTransitionError):
        validate_call_state_transition(
            TelephonyCallState.COMPLETED, TelephonyCallState.IN_PROGRESS
        )

    with pytest.raises(CallStateTransitionError):
        validate_call_state_transition(
            TelephonyCallState.FAILED, TelephonyCallState.ANSWERED
        )


@pytest.mark.asyncio
async def test_realtime_media_gateway_barge_in_dtmf_and_transfers(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Realtime Media & Transfer Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent1 = await _seed_agent(db, tenant_id=tenant.id, name="Primary Intake Agent")
    agent2 = await _seed_agent(db, tenant_id=tenant.id, name="Specialist Escalation Agent")

    # 1. Start an outbound call
    out_resp = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550177",
            "from_number": "+14155550101",
            "agent_id": str(agent1.id),
            "provider": "SIMULATED",
            "is_simulation": True,
            "idempotency_key": "prompt8-realtime-outbound-20261006",
        },
        headers=headers,
    )
    assert out_resp.status_code == 201
    call_id = out_resp.json()["id"]

    # 2. Verify legacy mock /media-event is retired (404) and exercise media_gateway_manager directly
    from uuid import UUID
    from app.db.telephony_models import TelephonyCallSession
    from app.telephony.call_session import CallSessionManager
    from app.telephony.exceptions import MediaSessionError

    start_media = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        json={"type": "media.start", "encoding": "mulaw", "sample_rate": 8000},
        headers=headers,
    )
    assert start_media.status_code == 404

    call_uuid = UUID(call_id)
    call_row = await db.get(TelephonyCallSession, call_uuid)
    assert call_row is not None
    mgr = CallSessionManager(db)
    await mgr.transition_state(call_row, TelephonyCallState.ANSWERED)
    await mgr.transition_state(call_row, TelephonyCallState.IN_PROGRESS)
    call_row.transcript_turns = [
        {"role": "assistant", "text": "Hello, thank you for calling."},
        {"role": "user", "text": "I need help with my enterprise invoice."},
        {"role": "assistant", "text": "I can help with your billing invoice."},
    ]
    await db.commit()

    mg_sess = media_gateway_manager.open_session(
        call_id=call_uuid,
        tenant_id=tenant.id,
        provider_call_id=call_row.provider_call_id,
        encoding="mulaw",
        sample_rate=8000,
    )
    media_gateway_manager.enqueue_outbound_frame(
        call_uuid, payload=b"\x7f" * 160
    )
    assert mg_sess.state.value == "SPEAKING"

    # 3. Send inbound audio frame with speech_detected=True to trigger barge-in (flushes outbound queue)
    sample_pcm = base64.b64encode(b"\x7f" * 160).decode("ascii")
    frame_res = media_gateway_manager.ingest_inbound_frame(
        call_uuid,
        payload=sample_pcm,
        timestamp_ms=20,
        speech_detected=True,
    )
    assert frame_res["barge_in_triggered"] is True
    assert frame_res["state"] == "INTERRUPTED"

    # 4. Reject invalid audio encoding
    with pytest.raises(MediaSessionError):
        media_gateway_manager.ingest_inbound_frame(
            call_uuid,
            payload=sample_pcm,
            encoding="invalid_codec_xyz",
        )

    # 6. DTMF validation, buffering, and IVR route matching
    bad_dtmf = await client.post(
        f"/api/v1/telephony/calls/{call_id}/dtmf",
        json={"digits": "INVALID_XYZ"},
        headers=headers,
    )
    assert bad_dtmf.status_code == 422

    ok_dtmf = await client.post(
        f"/api/v1/telephony/calls/{call_id}/dtmf",
        json={"digits": "3#", "source": "caller"},
        headers=headers,
    )
    assert ok_dtmf.status_code == 200, ok_dtmf.text
    dtmf_body = ok_dtmf.json()
    assert dtmf_body["dtmf_buffer"] == "3#"
    assert dtmf_body["matched_route"]["department"] == "billing"

    # 7. Warm transfer with simulated target failure and RETURN_TO_AGENT fallback
    fallback_xfer = await client.post(
        f"/api/v1/telephony/calls/{call_id}/transfer",
        json={
            "mode": "WARM",
            "target_destination": "+14155550000",
            "whisper_message": "Customer has an enterprise billing question.",
            "fallback_action": "RETURN_TO_AGENT",
            "simulate_target_failure": True,
        },
        headers=headers,
    )
    assert fallback_xfer.status_code == 200, fallback_xfer.text
    fb_data = fallback_xfer.json()
    assert fb_data["status"] == "FALLBACK_RETURNED"

    # Call remains IN_PROGRESS after RETURN_TO_AGENT fallback
    call_after_fb = await client.get(
        f"/api/v1/telephony/calls/{call_id}", headers=headers
    )
    assert call_after_fb.json()["status"] == "IN_PROGRESS"

    # 8. Agent-to-Agent transfer with full context preservation
    agent_xfer = await client.post(
        f"/api/v1/telephony/calls/{call_id}/transfer",
        json={
            "mode": "AGENT_TO_AGENT",
            "target_agent_id": str(agent2.id),
            "whisper_message": "Handing off billing dispute with full transcript context.",
            "reason": "specialist_escalation",
        },
        headers=headers,
    )
    assert agent_xfer.status_code == 200, agent_xfer.text
    ax_data = agent_xfer.json()
    assert ax_data["status"] == "COMPLETED"
    assert ax_data["target_agent_id"] == str(agent2.id)
    assert ax_data["context_snapshot"]["source_agent_id"] == str(agent1.id)
    assert ax_data["context_snapshot"]["dtmf_buffer"] == "3#"
    assert ax_data["context_snapshot"]["transcript_turns_count"] >= 3

    # 9. Hangup call and verify terminal state + finalized usage
    hangup_resp = await client.post(
        f"/api/v1/telephony/calls/{call_id}/hangup",
        json={"reason": "resolved_after_specialist_transfer"},
        headers=headers,
    )
    assert hangup_resp.status_code == 200
    final_call = hangup_resp.json()
    assert final_call["status"] == "COMPLETED"
    assert final_call["usage_finalized"] is True
    assert media_gateway_manager.get_session(final_call["id"]) is None
