from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.policy import prepare_contacts, validate_start, verified_caller_id
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import DialerContact, DialerJurisdictionOverride
from app.db.models import LeadStatus, Tenant, User
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.number_trust import NumberTrustProfile


async def test_start_requires_current_scrub_and_safe_harbor(db: AsyncSession, policy_factory, lead_factory):
    await lead_factory()
    policy = await policy_factory()
    with pytest.raises(ContactCenterV2Error) as raised:
        await validate_start(db, policy, require_agents=False)
    assert raised.value.code == "dnc_scrub_required"
    policy.safe_harbor_message = ""
    with pytest.raises(ContactCenterV2Error) as raised:
        await validate_start(db, policy, require_agents=False)
    assert raised.value.code == "safe_harbor_required"


async def test_prepare_snapshots_consent_dnc_and_timezone(db: AsyncSession, policy_factory, lead_factory):
    lead = await lead_factory()
    blocked = await lead_factory(phone="+15550002001", consent=False)
    blocked.status = LeadStatus.DNC
    await db.commit()
    policy = await policy_factory()
    result = await prepare_contacts(db, policy)
    await db.commit()
    assert result == {"leads": 2, "eligible": 1, "blocked": 1}
    contacts = (await db.execute(select(DialerContact))).scalars().all()
    eligible = next(row for row in contacts if row.lead_id == lead.id)
    denied = next(row for row in contacts if row.lead_id == blocked.id)
    assert eligible.state == "eligible"
    assert eligible.consent_version == 1
    assert eligible.recipient_timezone == "UTC"
    assert denied.state == "blocked"
    assert denied.block_reason == "lead_dnc"
    await validate_start(db, policy, require_agents=False)


async def test_predictive_requires_affirmative_consent_even_when_preview_policy_does_not(
    db: AsyncSession, policy_factory, lead_factory,
):
    await lead_factory(consent=False)
    predictive = await policy_factory(consent_required=False)
    result = await prepare_contacts(db, predictive)
    assert result["eligible"] == 0
    contact = (await db.execute(select(DialerContact))).scalar_one()
    assert contact.block_reason == "consent_required"


async def test_stale_or_foreign_caller_id_trust_fails_closed(
    db: AsyncSession, policy_factory, lead_factory, cc2_caller: PhoneNumber, tenant_b: Tenant,
):
    await lead_factory()
    policy = await policy_factory()
    await prepare_contacts(db, policy)
    trust = (await db.execute(select(NumberTrustProfile))).scalar_one()
    trust.last_checked = utcnow() - timedelta(days=30)
    await db.commit()
    with pytest.raises(ContactCenterV2Error) as raised:
        await verified_caller_id(db, policy)
    assert raised.value.code == "caller_id_stale"
    cc2_caller.tenant_id = tenant_b.id
    await db.commit()
    with pytest.raises(ContactCenterV2Error):
        await verified_caller_id(db, policy)


async def test_jurisdiction_override_is_bounded_and_expires(
    db: AsyncSession, policy_factory, lead_factory, agent_a: User,
):
    await lead_factory()
    policy = await policy_factory(abandonment_cap=Decimal("0.04"))
    await prepare_contacts(db, policy)
    with pytest.raises(ContactCenterV2Error) as raised:
        await validate_start(db, policy, require_agents=False)
    assert raised.value.code == "jurisdiction_override_required"
    override = DialerJurisdictionOverride(
        tenant_id=policy.tenant_id,
        environment_id=policy.environment_id,
        policy_id=policy.id,
        jurisdiction="US",
        abandonment_cap=Decimal("0.04"),
        legal_reference="Test jurisdiction order 2026-01",
        reason="Audited test of bounded override evaluation",
        approved_by=agent_a.id,
        expires_at=utcnow() + timedelta(hours=1),
    )
    db.add(override)
    await db.commit()
    await validate_start(db, policy, require_agents=False)
    override.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    with pytest.raises(ContactCenterV2Error):
        await validate_start(db, policy, require_agents=False)
