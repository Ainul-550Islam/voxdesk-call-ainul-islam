from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer.preview_dialer import (
    dial_claimed_contact,
    next_contact,
    skip_claimed_contact,
)
from app.contact_center_v2.errors import ContactCenterV2Error, NotFound
from app.contact_center_v2.policy import prepare_contacts
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import DialerContact, PreviewReservation
from app.db.models import User


async def test_preview_claim_is_agent_owned_reused_and_skipped_without_provider(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a: User,
    available_presence,
    softphone_registration,
):
    lead = await lead_factory()
    policy = await policy_factory(mode="preview")
    policy.status = "running"
    await prepare_contacts(db, policy)
    contact, returned_lead = await next_contact(
        db, tenant_id=policy.tenant_id, policy_id=policy.id, agent_id=agent_a.id
    )
    replay, _ = await next_contact(
        db, tenant_id=policy.tenant_id, policy_id=policy.id, agent_id=agent_a.id
    )
    assert replay.id == contact.id
    assert returned_lead.id == lead.id
    assert contact.state == "preview_assigned"
    reservation = (await db.execute(select(PreviewReservation))).scalar_one()
    assert reservation.agent_user_id == agent_a.id

    skipped = await skip_claimed_contact(
        db, tenant_id=policy.tenant_id, contact_id=contact.id, agent_id=agent_a.id
    )
    assert skipped.state == "skipped"
    assert reservation.status == "skipped"
    with pytest.raises(NotFound):
        await next_contact(
            db, tenant_id=policy.tenant_id, policy_id=policy.id, agent_id=agent_a.id
        )


async def test_expired_preview_lease_cannot_cross_provider_boundary(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a: User,
    available_presence,
    softphone_registration,
    monkeypatch: pytest.MonkeyPatch,
):
    await lead_factory()
    policy = await policy_factory(mode="preview")
    policy.status = "running"
    await prepare_contacts(db, policy)
    contact, _ = await next_contact(
        db, tenant_id=policy.tenant_id, policy_id=policy.id, agent_id=agent_a.id
    )
    provider_calls = 0

    async def provider(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {"ok": True}

    monkeypatch.setattr("app.telephony.outbound.place_call", provider)
    with pytest.raises(ContactCenterV2Error) as raised:
        await dial_claimed_contact(
            db,
            tenant_id=policy.tenant_id,
            contact_id=contact.id,
            agent_id=agent_a.id,
            idempotency_key="expired-preview-click",
            now=utcnow() + timedelta(seconds=301),
        )
    assert raised.value.code == "preview_lease_lost"
    assert provider_calls == 0
    assert (await db.execute(select(DialerContact))).scalar_one().active_attempt_id is None
