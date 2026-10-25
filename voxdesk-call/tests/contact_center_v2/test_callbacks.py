import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import Queue, QueueEntry
from app.contact_center_v2.acd.callbacks import cancel_callback, list_callbacks, schedule_callback
from app.contact_center_v2.errors import Conflict, NotFound, Validation
from app.contact_center_v2.repository import utcnow
from app.db.models import Call, CallDirection, DurableJob, Tenant, User
from tests.contact_center_v2.conftest import Cc2Scope


async def queue_entry(db: AsyncSession, scope: Cc2Scope, name: str = "Callbacks") -> QueueEntry:
    queue = Queue(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        name=name,
        enabled=True,
    )
    call = Call(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        call_sid=f"CA{uuid.uuid4().hex[:30]}",
        direction=CallDirection.INBOUND,
        from_number="+15550003000",
        to_number="+15550001000",
    )
    db.add_all([queue, call])
    await db.flush()
    entry = QueueEntry(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_id=queue.id,
        call_id=call.id,
        status="overflowed",
        priority=10,
        outcome="overflow",
    )
    db.add(entry)
    await db.commit()
    return entry


async def test_callback_schedule_is_durable_and_idempotent(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    entry = await queue_entry(db, cc2_scope)
    when = utcnow() + timedelta(hours=2)
    callback, created = await schedule_callback(
        db,
        tenant_id=cc2_scope.tenant.id,
        queue_entry_id=entry.id,
        requested_by=agent_a.id,
        scheduled_at=when,
        timezone_name="America/New_York",
        note="Customer requested afternoon callback",
    )
    replay, replay_created = await schedule_callback(
        db,
        tenant_id=cc2_scope.tenant.id,
        queue_entry_id=entry.id,
        requested_by=agent_a.id,
        scheduled_at=when,
        timezone_name="America/New_York",
        note="Customer requested afternoon callback",
    )
    await db.commit()
    assert created and not replay_created
    assert replay.id == callback.id
    assert replay.job_id == callback.job_id
    jobs = await db.scalar(select(func.count()).select_from(DurableJob))
    assert jobs == 1
    assert entry.outcome == "callback_scheduled"


async def test_callback_rejects_naive_time_and_cross_tenant_entry(
    db: AsyncSession, cc2_scope: Cc2Scope, tenant_b: Tenant,
):
    entry = await queue_entry(db, cc2_scope)
    with pytest.raises(Validation):
        await schedule_callback(
            db,
            tenant_id=cc2_scope.tenant.id,
            queue_entry_id=entry.id,
            requested_by=None,
            scheduled_at=datetime(2026, 1, 1, 12, 0),
            timezone_name="UTC",
        )
    with pytest.raises(NotFound):
        await schedule_callback(
            db,
            tenant_id=tenant_b.id,
            queue_entry_id=entry.id,
            requested_by=None,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            timezone_name="UTC",
        )


async def test_cancelled_callback_cannot_be_reused_for_a_different_time(
    db: AsyncSession, cc2_scope: Cc2Scope,
):
    entry = await queue_entry(db, cc2_scope)
    callback, _ = await schedule_callback(
        db,
        tenant_id=cc2_scope.tenant.id,
        queue_entry_id=entry.id,
        requested_by=None,
        scheduled_at=utcnow() + timedelta(hours=1),
        timezone_name="UTC",
    )
    await cancel_callback(db, tenant_id=cc2_scope.tenant.id, callback_id=callback.id)
    with pytest.raises(Conflict):
        await schedule_callback(
            db,
            tenant_id=cc2_scope.tenant.id,
            queue_entry_id=entry.id,
            requested_by=None,
            scheduled_at=utcnow() + timedelta(hours=2),
            timezone_name="UTC",
        )
    assert callback.status == "cancelled"


async def test_callback_list_can_be_scoped_to_requester_or_assignee(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User, manager_a: User,
):
    first_entry = await queue_entry(db, cc2_scope)
    second_entry = await queue_entry(db, cc2_scope, name="Assigned callbacks")
    first, _ = await schedule_callback(
        db, tenant_id=cc2_scope.tenant.id, queue_entry_id=first_entry.id,
        requested_by=agent_a.id, scheduled_at=utcnow() + timedelta(hours=1), timezone_name="UTC",
    )
    second, _ = await schedule_callback(
        db, tenant_id=cc2_scope.tenant.id, queue_entry_id=second_entry.id,
        requested_by=manager_a.id, scheduled_at=utcnow() + timedelta(hours=2), timezone_name="UTC",
    )
    second.assigned_agent_id = agent_a.id
    await db.commit()
    mine = await list_callbacks(db, tenant_id=cc2_scope.tenant.id, agent_user_id=agent_a.id)
    assert {row.id for row in mine} == {first.id, second.id}
    other = await list_callbacks(db, tenant_id=cc2_scope.tenant.id, agent_user_id=manager_a.id)
    assert [row.id for row in other] == [second.id]
