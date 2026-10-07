"""
tests/test_telephony_security_idempotency.py
Prompt 6 — Telephony Security, Replay Protection, Idempotency, Usage Accounting,
and Cross-Organization Isolation Tests.
"""

from __future__ import annotations

import json
import time

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion, UserRole
from app.db.telephony_models import TelephonyProviderEvent, TelephonyUsageLedger
from app.telephony.webhooks import DEFAULT_WEBHOOK_SECRET, compute_webhook_hmac_signature
from tests.acd_support import production
from tests.conftest import auth_headers, make_tenant, make_user


def _make_signed_request(
    payload: dict,
    *,
    org_id: str,
    secret: str = DEFAULT_WEBHOOK_SECRET,
    timestamp: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload).encode("utf-8")
    ts = str(timestamp if timestamp is not None else int(time.time()))
    sig = compute_webhook_hmac_signature(secret=secret, raw_body=raw, timestamp=ts)
    return raw, {
        "Content-Type": "application/json",
        "X-Voxdesk-Timestamp": ts,
        "X-Voxdesk-Signature": f"sha256={sig}",
        "X-Voxdesk-Organization-Id": org_id,
    }


async def _create_agent_for_tenant(db: AsyncSession, tenant_id, name: str) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        external_key=f"sec_ag_{int(time.time() * 1000) % 100000}_{name.lower().replace(' ', '_')}",
        name=name,
        description="Security test voice agent",
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
        config_hash="sec_hash_v1",
        config_snapshot={
            "greeting": "Hello from secure voice agent.",
            "system_prompt": f"You are {name}.",
            "voice_id": "alloy",
        },
        changelog="Initial v1",
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_webhook_signature_verification_tamper_and_replay_rejection(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Webhook Security Org")

    valid_payload = {
        "provider_event_id": "evt_sec_001",
        "provider_call_id": "call_sec_001",
        "event_type": "call.ringing",
        "from_number": "+14155550111",
        "to_number": "+14155550122",
    }
    raw_body = json.dumps(valid_payload).encode("utf-8")

    # 1. Missing signature header -> 401
    missing_sig_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert missing_sig_resp.status_code == 401

    # 2. Forged signature with wrong secret -> 401
    _, bad_headers = _make_signed_request(
        valid_payload,
        org_id=str(tenant.id),
        secret="attacker-forged-secret",
    )
    forged_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw_body,
        headers=bad_headers,
    )
    assert forged_resp.status_code == 401

    # 3. Payload tampered after signing -> 401
    _, valid_headers = _make_signed_request(valid_payload, org_id=str(tenant.id))
    tampered_body = json.dumps({**valid_payload, "to_number": "+19999999999"}).encode(
        "utf-8"
    )
    tampered_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=tampered_body,
        headers=valid_headers,
    )
    assert tampered_resp.status_code == 401

    # 4. Stale timestamp outside 300s replay window -> 401
    stale_ts = int(time.time()) - 3600
    raw_stale, stale_headers = _make_signed_request(
        valid_payload,
        org_id=str(tenant.id),
        timestamp=stale_ts,
    )
    stale_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw_stale,
        headers=stale_headers,
    )
    assert stale_resp.status_code == 401
    assert "replay tolerance" in stale_resp.text.lower()


