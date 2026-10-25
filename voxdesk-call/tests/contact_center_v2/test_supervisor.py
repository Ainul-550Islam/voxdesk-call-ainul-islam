import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.realtime import (
    RealtimeTicketError,
    WallboardStreamState,
    active_realtime_user,
    issue_realtime_ticket,
    supervisor_snapshot,
    verify_realtime_ticket,
    without_stream_clock,
)
from app.db.models import User
from tests.contact_center_v2.conftest import Cc2Scope


async def test_realtime_tickets_are_channel_call_and_role_bound(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User, manager_a: User,
):
    call_id = uuid.uuid4()
    ticket = issue_realtime_ticket(
        tenant_id=cc2_scope.tenant.id, environment_id=cc2_scope.environment.id,
        user_id=agent_a.id, token_version=agent_a.token_version,
        channel="assist", call_id=call_id,
    )
    principal = verify_realtime_ticket(ticket, channel="assist", call_id=call_id)
    assert principal.user_id == agent_a.id
    with pytest.raises(RealtimeTicketError):
        verify_realtime_ticket(ticket, channel="assist", call_id=uuid.uuid4())
    with pytest.raises(RealtimeTicketError):
        verify_realtime_ticket(ticket, channel="supervisor")

    supervisor_ticket = issue_realtime_ticket(
        tenant_id=cc2_scope.tenant.id, environment_id=cc2_scope.environment.id,
        user_id=manager_a.id, token_version=manager_a.token_version,
        channel="supervisor", supervisor=True,
    )
    supervisor_principal = verify_realtime_ticket(supervisor_ticket, channel="supervisor")
    _, allowed = await active_realtime_user(db, supervisor_principal)
    assert allowed is True

    forged_agent_ticket = issue_realtime_ticket(
        tenant_id=cc2_scope.tenant.id, environment_id=cc2_scope.environment.id,
        user_id=agent_a.id, token_version=agent_a.token_version,
        channel="supervisor", supervisor=True,
    )
    with pytest.raises(RealtimeTicketError):
        await active_realtime_user(
            db, verify_realtime_ticket(forged_agent_ticket, channel="supervisor")
        )


async def test_supervisor_snapshot_comes_from_durable_wallboard_and_diff_ignores_clock(
    db: AsyncSession, cc2_scope: Cc2Scope,
):
    snapshot = await supervisor_snapshot(db, tenant_id=cc2_scope.tenant.id)
    assert snapshot["source"] == "durable_contact_center_state"
    assert set(snapshot) >= {"queues", "agents", "callbacks", "assist"}
    comparable = without_stream_clock(snapshot)
    assert "streamed_at" not in comparable

    state = WallboardStreamState()
    queue = {"queue_id": "queue-1", "waiting": 2}
    assert state.changed("queue-1", queue) is True
    assert state.changed("queue-1", queue) is False
    queue["waiting"] = 3
    assert state.changed("queue-1", queue) is True
