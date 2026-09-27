"""Telephony callback → lead lifecycle (Batch 06).

The Twilio status callback grades leads only through the canonical
lifecycle: the grade is a validated, compare-and-set transition with history
(source ``telephony``), it happens only when call and lead agree on tenant
and environment, repeated callbacks never double-grade, and a do-not-call
lead is never touched or reversed by a callback.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import CallDirection, CallStatus, Environment, LeadStatus
from app.leads import lifecycle
from app.leads.activities import history_for
from tests.conftest import make_call, make_lead


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


async def _queued_lead(db, tenant, phone: str, **lead_over):
    """A lead exactly as the dialer leaves it: QUEUED with one attempt."""
    lead = await make_lead(db, tenant, phone=phone, **lead_over)
    await lifecycle.transition(db, lead, "queued", reason="claim", source="test")
    lead.attempts = 1
    await db.commit()
    return lead


async def _outbound_call(db, tenant, lead, *, lead_score=80):
    call = await make_call(
        db, tenant,
        direction=CallDirection.OUTBOUND,
        status=CallStatus.RINGING,
        lead_id=lead.id,
        to_number=lead.phone,
        from_number=tenant.twilio_number,
        lead_score=lead_score,
        duration_seconds=0,
    )
    return call


async def _post_status(client, call, status: str, duration: str = "30"):
    return await client.post(
        "/telephony/status",
        data={"CallSid": call.call_sid, "CallStatus": status,
              "CallDuration": duration},
    )


async def _telephony_history(db, lead):
    rows = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    return [row for row in rows if row.source == "telephony"]


# ------------------------------------------------------------- the grades ---

async def test_completed_high_score_qualifies_with_history(client, db, tenant_a):
    lead = await _queued_lead(db, tenant_a, "+15550800001")
    call = await _outbound_call(db, tenant_a, lead, lead_score=80)

    response = await _post_status(client, call, "completed", duration="30")
    assert response.status_code == 200

    await db.refresh(lead)
    assert lead.status is LeadStatus.QUALIFIED
    assert lead.score == 80
    history = await _telephony_history(db, lead)
    assert len(history) == 1
    assert history[0].from_status == "queued"
    assert history[0].to_status == "qualified"
    assert history[0].reason == "twilio_status_callback"


async def test_completed_low_score_marks_called(client, db, tenant_a):
    lead = await _queued_lead(db, tenant_a, "+15550800002")
    call = await _outbound_call(db, tenant_a, lead, lead_score=10)

    assert (await _post_status(client, call, "completed")).status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.CALLED
    history = await _telephony_history(db, lead)
    assert [h.to_status for h in history] == ["called"]


async def test_short_completed_call_does_not_grade(client, db, tenant_a):
    lead = await _queued_lead(db, tenant_a, "+15550800003")
    call = await _outbound_call(db, tenant_a, lead)

    assert (await _post_status(client, call, "completed", duration="5")).status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.QUEUED  # <= 10s: no grade, no history
    assert await _telephony_history(db, lead) == []


async def test_failed_at_attempt_cap_marks_failed(client, db, tenant_a):
    lead = await _queued_lead(db, tenant_a, "+15550800004")
    lead.attempts = tenant_a.max_call_attempts
    await db.commit()
    call = await _outbound_call(db, tenant_a, lead)

    assert (await _post_status(client, call, "failed", duration="0")).status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.FAILED
    history = await _telephony_history(db, lead)
    assert [(h.from_status, h.to_status) for h in history] == [("queued", "failed")]


async def test_no_answer_under_the_cap_leaves_the_lead_alone(client, db, tenant_a):
    lead = await _queued_lead(db, tenant_a, "+15550800005")
    call = await _outbound_call(db, tenant_a, lead)

    assert (await _post_status(client, call, "no-answer", duration="0")).status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.QUEUED
    assert await _telephony_history(db, lead) == []


# -------------------------------------------------------------- idempotence ---

async def test_repeated_callback_never_double_grades(client, db, tenant_a):
    lead = await _queued_lead(db, tenant_a, "+15550800006")
    call = await _outbound_call(db, tenant_a, lead)

    assert (await _post_status(client, call, "completed")).status_code == 200
    assert (await _post_status(client, call, "completed")).status_code == 200
    assert (await _post_status(client, call, "completed")).status_code == 200

    await db.refresh(lead)
    assert lead.status is LeadStatus.QUALIFIED
    assert len(await _telephony_history(db, lead)) == 1


# ------------------------------------------------------------- scope guards ---

async def test_cross_environment_lead_is_never_graded(client, db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await _queued_lead(
        db, tenant_a, "+15550800008", environment_id=staging.id
    )
    # the call itself is a production call (hook default) pointing at the
    # staging lead's id — exactly the legacy ambiguity Batch 06 closes
    production = await _production(db, tenant_a)
    call = await make_call(
        db, tenant_a,
        direction=CallDirection.OUTBOUND,
        status=CallStatus.RINGING,
        lead_id=lead.id,
        environment_id=production.id,
        to_number=lead.phone,
        from_number=tenant_a.twilio_number,
        lead_score=95,
    )

    assert (await _post_status(client, call, "completed")).status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.QUEUED  # untouched
    assert await _telephony_history(db, lead) == []


async def test_dnc_lead_is_never_touched_or_reversed(client, db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15550800009")
    await lifecycle.transition(
        db, lead, "do_not_call", reason="consent:voice:denied", source="consent"
    )
    await db.commit()
    call = await _outbound_call(db, tenant_a, lead)

    assert (await _post_status(client, call, "completed")).status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC  # terminal
    assert await _telephony_history(db, lead) == []
