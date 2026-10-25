from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.policy import abandonment_measurement, prepare_contacts
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import DialerAttempt, DialerContact
from sqlalchemy import select


async def test_rolling_thirty_day_measurement_excludes_machines_and_old_attempts(
    db: AsyncSession, policy_factory, lead_factory,
):
    await lead_factory()
    policy = await policy_factory()
    await prepare_contacts(db, policy)
    contact = (await db.execute(select(DialerContact))).scalar_one()
    now = utcnow()
    for index in range(100):
        db.add(DialerAttempt(
            tenant_id=policy.tenant_id,
            environment_id=policy.environment_id,
            policy_id=policy.id,
            contact_id=contact.id,
            lead_id=contact.lead_id,
            mode="predictive",
            outcome="abandoned" if index == 0 else "connected",
            idempotency_key=f"attempt-{index}",
            answered_by="human",
            answered_at=now - timedelta(hours=1),
            abandoned_at=now - timedelta(minutes=59) if index == 0 else None,
            abandonment_counted=index == 0,
        ))
    db.add(DialerAttempt(
        tenant_id=policy.tenant_id, environment_id=policy.environment_id,
        policy_id=policy.id, contact_id=contact.id, lead_id=contact.lead_id,
        mode="predictive", outcome="machine", idempotency_key="machine",
        answered_by="machine_start", answered_at=now - timedelta(hours=1),
    ))
    db.add(DialerAttempt(
        tenant_id=policy.tenant_id, environment_id=policy.environment_id,
        policy_id=policy.id, contact_id=contact.id, lead_id=contact.lead_id,
        mode="predictive", outcome="abandoned", idempotency_key="old",
        answered_by="human", answered_at=now - timedelta(days=31),
        abandoned_at=now - timedelta(days=31) + timedelta(seconds=2),
    ))
    await db.commit()
    result = await abandonment_measurement(db, policy, now=now)
    assert result.human_answered == 100
    assert result.abandoned == 1
    assert result.sample_sufficient
    assert not result.breached
    assert str(result.cap) == "0.03"


async def test_measured_breach_stops_all_predictive_excess(db: AsyncSession, policy_factory, lead_factory):
    await lead_factory()
    policy = await policy_factory()
    await prepare_contacts(db, policy)
    contact = (await db.execute(select(DialerContact))).scalar_one()
    now = utcnow()
    for index in range(30):
        db.add(DialerAttempt(
            tenant_id=policy.tenant_id, environment_id=policy.environment_id,
            policy_id=policy.id, contact_id=contact.id, lead_id=contact.lead_id,
            mode="predictive", outcome="abandoned", idempotency_key=f"breach-{index}",
            answered_by="human", answered_at=now - timedelta(hours=2),
            abandoned_at=now - timedelta(hours=1),
        ))
    await db.commit()
    result = await abandonment_measurement(db, policy, now=now)
    assert result.rate == 1
    assert result.breached
    assert result.remaining == 0
