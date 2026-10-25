import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer.campaign_state import transition_campaign_state
from app.contact_center_v2.errors import Conflict, NotFound
from app.db.models import User
from tests.contact_center_v2.conftest import Cc2Scope


async def test_campaign_state_replays_and_rejects_terminal_or_cross_environment_actions(
    db: AsyncSession, cc2_scope: Cc2Scope, policy_factory, agent_a: User,
):
    policy = await policy_factory(status="running")
    started = await transition_campaign_state(
        db, tenant_id=cc2_scope.tenant.id, environment_id=policy.environment_id,
        policy_id=policy.id, actor_user_id=agent_a.id, action="start",
    )
    assert started.replayed is True and started.job_id is None

    paused = await transition_campaign_state(
        db, tenant_id=cc2_scope.tenant.id, environment_id=policy.environment_id,
        policy_id=policy.id, actor_user_id=agent_a.id, action="pause",
    )
    assert paused.policy.status == "paused" and paused.replayed is False
    version = paused.policy.version
    replay = await transition_campaign_state(
        db, tenant_id=cc2_scope.tenant.id, environment_id=policy.environment_id,
        policy_id=policy.id, actor_user_id=agent_a.id, action="pause",
    )
    assert replay.replayed is True and replay.policy.version == version

    stopped = await transition_campaign_state(
        db, tenant_id=cc2_scope.tenant.id, environment_id=policy.environment_id,
        policy_id=policy.id, actor_user_id=agent_a.id, action="stop",
    )
    assert stopped.policy.status == "stopped"
    with pytest.raises(Conflict):
        await transition_campaign_state(
            db, tenant_id=cc2_scope.tenant.id, environment_id=policy.environment_id,
            policy_id=policy.id, actor_user_id=agent_a.id, action="start",
        )
    with pytest.raises(NotFound):
        await transition_campaign_state(
            db, tenant_id=cc2_scope.tenant.id, environment_id=uuid.uuid4(),
            policy_id=policy.id, actor_user_id=agent_a.id, action="pause",
        )
