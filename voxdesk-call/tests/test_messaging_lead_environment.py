"""Messaging webhook → lead environment + lifecycle (Batch 06).

The SMS/WhatsApp webhook acts in the tenant's resolved environment (the
headless rule: active default production). STOP/START go through the
canonical consent + lifecycle path — a lead created for an unknown number
always carries that environment and creation history, STOP produces a real
do-not-call transition with history, START never reverses it, and a STOP in
production can never mutate a staging lead.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Environment, Lead, LeadStatus
from app.leads.activities import history_for
from app.leads.models import LeadConsent
from tests.conftest import make_lead


# ----------------------------------------------------------------- helpers ---

async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def _staging(db, tenant) -> Environment:
    environment = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


async def _send(client, tenant, phone: str, body: str, sid: str = ""):
    return await client.post(
        "/channels/message",
        data={"From": phone, "To": tenant.twilio_number, "Body": body,
              "MessageSid": sid},
    )


async def _lead_for(db, tenant, phone: str, environment_id=None) -> Lead | None:
    stmt = select(Lead).where(
        Lead.tenant_id == tenant.id, Lead.phone == phone
    )
    if environment_id is not None:
        stmt = stmt.where(Lead.environment_id == environment_id)
    return (await db.execute(stmt)).scalars().first()


async def _consents(db, lead) -> list[LeadConsent]:
    return list(
        (
            await db.execute(
                select(LeadConsent)
                .where(LeadConsent.lead_id == lead.id)
                .order_by(LeadConsent.recorded_at, LeadConsent.version)
            )
        ).scalars().all()
    )


# -------------------------------------------------------------------- STOP ---

async def test_stop_from_an_unknown_number_creates_a_scoped_dnc_lead(
    client, db, tenant_a
):
    production = await _production(db, tenant_a)
    response = await _send(client, tenant_a, "+15551000001", "STOP")
    assert response.status_code == 200

    lead = await _lead_for(db, tenant_a, "+15551000001")
    assert lead is not None
    assert lead.environment_id == production.id  # never environment-less
    assert lead.status is LeadStatus.DNC

    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [
        (None, "new"), ("new", "do_not_call"),
    ]
    assert history[0].source == "messaging"
    assert history[1].source == "consent"
    assert "consent:voice:denied" in history[1].reason

    consents = await _consents(db, lead)
    assert [(c.channel, c.decision, c.source) for c in consents] == [
        ("voice", "denied", "messaging:stop")
    ]


async def test_stop_on_an_existing_production_lead(client, db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551000002")
    response = await _send(client, tenant_a, "+15551000002", "stop")
    assert response.status_code == 200

    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert history[-1].to_status == "do_not_call"
    assert history[-1].source == "consent"


# ------------------------------------------------------------------- START ---

async def test_start_never_reverses_do_not_call(client, db, tenant_a):
    await _send(client, tenant_a, "+15551000003", "STOP")
    response = await _send(client, tenant_a, "+15551000003", "START")
    assert response.status_code == 200
    assert "subscribed" in response.text

    lead = await _lead_for(db, tenant_a, "+15551000003")
    assert lead.status is LeadStatus.DNC  # terminal — the silent reversal is gone
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [
        (None, "new"), ("new", "do_not_call"),
    ]  # START wrote no status transition
    consents = await _consents(db, lead)
    assert [(c.channel, c.decision) for c in consents] == [
        ("voice", "denied"), ("sms", "granted"),
    ]


async def test_start_on_a_normal_lead_records_sms_consent_only(client, db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551000004")
    response = await _send(client, tenant_a, "+15551000004", "START")
    assert response.status_code == 200

    await db.refresh(lead)
    assert lead.status is LeadStatus.NEW  # subscription is consent state
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [h.to_status for h in history] == []
    consents = await _consents(db, lead)
    assert [(c.channel, c.decision, c.source) for c in consents] == [
        ("sms", "granted", "messaging:start")
    ]


# ------------------------------------------------------------------- scope ---

async def test_the_same_phone_is_independent_per_tenant(client, db, tenant_a, tenant_b):
    await _send(client, tenant_a, "+15551000005", "STOP")
    await _send(client, tenant_b, "+15551000005", "STOP")

    lead_a = await _lead_for(db, tenant_a, "+15551000005")
    lead_b = await _lead_for(db, tenant_b, "+15551000005")
    assert lead_a.id != lead_b.id
    assert lead_a.tenant_id == tenant_a.id and lead_b.tenant_id == tenant_b.id
    assert lead_a.environment_id == (await _production(db, tenant_a)).id
    assert lead_b.environment_id == (await _production(db, tenant_b)).id
    assert lead_a.status is LeadStatus.DNC and lead_b.status is LeadStatus.DNC


async def test_stop_in_production_never_mutates_a_staging_lead(client, db, tenant_a):
    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    staging_lead = await make_lead(
        db, tenant_a, phone="+15551000006", environment_id=staging.id
    )

    response = await _send(client, tenant_a, "+15551000006", "STOP")
    assert response.status_code == 200

    await db.refresh(staging_lead)
    assert staging_lead.status is LeadStatus.NEW  # untouched by the webhook
    assert await history_for(
        db, staging_lead.tenant_id, staging_lead.environment_id, staging_lead.id
    ) == []

    # the webhook acted in production: a separate DNC lead exists there
    production_lead = await _lead_for(
        db, tenant_a, "+15551000006", environment_id=production.id
    )
    assert production_lead is not None
    assert production_lead.status is LeadStatus.DNC
    assert production_lead.id != staging_lead.id


# ------------------------------------------------------- conversation gate ---

async def test_a_dnc_lead_gets_no_conversation_reply(client, db, tenant_a):
    """Section 2 of the webhook honours an existing opt-out — scoped to the
    webhook's environment. The reply is silent and no conversation turn is
    stored."""
    from app.db.models import Turn

    await _send(client, tenant_a, "+15551000007", "STOP")
    response = await _send(client, tenant_a, "+15551000007", "do you have prices?")
    assert response.status_code == 200

    lead = await _lead_for(db, tenant_a, "+15551000007")
    turns = (
        await db.execute(select(Turn).where(Turn.text == "do you have prices?"))
    ).scalars().all()
    assert turns == []  # never reached the LLM path
    assert lead.status is LeadStatus.DNC


async def test_duplicate_delivery_is_answered_silently(client, db, tenant_a):
    """Twilio retry with the same MessageSid must not create a second consent
    row, a second history entry or a second reply."""
    await _send(client, tenant_a, "+15551000008", "STOP", sid="SMduplicate01")
    response = await _send(client, tenant_a, "+15551000008", "STOP", sid="SMduplicate01")
    assert response.status_code == 200

    lead = await _lead_for(db, tenant_a, "+15551000008")
    consents = await _consents(db, lead)
    assert len(consents) == 1
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert len(history) == 2  # created + the single DNC transition
