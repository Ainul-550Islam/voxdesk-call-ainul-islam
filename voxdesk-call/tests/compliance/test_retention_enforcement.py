"""Per-tenant, per-agent, per-resource retention and legal-hold tests (Part 1D / Gate G2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.retention import purge_expired_calls
from app.db.enterprise_models import RetentionPolicy
from app.db.models import AuditAction, AuditLog, Call, CallDirection, CallStatus, Speaker, Turn
from app.db.telephony_models import TelephonyCallSession
from app.telephony import media_storage
from app.telephony.recording import CallRecording
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _seed_agent_call(
    db,
    tenant,
    *,
    agent_id: str,
    age_days: int,
    with_recording_key: str | None = None,
) -> Call:
    started_naive = datetime.utcnow() - timedelta(days=age_days)
    call_id = uuid.uuid4()
    sid = f"CA{call_id.hex[:30]}"

    call = Call(
        id=call_id,
        tenant_id=tenant.id,
        call_sid=sid,
        from_number="+15551000001",
        to_number=tenant.twilio_number,
        status=CallStatus.COMPLETED,
        direction=CallDirection.INBOUND,
        started_at=started_naive,
        ended_at=started_naive + timedelta(minutes=2),
    )
    db.add(call)
    await db.flush()

    db.add(
        TelephonyCallSession(
            id=call_id,
            tenant_id=tenant.id,
            agent_id=agent_id,
            provider="TWILIO",
            provider_call_id=sid,
            direction="INBOUND",
            from_number="+15551000001",
            to_number=tenant.twilio_number,
            status="COMPLETED",
            created_at=started_naive,
            updated_at=started_naive,
        )
    )
    db.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Hello there"))

    if with_recording_key:
        db.add(
            CallRecording(
                tenant_id=tenant.id,
                call_id=call.id,
                provider="twilio",
                external_recording_id=f"RE{call_id.hex[:16]}",
                external_guard=f"twilio:RE{call_id.hex[:16]}",
                state="ready",
                storage_key=with_recording_key,
                created_at=started_naive,
            )
        )
    await db.flush()
    return call


async def test_per_agent_and_tenant_default_retention_and_legal_hold(
    client, db, tenant_a, owner_a, monkeypatch
):
    deleted_storage_keys: list[str] = []
    monkeypatch.setattr(
        media_storage,
        "delete_recording_object",
        lambda key: deleted_storage_keys.append(key) or True,
    )

    agent_x = str(uuid.uuid4())
    agent_y = str(uuid.uuid4())

    # Tenant A sets a 7-day retention policy on `call` for Agent X and a 30-day tenant-wide default
    pol_x = RetentionPolicy(
        tenant_id=tenant_a.id,
        agent_id=agent_x,
        resource_type="call",
        retention_days=7,
        purge_after_days=30,
        legal_hold=False,
    )
    pol_default = RetentionPolicy(
        tenant_id=tenant_a.id,
        agent_id="",
        resource_type="call",
        retention_days=30,
        purge_after_days=90,
        legal_hold=False,
    )
    db.add_all([pol_x, pol_default])
    await db.flush()

    call_x_10d = await _seed_agent_call(
        db, tenant_a, agent_id=agent_x, age_days=10, with_recording_key="rec/x-10d.wav"
    )
    call_x_40d = await _seed_agent_call(
        db, tenant_a, agent_id=agent_x, age_days=40, with_recording_key="rec/x-40d.wav"
    )
    call_y_10d = await _seed_agent_call(db, tenant_a, agent_id=agent_y, age_days=10)
    call_y_40d = await _seed_agent_call(
        db, tenant_a, agent_id=agent_y, age_days=40, with_recording_key="rec/y-40d.wav"
    )
    await db.commit()

    # Dry-run via API reports exact eligible counts without deleting
    headers = await auth_headers(client, owner_a)
    dry_resp = await client.post(
        "/api/retention/purge",
        headers=headers,
        json={"dry_run": True, "resource_type": "call"},
    )
    assert dry_resp.status_code == 200, dry_resp.text
    dry_data = dry_resp.json()
    assert dry_data["dry_run"] is True
    assert dry_data["purged_calls"] == 3
    assert await db.get(Call, call_x_10d.id) is not None

    # Execute real purge
    summary = await purge_expired_calls(db, tenant_id=tenant_a.id)
    assert summary["purged_calls"] == 3
    assert summary["purged_recordings"] == 3

    # Agent X's 10d and 40d calls are deleted; Agent Y's 10d call is kept and 40d call is deleted
    assert await db.get(Call, call_x_10d.id) is None
    assert await db.get(Call, call_x_40d.id) is None
    assert await db.get(Call, call_y_10d.id) is not None
    assert await db.get(Call, call_y_40d.id) is None

    await db.refresh(pol_x)
    await db.refresh(pol_default)
    assert pol_x.last_purge_at is not None
    assert pol_x.next_purge_at is not None
    assert pol_default.last_purge_at is not None

    audits = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.action == AuditAction.GDPR_ERASURE,
            )
        )
    ).scalars().all()
    assert len(audits) >= 2

    # Seed another old call for Agent Y, place Agent Y under legal_hold=True, and re-run
    call_y_50d = await _seed_agent_call(db, tenant_a, agent_id=agent_y, age_days=50)
    pol_y_hold = RetentionPolicy(
        tenant_id=tenant_a.id,
        agent_id=agent_y,
        resource_type="call",
        retention_days=7,
        purge_after_days=30,
        legal_hold=True,
    )
    db.add(pol_y_hold)
    # Also set tenant-wide policy to legal_hold=True to verify nothing for Agent Y is deleted
    pol_default.legal_hold = True
    await db.commit()

    summary_held = await purge_expired_calls(db, tenant_id=tenant_a.id)
    assert summary_held["purged_calls"] == 0
    assert summary_held["skipped_legal_hold"] >= 1
    assert await db.get(Call, call_y_50d.id) is not None
    assert await db.get(Call, call_y_10d.id) is not None

    # POST /api/retention/purge for a held policy returns 409
    held_resp = await client.post(
        "/api/retention/purge",
        headers=headers,
        json={"policy_id": str(pol_y_hold.id), "dry_run": False},
    )
    assert held_resp.status_code == 409, held_resp.text
