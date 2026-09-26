"""Sampling is deterministic and does not select the same call twice."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.contact_center.models import QueueEntry, RoutingAssignment
from app.contact_center.queues import create_queue
from app.qa.models import ComplianceFinding, SampleSelection
from app.qa.sampling import bucket, create_rule, run_rule
from app.telephony.qos import QosSample
from app.tenancy.isolation import NotFound
from tests.acd_support import live_call, production


@pytest.mark.asyncio
async def test_percentage_and_count_are_stable_and_idempotent(db, tenant_a):
    env = await production(db, tenant_a)
    calls = [await live_call(db, tenant_a, env) for _ in range(5)]
    ids = [row.id for row in calls]
    rule = await create_rule(
        db, tenant_id=tenant_a.id, name=f"pct-{uuid.uuid4().hex[:6]}", kind="percentage", percent=100
    )
    first = await run_rule(db, tenant_id=tenant_a.id, rule_id=rule.id, window_key="2026-W13", call_ids=ids)
    second = await run_rule(db, tenant_id=tenant_a.id, rule_id=rule.id, window_key="2026-W13", call_ids=ids)
    assert {row.call_id for row in first} == set(ids)
    assert {row.call_id for row in second} == set(ids)
    stored = await db.scalar(
        select(func.count()).select_from(SampleSelection).where(SampleSelection.rule_id == rule.id)
    )
    assert int(stored) == len(ids)
    counted = await create_rule(
        db,
        tenant_id=tenant_a.id,
        name=f"count-{uuid.uuid4().hex[:6]}",
        kind="count",
        sample_count=2,
    )
    picked = await run_rule(
        db, tenant_id=tenant_a.id, rule_id=counted.id, window_key="2026-W13", call_ids=ids
    )
    again = await run_rule(
        db, tenant_id=tenant_a.id, rule_id=counted.id, window_key="2026-W13", call_ids=list(reversed(ids))
    )
    expected = sorted(ids, key=lambda call_id: (bucket(tenant_a.id, "2026-W13", counted.id, call_id), str(call_id)))[:2]
    assert [row.call_id for row in sorted(picked, key=lambda row: str(row.call_id))] == sorted(expected)
    assert {row.call_id for row in again} == set(expected)


@pytest.mark.asyncio
async def test_targeted_rules_use_stored_facts_not_guesses(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    low = await live_call(db, tenant_a, env)
    fine = await live_call(db, tenant_a, env)
    other = await live_call(db, tenant_a, env)
    db.add(QosSample(tenant_id=tenant_a.id, call_id=low.id, provider="twilio", quality_index=20))
    db.add(QosSample(tenant_id=tenant_a.id, call_id=fine.id, provider="twilio", quality_index=90))
    queue = await create_queue(db, tenant_id=tenant_a.id, name=f"q-{uuid.uuid4().hex[:6]}", environment_id=env.id)
    entry = QueueEntry(
        tenant_id=tenant_a.id,
        environment_id=env.id,
        queue_id=queue.id,
        call_id=low.id,
        status="assigned",
    )
    db.add(entry)
    await db.flush()
    db.add(
        RoutingAssignment(
            tenant_id=tenant_a.id,
            entry_id=entry.id,
            queue_id=queue.id,
            user_id=agent_a.id,
            status="active",
        )
    )
    finding = ComplianceFinding(
        tenant_id=tenant_a.id,
        environment_id=env.id,
        call_id=other.id,
        policy_code="greeting",
        policy_version=1,
        status="open",
        severity="low",
        source="rule",
        idempotency_key=f"rule:{uuid.uuid4().hex}",
    )
    db.add(finding)
    await db.commit()
    qos = await create_rule(
        db,
        tenant_id=tenant_a.id,
        name=f"qos-{uuid.uuid4().hex[:6]}",
        kind="low_qos",
        qos_threshold=50,
        sample_count=10,
    )
    selected = await run_rule(
        db,
        tenant_id=tenant_a.id,
        rule_id=qos.id,
        window_key="day",
        call_ids=[low.id, fine.id, other.id],
    )
    assert {row.call_id for row in selected} == {low.id}
    queued = await create_rule(
        db,
        tenant_id=tenant_a.id,
        name=f"queue-{uuid.uuid4().hex[:6]}",
        kind="queue",
        queue_id=queue.id,
        sample_count=10,
    )
    in_queue = await run_rule(
        db,
        tenant_id=tenant_a.id,
        rule_id=queued.id,
        window_key="day",
        call_ids=[low.id, fine.id],
    )
    assert {row.call_id for row in in_queue} == {low.id}
    agent_rule = await create_rule(
        db,
        tenant_id=tenant_a.id,
        name=f"agent-{uuid.uuid4().hex[:6]}",
        kind="agent",
        agent_user_id=agent_a.id,
        sample_count=10,
    )
    owned = await run_rule(
        db,
        tenant_id=tenant_a.id,
        rule_id=agent_rule.id,
        window_key="day",
        call_ids=[low.id, fine.id],
    )
    assert {row.call_id for row in owned} == {low.id}
    compliance = await create_rule(
        db,
        tenant_id=tenant_a.id,
        name=f"comp-{uuid.uuid4().hex[:6]}",
        kind="compliance",
        sample_count=10,
    )
    flagged = await run_rule(
        db,
        tenant_id=tenant_a.id,
        rule_id=compliance.id,
        window_key="day",
        call_ids=[other.id, fine.id],
    )
    assert {row.call_id for row in flagged} == {other.id}


@pytest.mark.asyncio
async def test_foreign_call_is_not_found(db, tenant_a, tenant_b):
    env_a = await production(db, tenant_a)
    env_b = await production(db, tenant_b)
    own = await live_call(db, tenant_a, env_a)
    foreign = await live_call(db, tenant_b, env_b)
    rule = await create_rule(
        db, tenant_id=tenant_a.id, name=f"own-{uuid.uuid4().hex[:6]}", kind="count", sample_count=1
    )
    with pytest.raises(NotFound):
        await run_rule(
            db,
            tenant_id=tenant_a.id,
            rule_id=rule.id,
            window_key="day",
            call_ids=[own.id, foreign.id],
        )
