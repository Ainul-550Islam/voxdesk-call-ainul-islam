from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.occupancy import occupancy_report
from app.db.contact_center_v2_models import AgentStatePeriod
from app.db.models import User
from tests.contact_center_v2.conftest import Cc2Scope


async def test_occupancy_uses_clipped_intervals_not_row_durations(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    start = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    rows = [
        ("available", start - timedelta(minutes=30), start + timedelta(minutes=15)),
        ("busy", start + timedelta(minutes=15), start + timedelta(minutes=45)),
        ("wrap_up", start + timedelta(minutes=45), end + timedelta(minutes=30)),
    ]
    for state, started, ended in rows:
        db.add(AgentStatePeriod(
            tenant_id=cc2_scope.tenant.id,
            environment_id=cc2_scope.environment.id,
            user_id=agent_a.id,
            state=state,
            started_at=started,
            ended_at=ended,
            open_key=None,
            source_id="interval-test",
        ))
    await db.commit()
    report = await occupancy_report(
        db, tenant_id=cc2_scope.tenant.id, start=start, end=end, now=end,
    )
    agent = report["agents"][0]
    assert agent["seconds"]["available"] == 900
    assert agent["seconds"]["busy"] == 1800
    assert agent["seconds"]["wrap_up"] == 900
    assert agent["occupancy"] == "0.75"
    assert report["method"] == "clipped_state_intervals_v1"


async def test_open_interval_and_invalid_reporting_window(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    start = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    db.add(AgentStatePeriod(
        tenant_id=cc2_scope.tenant.id,
        environment_id=cc2_scope.environment.id,
        user_id=agent_a.id,
        state="hold",
        started_at=start,
        ended_at=None,
        open_key=f"{cc2_scope.tenant.id}:{agent_a.id}",
        source_id="interval-test",
    ))
    await db.commit()
    end = start + timedelta(minutes=10)
    report = await occupancy_report(
        db, tenant_id=cc2_scope.tenant.id, start=start, end=end, now=end,
    )
    assert report["total_seconds"]["hold"] == 600
    with pytest.raises(ContactCenterV2Error):
        await occupancy_report(
            db, tenant_id=cc2_scope.tenant.id, start=end, end=start,
        )
