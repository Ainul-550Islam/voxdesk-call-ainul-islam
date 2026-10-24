"""Incidents, breach clocks, access-review campaigns, DSAR and HIPAA safeguards.

These are the controls an auditor asks to see operating: an incident has a
timeline nobody can rewrite, a regulatory clock records when notice was given,
an access-review campaign can only be decided by its reviewers, a DSAR export is
signed and can be re-verified later, and break-glass access needs a second
person and expires on its own.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.compliance_v2.incidents import breach_clock, service as incident_service

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


async def test_incident_lifecycle_records_a_timeline(db, tenant_a, owner_a):
    incident = await incident_service.create_incident(
        db,
        tenant_id=tenant_a.id,
        title="Credential stuffing spike",
        summary="Elevated failed logins from one ASN",
        severity="sev2",
        created_by=None,
        detected_at=_now(),
    )
    await db.commit()

    advanced = await incident_service.advance(
        db,
        incident_id=incident.id,
        to_status="triaged",
        actor_user_id=owner_a.id,
        note="confirmed as an attack",
        tenant_id=tenant_a.id,
    )
    await db.commit()

    entries = await incident_service.timeline(db, incident_id=incident.id)

    assert advanced.status == "triaged"
    assert advanced.triaged_at is not None
    assert [entry.entry_type for entry in entries] == ["detected", "triaged"]


async def test_incident_advance_rejects_an_unknown_status(db, tenant_a, owner_a):
    incident = await incident_service.create_incident(
        db,
        tenant_id=tenant_a.id,
        title="Test",
        summary="",
        severity="sev3",
        created_by=None,
    )
    await db.commit()

    with pytest.raises(incident_service.InvalidTransition):
        await incident_service.advance(
            db,
            incident_id=incident.id,
            to_status="launched",
            actor_user_id=owner_a.id,
            tenant_id=tenant_a.id,
        )


async def test_incident_lookup_is_tenant_scoped(db, tenant_a, tenant_b):
    incident = await incident_service.create_incident(
        db, tenant_id=tenant_a.id, title="Scoped", summary="", severity="sev3", created_by=None
    )
    await db.commit()

    with pytest.raises(incident_service.IncidentNotFound):
        await incident_service.get_incident(db, incident_id=incident.id, tenant_id=tenant_b.id)


async def test_breach_clock_deadline_follows_the_jurisdiction(db, tenant_a):
    incident = await incident_service.create_incident(
        db,
        tenant_id=tenant_a.id,
        title="Lost backup tape",
        summary="",
        severity="sev1",
        created_by=None,
        personal_data_involved=True,
        detected_at=_now(),
    )
    await db.commit()

    clocks = await breach_clock.start_clocks_for_incident(
        db,
        incident_id=incident.id,
        tenant_id=tenant_a.id,
        jurisdictions=["eu_gdpr", "us_hipaa"],
        detected_at=incident.detected_at,
    )
    await db.commit()

    by_jurisdiction = {clock.jurisdiction: clock for clock in clocks}
    assert set(by_jurisdiction) == {"eu_gdpr", "us_hipaa"}
    window = by_jurisdiction["eu_gdpr"].deadline_at - by_jurisdiction["eu_gdpr"].started_at
    assert window == dt.timedelta(days=3)
    assert by_jurisdiction["us_hipaa"].deadline_at > by_jurisdiction["eu_gdpr"].deadline_at
    assert "GDPR" in breach_clock.obligation_for("eu_gdpr")


async def test_recording_a_notification_marks_the_clock_met(db, tenant_a, owner_a):
    incident = await incident_service.create_incident(
        db,
        tenant_id=tenant_a.id,
        title="Phishing",
        summary="",
        severity="sev2",
        created_by=None,
        personal_data_involved=True,
        detected_at=_now() - dt.timedelta(hours=1),
    )
    await db.commit()
    clock = await breach_clock.start_clock(
        db,
        incident_id=incident.id,
        tenant_id=tenant_a.id,
        jurisdiction="eu_gdpr",
        detected_at=incident.detected_at,
    )
    await db.commit()

    notified = await breach_clock.record_notification(
        db,
        clock_id=clock.id,
        tenant_id=tenant_a.id,
        notified_at=_now(),
        channel="email",
        recipient="dpa@example.gov",
        proof_reference="CASE-1234",
        actor_user_id=owner_a.id,
    )
    await db.commit()

    document = breach_clock.as_dict(notified)
    assert notified.status in {"notified", "met"}
    assert document["notification_proof"]["channel"] == "email"
    assert document["notification_proof"]["within_deadline"] is True


async def test_clock_tick_escalates_an_overdue_notice(db, tenant_a):
    incident = await incident_service.create_incident(
        db,
        tenant_id=tenant_a.id,
        title="Old breach",
        summary="",
        severity="sev1",
        created_by=None,
        personal_data_involved=True,
        detected_at=_now() - dt.timedelta(days=7),
    )
    await db.commit()
    await breach_clock.start_clock(
        db,
        incident_id=incident.id,
        tenant_id=tenant_a.id,
        jurisdiction="eu_gdpr",
        detected_at=incident.detected_at,
    )
    await db.commit()

    outcome = await breach_clock.tick(db)
    await db.commit()

    assert outcome["escalated"] >= 1


# ------------------------------------------------------------ access reviews ---


