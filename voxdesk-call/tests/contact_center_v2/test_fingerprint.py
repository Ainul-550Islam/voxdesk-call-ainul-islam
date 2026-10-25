import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.leads.consent import record_consent
from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.policy import assert_contact_callable, latest_consent, prepare_contacts
from app.db.contact_center_v2_models import DialerContact
from app.db.models import LeadStatus


async def test_scrub_fingerprint_and_live_consent_dnc_checks_fail_closed(
    db: AsyncSession, policy_factory, lead_factory,
):
    policy = await policy_factory(mode="preview")
    lead = await lead_factory()
    await prepare_contacts(db, policy)
    contact = await db.scalar(
        select(DialerContact).where(
            DialerContact.tenant_id == policy.tenant_id,
            DialerContact.policy_id == policy.id,
            DialerContact.lead_id == lead.id,
        )
    )
    assert contact is not None and contact.scrub_fingerprint
    await assert_contact_callable(db, policy, contact, lead)

    original_phone = lead.phone
    lead.phone = "+15550002999"
    with pytest.raises(ContactCenterV2Error, match="fresh DNC scrub"):
        await assert_contact_callable(db, policy, contact, lead)
    lead.phone = original_phone

    original_timezone = lead.custom_fields["timezone"]
    lead.custom_fields = {**lead.custom_fields, "timezone": "America/New_York"}
    with pytest.raises(ContactCenterV2Error, match="fresh DNC scrub"):
        await assert_contact_callable(db, policy, contact, lead)
    lead.custom_fields = {**lead.custom_fields, "timezone": original_timezone}

    await record_consent(
        db, lead, channel="voice", decision="granted", source="new_signed_form"
    )
    with pytest.raises(ContactCenterV2Error, match="consent"):
        await assert_contact_callable(db, policy, contact, lead)

    # Restoring the snapshot consent does not bypass a direct DNC status change.
    current_consent = await latest_consent(db, lead)
    assert current_consent is not None
    contact.consent_version = current_consent.version
    lead.status = LeadStatus.DNC
    with pytest.raises(ContactCenterV2Error, match="DNC|dnc|blocked"):
        await assert_contact_callable(db, policy, contact, lead)