@pytest.mark.asyncio
async def test_webhook_idempotency_out_of_order_and_usage_no_double_billing(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Idempotency & Metering Org")
    environment = await production(db, tenant)
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _create_agent_for_tenant(db, tenant.id, "Metering Agent")
    agent.environment_id = environment.id
    published_version = await db.get(AgentVersion, agent.published_version_id)
    assert published_version is not None
    published_version.published_environment_id = environment.id
    await db.commit()

    # Register the inbound number and immutable agent snapshot in one production environment.
    num_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={
            "number": "+14155550180",
            "provider": "TWILIO",
            "environment_id": str(environment.id),
            "inbound_agent_id": str(agent.id),
        },
        headers=headers,
    )
    assert num_resp.status_code == 201

    # 1. Deliver initial inbound ringing event twice with identical provider_event_id
    ring_payload = {
        "provider_event_id": "evt_idem_ring_100",
        "provider_call_id": "CA_prod_call_100",
        "event_type": "call.ringing",
        "status": "ringing",
        "direction": "inbound",
        "from_number": "+14155550999",
        "to_number": "+14155550180",
    }
    raw_ring, ring_hdrs = _make_signed_request(ring_payload, org_id=str(tenant.id))

    first_ring = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw_ring,
        headers=ring_hdrs,
    )
    assert first_ring.status_code == 200
    assert first_ring.json()["duplicate"] is False
    call_session_id = first_ring.json()["call_session_id"]

    second_ring = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw_ring,
        headers=ring_hdrs,
    )
    assert second_ring.status_code == 200
    assert second_ring.json()["duplicate"] is True
    assert second_ring.json()["call_session_id"] == call_session_id

    # Verify only 1 TelephonyProviderEvent row exists with duplicate_count == 1
    evt_row = (
        await db.execute(
            select(TelephonyProviderEvent).where(
                TelephonyProviderEvent.tenant_id == tenant.id,
                TelephonyProviderEvent.provider_event_id == "evt_idem_ring_100",
            )
        )
    ).scalar_one()
    assert evt_row.duplicate_count == 1

    # 2. Transition call to ANSWERED and then COMPLETED (with 75s billable duration)
    ans_payload = {
        "provider_event_id": "evt_idem_ans_101",
        "provider_call_id": "CA_prod_call_100",
        "event_type": "call.answered",
        "status": "answered",
        "from_number": "+14155550999",
        "to_number": "+14155550180",
    }
    raw_ans, ans_hdrs = _make_signed_request(ans_payload, org_id=str(tenant.id))
    ans_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/status",
        content=raw_ans,
        headers=ans_hdrs,
    )
    assert ans_resp.status_code == 200
    assert ans_resp.json()["call_state"] == "ANSWERED"

    comp_payload = {
        "provider_event_id": "evt_idem_comp_102",
        "provider_call_id": "CA_prod_call_100",
        "event_type": "call.completed",
        "status": "completed",
        "duration_seconds": 75,
        "from_number": "+14155550999",
        "to_number": "+14155550180",
    }
    raw_comp, comp_hdrs = _make_signed_request(comp_payload, org_id=str(tenant.id))
    comp_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/status",
        content=raw_comp,
        headers=comp_hdrs,
    )
    assert comp_resp.status_code == 200
    assert comp_resp.json()["call_state"] == "COMPLETED"

    # 3. Replay terminal completion webhook (both same event ID and a second terminal event ID)
    comp_replay_same = await client.post(
        "/api/v1/telephony/webhooks/twilio/status",
        content=raw_comp,
        headers=comp_hdrs,
    )
    assert comp_replay_same.status_code == 200
    assert comp_replay_same.json()["duplicate"] is True

    comp_payload_second = {
        **comp_payload,
        "provider_event_id": "evt_idem_comp_103_second_terminal",
    }
    raw_comp2, comp2_hdrs = _make_signed_request(
        comp_payload_second, org_id=str(tenant.id)
    )
    comp_replay_diff_id = await client.post(
        "/api/v1/telephony/webhooks/twilio/status",
        content=raw_comp2,
        headers=comp2_hdrs,
    )
    assert comp_replay_diff_id.status_code == 200
    assert comp_replay_diff_id.json()["call_state"] == "COMPLETED"

    # 4. Out-of-order IN_PROGRESS event arriving AFTER COMPLETED must not regress call state
    ooo_payload = {
        "provider_event_id": "evt_idem_ooo_104",
        "provider_call_id": "CA_prod_call_100",
        "event_type": "call.in_progress",
        "status": "in_progress",
    }
    raw_ooo, ooo_hdrs = _make_signed_request(ooo_payload, org_id=str(tenant.id))
    ooo_resp = await client.post(
        "/api/v1/telephony/webhooks/twilio/status",
        content=raw_ooo,
        headers=ooo_hdrs,
    )
    assert ooo_resp.status_code == 200
    assert ooo_resp.json()["call_state"] == "COMPLETED"
    assert ooo_resp.json()["detail"] == "ignored_out_of_order_after_terminal"

    # 5. Verify usage ledger has exactly 1 entry and was NOT double-billed
    ledger_count = (
        await db.execute(
            select(func.count(TelephonyUsageLedger.id)).where(
                TelephonyUsageLedger.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert ledger_count == 1

    usage_resp = await client.get("/api/v1/telephony/usage", headers=headers)
    assert usage_resp.status_code == 200
    usage_data = usage_resp.json()
    assert usage_data["call_count"] == 1
    assert usage_data["total_billable_seconds"] >= 0


@pytest.mark.asyncio
async def test_cross_organization_isolation_across_telephony_resources(
    client: AsyncClient,
    db: AsyncSession,
):
    org_a = await make_tenant(db, "Org Alpha Telephony")
    admin_a = await make_user(db, org_a, role=UserRole.ADMIN)
    headers_a = await auth_headers(client, admin_a)
    agent_a = await _create_agent_for_tenant(db, org_a.id, "Alpha Voice Agent")

    org_b = await make_tenant(db, "Org Beta Telephony")
    admin_b = await make_user(db, org_b, role=UserRole.ADMIN)
    headers_b = await auth_headers(client, admin_b)
    agent_b = await _create_agent_for_tenant(db, org_b.id, "Beta Voice Agent")

    # Org A creates a phone number, SIP connection, and active outbound call
    num_a_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={
            "number": "+14155550191",
            "provider": "SIMULATED",
            "inbound_agent_id": str(agent_a.id),
            "outbound_agent_id": str(agent_a.id),
        },
        headers=headers_a,
    )
    assert num_a_resp.status_code == 201
    num_a_id = num_a_resp.json()["id"]

    sip_a_resp = await client.post(
        "/api/v1/telephony/sip-connections",
        json={
            "name": "Alpha Private SIP",
            "termination_uri": "sip:alpha.carrier.example.com:5061",
            "transport": "TLS",
        },
        headers=headers_a,
    )
    assert sip_a_resp.status_code == 201
    sip_a_id = sip_a_resp.json()["id"]

    call_a_resp = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550192",
            "phone_number_id": num_a_id,
            "agent_id": str(agent_a.id),
            "provider": "SIMULATED",
            "is_simulation": True,
            "idempotency_key": "prompt7-alpha-call-1",
        },
        headers=headers_a,
    )
    assert call_a_resp.status_code == 201
    call_a_id = call_a_resp.json()["id"]

    # 1. Org B cannot see Org A's phone numbers, SIP connections, or calls in list endpoints
    assert (
        await client.get("/api/v1/telephony/phone-numbers", headers=headers_b)
    ).json()["total"] == 0
    assert (
        await client.get("/api/v1/telephony/sip-connections", headers=headers_b)
    ).json()["total"] == 0
    assert (
        await client.get("/api/v1/telephony/calls", headers=headers_b)
    ).json()["total"] == 0

    # 2. Org B cannot read, update, bind, or delete Org A's phone number -> 404
    assert (
        await client.get(
            f"/api/v1/telephony/phone-numbers/{num_a_id}", headers=headers_b
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/api/v1/telephony/phone-numbers/{num_a_id}", headers=headers_b
        )
    ).status_code == 404

    # 3. Org A cannot bind Org B's agent to Org A's phone number -> 403
    cross_bind = await client.post(
        f"/api/v1/telephony/phone-numbers/{num_a_id}/bind-agent",
        json={"inbound_agent_id": str(agent_b.id)},
        headers=headers_a,
    )
    assert cross_bind.status_code == 403

    # 4. Org B cannot test Org A's SIP connection -> 404
    assert (
        await client.post(
            f"/api/v1/telephony/sip-connections/{sip_a_id}/test",
            json={},
            headers=headers_b,
        )
    ).status_code == 404

    # 5. Org B cannot read, hangup, send DTMF, or transfer Org A's call -> 404
    assert (
        await client.get(f"/api/v1/telephony/calls/{call_a_id}", headers=headers_b)
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/telephony/calls/{call_a_id}/hangup",
            json={"reason": "cross_tenant_attack"},
            headers=headers_b,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/telephony/calls/{call_a_id}/dtmf",
            json={"digits": "1"},
            headers=headers_b,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/telephony/calls/{call_a_id}/transfer",
            json={"mode": "COLD", "target_destination": "+14155550199"},
            headers=headers_b,
        )
    ).status_code == 404
