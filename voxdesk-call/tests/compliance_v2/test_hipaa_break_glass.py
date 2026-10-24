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

from app.compliance_v2.hipaa import break_glass

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


async def test_break_glass_needs_a_second_approver(db, tenant_a, admin_a, owner_a, compliance_settings):
    grant = await break_glass.request_grant(
        db,
        tenant_id=tenant_a.id,
        requested_by=admin_a.id,
        reason="patient chart needed during an outage",
        scope={"resource_types": ["transcript"], "call_id": "call-1"},
        settings=compliance_settings,
    )
    await db.commit()

    assert grant.status == "pending_approval"
    assert await break_glass.authorize_read(
        db, tenant_id=tenant_a.id, user_id=admin_a.id, resource_type="transcript"
    ) is None

    approved = await break_glass.approve_grant(
        db,
        tenant_id=tenant_a.id,
        grant_id=grant.id,
        approver_id=owner_a.id,
        settings=compliance_settings,
    )
    await db.commit()

    assert approved.status in {"active", "approved"}
    assert break_glass.is_active(approved) is True
    authorised = await break_glass.authorize_read(
        db, tenant_id=tenant_a.id, user_id=admin_a.id, resource_type="transcript"
    )
    assert authorised is not None and authorised.id == grant.id


async def test_break_glass_rejects_a_trivial_reason(db, tenant_a, admin_a, compliance_settings):
    with pytest.raises(break_glass.BreakGlassError):
        await break_glass.request_grant(
            db,
            tenant_id=tenant_a.id,
            requested_by=admin_a.id,
            reason="ok",
            scope={},
            settings=compliance_settings,
        )


async def test_break_glass_expires_on_its_own(db, tenant_a, admin_a, owner_a, compliance_settings):
    grant = await break_glass.request_grant(
        db,
        tenant_id=tenant_a.id,
        requested_by=admin_a.id,
        reason="incident investigation with approved scope",
        scope={"resource_types": ["transcript"]},
        settings=compliance_settings,
    )
    await db.commit()
    await break_glass.approve_grant(
        db,
        tenant_id=tenant_a.id,
        grant_id=grant.id,
        approver_id=owner_a.id,
        settings=compliance_settings,
    )
    await db.commit()

    later = _now() + dt.timedelta(hours=compliance_settings.break_glass_max_minutes + 1)
    outcome = await break_glass.expire_due(db, now=later)
    await db.commit()
    await db.refresh(grant)

    assert outcome["expired"] >= 1
    assert grant.status in {"expired", "revoked"}
    assert await break_glass.authorize_read(
        db, tenant_id=tenant_a.id, user_id=admin_a.id, resource_type="transcript", now=later
    ) is None


