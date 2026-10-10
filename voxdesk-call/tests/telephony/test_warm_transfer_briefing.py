"""Integration tests for Warm Transfer Briefing, Conference Bridge & Fallback to AI (Sub-Phase 2D)."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import Call, CallDirection, CallStatus, TransferState, UserRole
from tests.conftest import auth_headers, make_tenant, make_user


@pytest.mark.asyncio
async def test_warm_transfer_briefing_and_conference_bridge_flow(client, db):
    tenant = await make_tenant(db, name="Warm Transfer Dental")
    tenant.escalation_number = "+14155550199"
    await db.commit()
    owner = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, owner)

    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_WARM_001",
        from_number="+14155550111",
        to_number=tenant.twilio_number,
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    await db.commit()

    # Step 1: Initiate Warm Transfer -> Caller placed on hold in conference, human leg dialed with whispered briefing
    init_res = await client.post(
        f"/api/calls/{call.id}/transfer/warm",
        headers=headers,
        json={
            "destination": "+14155550199",
            "reason": "billing escalation",
            "summary": "Caller has a question about an insurance co-pay from last Tuesday.",
            "caller_name": "Alice Carter",
            "sentiment": "frustrated",
        },
    )
    assert init_res.status_code == 201, init_res.text
    init_body = init_res.json()
    assert init_body["ok"] is True
    assert init_body["mode"] == "warm"
    assert init_body["caller_hold"] is True
    assert init_body["human_briefed"] is True
    assert init_body["conference_name"] == f"voxdesk-warm-{call.id}"
    assert "Alice Carter" in init_body["briefing_text"]
    assert "billing escalation" in init_body["briefing_text"]

    # Step 2: Human agent accepts -> Bridge conference (caller unheld, AI leg detached)
    bridge_res = await client.post(
        f"/api/calls/{call.id}/transfer/warm/bridge",
        headers=headers,
    )
    assert bridge_res.status_code == 200, bridge_res.text
    bridge_body = bridge_res.json()
    assert bridge_body["ok"] is True
    assert bridge_body["caller_hold"] is False
    assert bridge_body["ai_detached"] is True
    assert bridge_body["state"] == TransferState.CONNECTED.value


@pytest.mark.asyncio
async def test_warm_transfer_abort_returns_caller_to_ai(client, db):
    tenant = await make_tenant(db, name="Warm Transfer Abort Clinic")
    tenant.escalation_number = "+14155550199"
    await db.commit()
    owner = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, owner)

    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_WARM_002",
        from_number="+14155550122",
        to_number=tenant.twilio_number,
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    await db.commit()

    await client.post(
        f"/api/calls/{call.id}/transfer/warm",
        headers=headers,
        json={
            "destination": "+14155550199",
            "reason": "schedule change",
            "caller_name": "Bob Vance",
        },
    )

    # Human does not answer -> abort warm transfer and return caller from hold to AI
    abort_res = await client.post(
        f"/api/calls/{call.id}/transfer/warm/abort",
        headers=headers,
        json={"reason": "no_answer"},
    )
    assert abort_res.status_code == 200, abort_res.text
    abort_body = abort_res.json()
    assert abort_body["ok"] is True
    assert abort_body["caller_hold"] is False
    assert abort_body["returned_to_ai"] is True
    assert abort_body["state"] == TransferState.FAILED.value
    assert "off hold" in abort_body["fallback_message"]
