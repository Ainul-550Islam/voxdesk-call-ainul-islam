from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contact_center_v2.worker import (
    run_agent_state_rollup_once,
    run_dialer_feed_once,
    run_maintenance_once,
)
from app.db.contact_center_v2_models import AgentStatePeriod
from app.db.models import DurableJob


async def test_maintenance_worker_runs_a_real_empty_database_pass(db: AsyncSession):
    maker = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    result = await run_maintenance_once(session_factory=maker)
    assert result == {"expired": 0, "timed_out": 0, "overflowed": 0, "crm_reconciled": 0, "errors": 0}

async def test_agent_state_repair_closes_a_contradictory_open_interval(
    db: AsyncSession, available_presence, cc2_scope,
):
    db.add(AgentStatePeriod(
        tenant_id=available_presence.tenant_id,
        environment_id=cc2_scope.environment.id,
        user_id=available_presence.user_id,
        state="available",
        started_at=available_presence.state_changed_at,
        open_key=f"{available_presence.tenant_id}:{available_presence.user_id}",
        source_id="test:available",
    ))
    await db.commit()
    available_presence.state = "offline"
    await db.commit()
    maker = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    async with maker() as maintenance:
        assert await run_agent_state_rollup_once(maintenance) == 1
        await maintenance.commit()
    periods = (await db.execute(select(AgentStatePeriod))).scalars().all()
    assert len(periods) == 2
    assert periods[0].ended_at is not None
    assert periods[1].state == "offline"
    async with maker() as maintenance:
        assert await run_agent_state_rollup_once(maintenance) == 0


async def test_dialer_feed_recovery_is_idempotent_for_the_current_tick(
    db: AsyncSession, policy_factory,
):
    await policy_factory(status="running")
    moment = datetime.now(UTC).replace(microsecond=0)
    maker = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    for _ in range(2):
        async with maker() as maintenance:
            assert await run_dialer_feed_once(maintenance, now=moment) == 1
            await maintenance.commit()
    count = await db.scalar(
        select(func.count()).select_from(DurableJob).where(DurableJob.job_type == "cc2.dialer.tick")
    )
    assert count == 1
