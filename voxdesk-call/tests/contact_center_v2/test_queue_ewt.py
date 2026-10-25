from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.acd.estimator import estimate_wait_seconds, queue_wait_snapshot
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import QueuePriorityConfig
from tests.contact_center_v2.test_callbacks import queue_entry


def test_ewt_math_is_bounded_and_uses_observed_talk_time():
    assert estimate_wait_seconds(
        waiting=0, active_calls=0, available_agents=0, median_talk_seconds=180, max_wait_seconds=300
    ) == 0
    assert estimate_wait_seconds(
        waiting=4, active_calls=2, available_agents=1, median_talk_seconds=180, max_wait_seconds=300
    ) == 0
    assert estimate_wait_seconds(
        waiting=4, active_calls=2, available_agents=0, median_talk_seconds=120, max_wait_seconds=300
    ) == 240
    assert estimate_wait_seconds(
        waiting=20, active_calls=0, available_agents=0, median_talk_seconds=180, max_wait_seconds=300
    ) == 300
    assert estimate_wait_seconds(
        waiting=1, active_calls=1, available_agents=0, median_talk_seconds=0, max_wait_seconds=300
    ) == 1


async def test_queue_snapshot_uses_real_waiting_age_and_queue_configuration(
    db: AsyncSession, cc2_scope,
):
    entry = await queue_entry(db, cc2_scope, name="EWT proof")
    moment = utcnow()
    entry.status = "waiting"
    entry.enqueued_at = moment - timedelta(seconds=42)
    config = QueuePriorityConfig(
        tenant_id=cc2_scope.tenant.id,
        environment_id=cc2_scope.environment.id,
        queue_id=entry.queue_id,
        priority=7,
        strategy="longest_idle",
        max_wait_seconds=100,
        fallback_kind="callback",
        ewt_config={"median_talk_seconds": 120},
    )
    db.add(config)
    await db.commit()
    snapshot = await queue_wait_snapshot(
        db, tenant_id=cc2_scope.tenant.id, queue_id=entry.queue_id, now=moment
    )
    assert snapshot["waiting"] == 1
    assert snapshot["assigned"] == 0
    assert snapshot["oldest_wait_seconds"] == 42
    assert snapshot["estimated_wait_seconds"] == 100
    assert snapshot["priority"] == 7
    assert snapshot["estimate_method"] == "observed_queue_talk_time_v1"
