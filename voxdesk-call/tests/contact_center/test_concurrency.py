"""Two assignments cannot both win the same item or the same capacity-1 agent."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contact_center.exceptions import AcdError
from app.contact_center.models import RoutingAssignment
from app.contact_center.queues import add_member, create_queue
from app.contact_center.service import enqueue
from app.db.models import Base, Call, CallStatus, Environment, UserRole
from tests.acd_support import go_available
from tests.conftest import make_tenant, make_user


@pytest.mark.asyncio
async def test_capacity_one_rejects_a_second_item(db, tenant_a, agent_a):
    env = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id, Environment.kind == "production"
            )
        )
    ).scalar_one()
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id, capacity=1)
    await go_available(db, tenant_a, agent_a)
    first = Call(
        tenant_id=tenant_a.id,
        environment_id=env.id,
        call_sid=f"CA{uuid.uuid4().hex}",
        from_number="+15551230001",
        to_number=tenant_a.twilio_number,
        status=CallStatus.IN_PROGRESS,
        duration_seconds=0.0,
    )
    second = Call(
        tenant_id=tenant_a.id,
        environment_id=env.id,
        call_sid=f"CA{uuid.uuid4().hex}",
        from_number="+15551230002",
        to_number=tenant_a.twilio_number,
        status=CallStatus.IN_PROGRESS,
        duration_seconds=0.0,
    )
    db.add(first)
    db.add(second)
    await db.commit()
    one = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=first.id)
    two = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=second.id)
    await db.commit()
    assert one.outcome == "assigned"
    assert two.outcome == "waiting"
    assert two.assignment is None
    active = (
        await db.execute(
            select(RoutingAssignment).where(
                RoutingAssignment.tenant_id == tenant_a.id,
                RoutingAssignment.status == "active",
            )
        )
    ).scalars().all()
    assert len(active) == 1


@pytest.mark.asyncio
async def test_concurrent_sessions_award_one_owner():
    path = Path("/tmp/acd-race.sqlite")
    if path.exists():
        path.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        tenant = await make_tenant(db, "Race")
        agent = await make_user(db, tenant, UserRole.AGENT)
        env = (
            await db.execute(
                select(Environment).where(
                    Environment.tenant_id == tenant.id, Environment.kind == "production"
                )
            )
        ).scalar_one()
        queue = await create_queue(db, tenant_id=tenant.id, name="Support", environment_id=env.id)
        await add_member(db, tenant_id=tenant.id, queue_id=queue.id, user_id=agent.id, capacity=1)
        await go_available(db, tenant, agent)
        call = Call(
            tenant_id=tenant.id,
            environment_id=env.id,
            call_sid=f"CA{uuid.uuid4().hex}",
            from_number="+15551230009",
            to_number=tenant.twilio_number,
            status=CallStatus.IN_PROGRESS,
            duration_seconds=0.0,
        )
        db.add(call)
        await db.commit()
        call_id = call.id
        queue_id = queue.id
        tenant_id = tenant.id
    other = Call(
        tenant_id=tenant_id,
        environment_id=env.id,
        call_sid=f"CA{uuid.uuid4().hex}",
        from_number="+15551230008",
        to_number="+15550000000",
        status=CallStatus.IN_PROGRESS,
        duration_seconds=0.0,
    )

    async def _attempt(target_call_id):
        async with maker() as session:
            try:
                result = await enqueue(
                    session, tenant_id=tenant_id, queue_id=queue_id, call_id=target_call_id
                )
                await session.commit()
                return result.outcome
            except AcdError:
                await session.rollback()
                return "failed"

    async with maker() as db:
        db.add(other)
        await db.commit()
        other_id = other.id
    outcomes = await asyncio.gather(_attempt(call_id), _attempt(other_id))
    assert outcomes.count("assigned") == 1
    assert "assigned" in outcomes
    async with maker() as db:
        active = (
            await db.execute(
                select(RoutingAssignment).where(RoutingAssignment.status == "active")
            )
        ).scalars().all()
    assert len(active) == 1
    await engine.dispose()
