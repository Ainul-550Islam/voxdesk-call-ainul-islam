"""Tests for A/B experiment assignment, post-call outcome recording, and statistical evaluation (Part 1G / Gate G1)."""
from __future__ import annotations

import uuid

import pytest

from app.db.enterprise_models import (
    ExperimentStatus,
    ExperimentVariant,
)
from app.db.models import Agent, Call
from app.services.experiment_service import (
    hash_bucket_bp,
    record_call_outcome,
    select_variant_by_hash,
)
from app.telephony.runtime import TelephonyRuntimeService
from tests.conftest import auth_headers


def test_deterministic_hash_assignment_and_distribution():
    exp_id = uuid.uuid4()
    v_control = ExperimentVariant(
        id=uuid.uuid4(),
        experiment_id=exp_id,
        tenant_id=uuid.uuid4(),
        name="Control",
        weight=50,
        is_control=True,
        config={"greeting": "Hello"},
    )
    v_treatment = ExperimentVariant(
        id=uuid.uuid4(),
        experiment_id=exp_id,
        tenant_id=v_control.tenant_id,
        name="Treatment B",
        weight=50,
        is_control=False,
        config={"greeting": "Hi there"},
    )
    variants = [v_control, v_treatment]

    # Deterministic: same call_sid + experiment_id always picks the exact same variant
    sid = "CA99887766554433221100"
    first = select_variant_by_hash(variants, sid, exp_id)
    for _ in range(20):
        assert select_variant_by_hash(variants, sid, exp_id).id == first.id

    # Distribution across 1,000 calls on a 50/50 split must be within [0.44, 0.56]
    control_count = 0
    for idx in range(1000):
        chosen = select_variant_by_hash(variants, f"CA_DIST_{idx:05d}", exp_id)
        bucket = hash_bucket_bp(f"CA_DIST_{idx:05d}", exp_id)
        if bucket < 5000:
            assert chosen.id == v_control.id
            control_count += 1
        else:
            assert chosen.id == v_treatment.id
    ratio = control_count / 1000.0
    assert 0.44 <= ratio <= 0.56


@pytest.mark.asyncio
async def test_runtime_call_setup_persists_experiment_and_variant_and_results_compute_stats(
    client, db, tenant_a, owner_a
):
    agent = Agent(
        tenant_id=tenant_a.id,
        external_key=f"exp_agent_{uuid.uuid4().hex[:8]}",
        name="Active Experiment Agent",
        description="Active voice agent with A/B experiment",
        agent_type="voice",
        status="active",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    headers = await auth_headers(client, owner_a)

    # Create experiment via API
    create_resp = await client.post(
        "/api/ab-testing/experiments",
        headers=headers,
        json={
            "agent_id": str(agent.id),
            "name": "Pitch A vs Pitch B",
            "description": "Test booking conversion rate",
            "variants": [
                {
                    "name": "Control",
                    "weight": 50,
                    "config": {"temperature": 0.4},
                    "prompt": "Standard pitch",
                    "is_control": True,
                },
                {
                    "name": "Challenger",
                    "weight": 50,
                    "config": {"temperature": 0.7},
                    "prompt": "Consultative pitch",
                    "is_control": False,
                },
            ],
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    exp_data = create_resp.json()
    exp_id = uuid.UUID(exp_data["id"])
    control_id = uuid.UUID(
        next(v["id"] for v in exp_data["variants"] if v["is_control"])
    )
    challenger_id = uuid.UUID(
        next(v["id"] for v in exp_data["variants"] if not v["is_control"])
    )

    # Start experiment
    start_resp = await client.post(
        f"/api/ab-testing/experiments/{exp_id}/start",
        headers=headers,
    )
    assert start_resp.status_code == 200, start_resp.text
    assert start_resp.json()["status"] == ExperimentStatus.RUNNING.value

    # Assign a Call via TelephonyRuntimeService
    runtime_svc = TelephonyRuntimeService(db)
    call_one = Call(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        call_sid=f"CA{uuid.uuid4().hex[:30]}",
        from_number="+15550101111",
        to_number="+15550102222",
        direction="inbound",
        status="completed",
        duration_seconds=120.0,
    )
    db.add(call_one)
    await db.flush()

    assigned = await runtime_svc.assign_experiment_for_call(
        call=call_one, agent_id=agent.id
    )
    await db.commit()
    await db.refresh(call_one)

    assert assigned is not None
    assert call_one.experiment_id == exp_id
    assert call_one.variant_id in (control_id, challenger_id)

    # Record fewer than 30 outcomes per arm -> GET /api/ab-testing/experiments/{id}/results returns "insufficient_data"
    await record_call_outcome(
        db,
        tenant_id=tenant_a.id,
        call_id=call_one.id,
        success=True,
        duration=120.0,
        csat=4.5,
        cost=0.18,
    )
    await db.commit()

    insuff_resp = await client.get(
        f"/api/ab-testing/experiments/{exp_id}/results",
        headers=headers,
    )
    assert insuff_resp.status_code == 200, insuff_resp.text
    insuff_body = insuff_resp.json()
    assert insuff_body["status"] == "insufficient_data"
    assert insuff_body["significance"] == "insufficient_data"

    # Populate >= 35 outcomes per arm with clear difference (Control: 10/35 = 28.6%, Challenger: 28/35 = 80.0%)
    for i in range(35):
        await record_call_outcome(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            success=(i < 10),
            duration=180.0 + (i % 15),
            csat=3.2 + (i % 3) * 0.2,
            cost=0.35 + (i % 5) * 0.01,
            experiment_id=exp_id,
            variant_id=control_id,
            call_sid=f"CA_CTRL_{i:03d}",
        )
        await record_call_outcome(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            success=(i < 28),
            duration=110.0 + (i % 15),
            csat=4.6 + (i % 3) * 0.1,
            cost=0.22 + (i % 5) * 0.01,
            experiment_id=exp_id,
            variant_id=challenger_id,
            call_sid=f"CA_CHAL_{i:03d}",
        )
    await db.commit()

    results_resp = await client.get(
        f"/api/ab-testing/experiments/{exp_id}/results",
        headers=headers,
    )
    assert results_resp.status_code == 200, results_resp.text
    results_body = results_resp.json()
    assert results_body["status"] == "significant"
    assert results_body["recommended_winner_variant_id"] == str(challenger_id)
    assert len(results_body["comparisons"]) == 1
    comp = results_body["comparisons"][0]
    assert comp["statistically_significant"] is True
    assert comp["success_z_test"]["z_stat"] > 3.0
    assert comp["success_z_test"]["p_value"] < 0.01
    assert comp["duration_welch_t_test"]["t_stat"] < -5.0
    assert comp["duration_welch_t_test"]["p_value"] < 0.01
