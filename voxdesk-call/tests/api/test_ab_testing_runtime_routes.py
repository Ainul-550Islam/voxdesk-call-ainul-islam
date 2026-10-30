"""Behavioural runtime tests for A/B Experiment lifecycle, promote, and rollback (Capability 5)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion, Call, CallStatus
from tests.conftest import auth_headers


async def _seed_published_agent(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[Agent, AgentVersion]:
    agent = Agent(
        tenant_id=tenant_id,
        external_key=f"ab_{uuid.uuid4().hex[:10]}",
        name="AB Runtime Voice Agent",
        agent_type="voice",
        status="published",
        current_draft_config={
            "greeting": "Control greeting v1",
            "system_prompt": "You are a concise concierge.",
            "voice_id": "voice-ctrl",
        },
        meta={},
    )
    db.add(agent)
    await db.flush()

    v1 = AgentVersion(
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_snapshot=dict(agent.current_draft_config),
        config_hash="v1hash",
    )
    db.add(v1)
    await db.flush()
    agent.published_version_id = v1.id
    agent.published_version_number = 1
    await db.commit()
    await db.refresh(agent)
    return agent, v1


@pytest.mark.asyncio
async def test_ab_testing_full_10_operations_promote_and_rollback(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
) -> None:
    tenant_id = tenant_a.id
    headers = await auth_headers(client, owner_a)
    agent, _ = await _seed_published_agent(db, tenant_id)
    agent_id = agent.id

    # 1. Invalid weights (sum != 100) -> 422
    bad_w = await client.post(
        "/api/experiments",
        headers=headers,
        json={
            "name": "Bad Weights",
            "agent_id": str(agent_id),
            "variants": [
                {"name": "Control", "weight": 60, "is_control": True},
                {"name": "Treatment", "weight": 60, "is_control": False},
            ],
        },
    )
    assert bad_w.status_code == 422

    # 2. Zero or >1 control arms -> 422
    bad_ctrl = await client.post(
        "/api/experiments",
        headers=headers,
        json={
            "name": "No Control",
            "agent_id": str(agent_id),
            "variants": [
                {"name": "Arm A", "weight": 50, "is_control": False},
                {"name": "Arm B", "weight": 50, "is_control": False},
            ],
        },
    )
    assert bad_ctrl.status_code == 422

    # 3. Create valid experiment (Op 1)
    created = await client.post(
        "/api/experiments",
        headers=headers,
        json={
            "name": "Greeting Conversion Experiment",
            "description": "Compare warm vs concise greeting",
            "agent_id": str(agent_id),
            "variants": [
                {
                    "name": "Control",
                    "weight": 50,
                    "is_control": True,
                    "config": {"greeting": "Control greeting v1"},
                },
                {
                    "name": "Warm Challenger",
                    "weight": 50,
                    "is_control": False,
                    "config": {"greeting": "Warm challenger greeting v2!"},
                    "prompt": "You are a warm concierge.",
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    exp = created.json()
    exp_id = exp["id"]

    # 4. List & Get & Patch experiment & Patch variant (Ops 2, 3, 4, 5)
    listed = await client.get("/api/experiments", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == exp_id for item in listed.json()["experiments"])

    fetched = await client.get(f"/api/experiments/{exp_id}", headers=headers)
    assert fetched.status_code == 200
    variants = fetched.json()["variants"]
    ctrl_id = next(v["id"] for v in variants if v["is_control"])
    chall_id = next(v["id"] for v in variants if not v["is_control"])

    patched = await client.patch(
        f"/api/experiments/{exp_id}",
        headers=headers,
        json={"description": "Updated description"},
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Updated description"

    v_patched = await client.patch(
        f"/api/experiments/{exp_id}/variants/{chall_id}",
        headers=headers,
        json={"name": "Warm Challenger Updated"},
    )
    assert v_patched.status_code == 200
    assert v_patched.json()["name"] == "Warm Challenger Updated"

    # 5. Start & Pause & Re-Start (Ops 6, 7)
    started = await client.post(f"/api/experiments/{exp_id}/start", headers=headers)
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    # Reject second concurrent running experiment on same agent -> 409
    second = await client.post(
        "/api/experiments",
        headers=headers,
        json={
            "name": "Concurrent Experiment",
            "agent_id": str(agent_id),
            "variants": [
                {"name": "C", "weight": 50, "is_control": True},
                {"name": "T", "weight": 50, "is_control": False},
            ],
        },
    )
    second_id = second.json()["id"]
    conflict = await client.post(f"/api/experiments/{second_id}/start", headers=headers)
    assert conflict.status_code == 409

    paused = await client.post(f"/api/experiments/{exp_id}/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    restarted = await client.post(f"/api/experiments/{exp_id}/start", headers=headers)
    assert restarted.status_code == 200

    # 6. Deterministic call assignment & outcomes & statistical results (Op 8)
    assign_check_1 = await client.post(
        f"/api/experiments/{exp_id}/assign",
        headers=headers,
        json={"call_sid": "CA_det_hash_probe_001"},
    )
    assign_check_2 = await client.post(
        f"/api/experiments/{exp_id}/assign",
        headers=headers,
        json={"call_sid": "CA_det_hash_probe_001"},
    )
    assert assign_check_1.status_code == 200
    assert assign_check_1.json()["variant_id"] == assign_check_2.json()["variant_id"]

    # Seed Call rows assigned to Control vs Challenger so sample sizes >= 30 and Challenger wins p < 0.05
    ctrl_calls = 0
    chall_calls = 0
    idx = 0
    while ctrl_calls < 35 or chall_calls < 35:
        idx += 1
        sid = f"CA_ab_sample_{idx:04d}"
        c = Call(
            tenant_id=tenant_id,
            call_sid=sid,
            from_number="+14155550100",
            to_number="+14155550199",
            status=CallStatus.COMPLETED,
        )
        db.add(c)
        await db.commit()
        cid = c.id

        asgn = await client.post(
            f"/api/experiments/{exp_id}/assign",
            headers=headers,
            json={"call_id": str(cid), "call_sid": sid},
        )
        assert asgn.status_code == 200
        vid = asgn.json()["variant_id"]
        if vid == ctrl_id and ctrl_calls < 35:
            ctrl_calls += 1
            succ = ctrl_calls <= 7  # 20% conversion
            out_r = await client.post(
                f"/api/experiments/{exp_id}/outcomes",
                headers=headers,
                json={"call_id": str(cid), "call_sid": sid, "success": succ, "duration": 62.0, "csat": 3.5},
            )
            assert out_r.status_code == 201
        elif vid == chall_id and chall_calls < 35:
            chall_calls += 1
            succ = chall_calls <= 30  # ~85.7% conversion
            out_r = await client.post(
                f"/api/experiments/{exp_id}/outcomes",
                headers=headers,
                json={"call_id": str(cid), "call_sid": sid, "success": succ, "duration": 45.0, "csat": 4.8},
            )
            assert out_r.status_code == 201

    res = await client.get(f"/api/experiments/{exp_id}/results", headers=headers)
    assert res.status_code == 200
    res_json = res.json()
    assert res_json["significance"] == "significant"
    assert res_json["comparisons"][0]["success_z_test"]["p_value"] < 0.05
    assert res_json["recommended_winner_variant_id"] == chall_id

    # 7. Promote Challenger -> creates new published AgentVersion (Op 9)
    promoted = await client.post(
        f"/api/experiments/{exp_id}/promote",
        headers=headers,
        json={"variant_id": chall_id, "reason": "Significant conversion lift"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["winner_variant_id"] == chall_id

    db.expire_all()
    refreshed_agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    assert refreshed_agent is not None
    assert refreshed_agent.published_version_number == 2

    # 8. Rollback promotion -> restores pre-promotion AgentVersion (Op 10)
    rolled = await client.post(f"/api/experiments/{exp_id}/rollback", headers=headers)
    assert rolled.status_code == 200, rolled.text

    db.expire_all()
    restored_agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    assert restored_agent is not None
    assert restored_agent.published_version_number == 1

    # 9. Delete draft second experiment
    deleted = await client.delete(f"/api/experiments/{second_id}", headers=headers)
    assert deleted.status_code in (200, 204)
