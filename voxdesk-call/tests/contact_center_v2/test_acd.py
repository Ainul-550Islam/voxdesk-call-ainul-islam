from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.acd import sweep_overflow, upsert_policy, wallboard
from app.contact_center_v2.repository import utcnow
from app.db.models import User
from tests.contact_center_v2.conftest import Cc2Scope
from tests.contact_center_v2.test_callbacks import queue_entry


async def test_callback_overflow_runs_once_and_wallboard_uses_real_rows(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    available_presence,
    softphone_registration,
):
    entry = await queue_entry(db, cc2_scope)
    entry.status = "waiting"
    entry.enqueued_at = utcnow() - timedelta(minutes=5)
    await db.commit()
    queue, policy = await upsert_policy(
        db,
        tenant_id=cc2_scope.tenant.id,
        queue_id=entry.queue_id,
        payload={
            "strategy": "skill_first",
            "required_skills": ["billing"],
            "priority": 20,
            "max_concurrency": 3,
            "overflow_kind": "callback",
            "overflow_after_seconds": 60,
            "overflow_waiting": 0,
            "callback_enabled": True,
        },
    )
    assert queue.overflow_policy["kind"] == "none"
    assert queue.required_skills == ["billing"]
    assert policy.overflow_kind == "callback"
    first = await sweep_overflow(db, tenant_id=cc2_scope.tenant.id, queue_id=queue.id)
    second = await sweep_overflow(db, tenant_id=cc2_scope.tenant.id, queue_id=queue.id)
    await db.commit()
    assert first["overflowed"] == 1
    assert second["examined"] == 0
    assert entry.status == "overflowed"
    assert entry.outcome == "callback_requested"
    board = await wallboard(db, tenant_id=cc2_scope.tenant.id)
    assert board["callbacks"]["scheduled"] == 1
    assert board["queues"][0]["waiting"] == 0
    agent = next(row for row in board["agents"] if row["user_id"] == str(agent_a.id))
    assert agent["softphone_registered"] is True
