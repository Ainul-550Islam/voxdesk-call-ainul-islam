from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer.abandoned_call_handler import handle_abandoned_call
from app.contact_center_v2.occupancy import reserve_agent
from app.contact_center_v2.policy import prepare_contacts
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import AgentReservation, DialerAttempt, DialerContact
from app.db.models import User


async def test_human_abandonment_is_accounted_once_and_plays_safe_harbor(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a: User,
    available_presence,
    softphone_registration,
    monkeypatch: pytest.MonkeyPatch,
):
    await lead_factory()
    policy = await policy_factory(mode="predictive")
    policy.safe_harbor_message = "Request a callback & opt out."
    await prepare_contacts(db, policy)
    contact = (await db.execute(select(DialerContact))).scalar_one()
    attempt = DialerAttempt(
        tenant_id=policy.tenant_id,
        environment_id=policy.environment_id,
        policy_id=policy.id,
        contact_id=contact.id,
        lead_id=contact.lead_id,
        mode="predictive",
        outcome="ringing",
        idempotency_key="abandon-handler-proof",
        answered_by="human",
        answered_at=utcnow(),
    )
    db.add(attempt)
    await db.flush()
    reservation = await reserve_agent(
        db,
        tenant_id=policy.tenant_id,
        environment_id=policy.environment_id,
        user_id=agent_a.id,
        kind="dialer",
        resource_id=attempt.id,
    )
    await db.commit()
    moment = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.contact_center_v2.dialer.abandoned_call_handler.utcnow", lambda: moment
    )

    response = await handle_abandoned_call(db, attempt=attempt, policy=policy)
    first_abandoned_at = attempt.abandoned_at
    assert response.media_type == "application/xml"
    assert "Request a callback &amp; opt out." in response.body.decode()
    assert "<Hangup" in response.body.decode()
    assert attempt.abandonment_counted is True
    assert attempt.safe_harbor_requested_at == moment
    assert attempt.outcome == "answered"
    assert reservation.status == "released"

    # A repeated provider timeout/webhook cannot double-count or extend the
    # safe-harbor deadline, and it must not create a new occupancy transition.
    replay = await handle_abandoned_call(db, attempt=attempt, policy=policy)
    assert replay.body == response.body
    assert attempt.abandoned_at == first_abandoned_at == moment
    active = (
        await db.execute(
            select(AgentReservation).where(
                AgentReservation.resource_id == attempt.id,
                AgentReservation.status == "active",
            )
        )
    ).scalars().all()
    assert active == []
