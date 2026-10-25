from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer.preview_dialer import next_contact
from app.contact_center_v2.policy import prepare_contacts
from app.contact_center_v2.jobs import (
    abandon_rollup,
    callback_dispatch_tick,
    occupancy_rollup,
    pacing_tick_guard,
    preview_reservation_reaper,
    stale_device_reaper,
)
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import (
    AgentDevice,
    DialerAttempt,
    DialerContact,
    PreviewReservation,
    SoftphoneSession,
)


async def test_all_six_maintenance_entrypoints_run_an_empty_pass(db: AsyncSession):
    assert await pacing_tick_guard(db) == 0
    assert await abandon_rollup(db) == 0
    assert await callback_dispatch_tick(db) == 0
    assert await occupancy_rollup(db) == 0
    assert await stale_device_reaper(db) == 0
    assert await preview_reservation_reaper(db) == 0


async def test_stale_device_reaper_expires_registration_and_releases_active_key(
    db: AsyncSession,
    cc2_scope,
    agent_a,
    softphone_registration: SoftphoneSession,
):
    moment = utcnow()
    device = AgentDevice(
        tenant_id=cc2_scope.tenant.id,
        environment_id=cc2_scope.environment.id,
        user_id=agent_a.id,
        kind="browser",
        label="Stale browser",
        fingerprint_hash="a" * 64,
        status="active",
        last_heartbeat_at=moment - timedelta(hours=2),
    )
    db.add(device)
    await db.flush()
    softphone_registration.device_id = device.id
    softphone_registration.active_device_key = f"{device.tenant_id}:{device.id}"
    softphone_registration.last_heartbeat_at = device.last_heartbeat_at
    softphone_registration.expires_at = moment + timedelta(minutes=5)
    await db.commit()

    assert await stale_device_reaper(db, now=moment) == 1
    await db.commit()
    await db.refresh(device)
    await db.refresh(softphone_registration)
    assert device.status == "disabled"
    assert device.revoked_at is not None
    assert softphone_registration.status == "expired"
    assert softphone_registration.active_device_key is None
    assert await stale_device_reaper(db, now=moment) == 0


async def test_preview_reservation_reaper_returns_only_an_undialed_contact(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a,
    available_presence,
    softphone_registration,
):
    await lead_factory()
    policy = await policy_factory(mode="preview", status="running")
    await prepare_contacts(db, policy)
    contact, _ = await next_contact(
        db, tenant_id=policy.tenant_id, policy_id=policy.id, agent_id=agent_a.id
    )
    reservation = (await db.execute(select(PreviewReservation))).scalar_one()
    moment = utcnow()
    reservation.expires_at = moment - timedelta(seconds=1)
    contact.preview_lease_until = reservation.expires_at
    await db.commit()

    assert await preview_reservation_reaper(db, now=moment) == 1
    await db.commit()
    await db.refresh(contact)
    await db.refresh(reservation)
    assert reservation.status == "expired"
    assert reservation.active_key is None
    assert contact.state == "eligible"
    assert contact.assigned_agent_id is None
    assert contact.preview_lease_until is None
    assert await preview_reservation_reaper(db, now=moment) == 0


async def test_abandon_rollup_materializes_each_transition_once(
    db: AsyncSession, policy_factory, lead_factory,
):
    lead = await lead_factory()
    policy = await policy_factory(mode="predictive")
    await prepare_contacts(db, policy)
    contact = (await db.execute(select(DialerContact))).scalar_one()
    moment = utcnow()
    attempt = DialerAttempt(
        tenant_id=policy.tenant_id,
        environment_id=policy.environment_id,
        policy_id=policy.id,
        contact_id=contact.id,
        lead_id=lead.id,
        mode="predictive",
        outcome="connected",
        idempotency_key="abandon-rollup-test",
        answered_by="human",
        answered_at=moment - timedelta(seconds=10),
        agent_connected_at=moment - timedelta(seconds=7),
        updated_at=moment,
    )
    db.add(attempt)
    await db.commit()

    assert await abandon_rollup(db, now=moment) == 3
    await db.commit()
    assert await abandon_rollup(db, now=moment) == 0
