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

from app.compliance_v2.dsar import service as dsar_service

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


async def test_dsar_requires_a_verified_identity_before_it_runs(db, tenant_a, owner_a):
    request = await dsar_service.create_request(
        db,
        tenant_id=tenant_a.id,
        kind="export",
        subject_type="user",
        subject_key=owner_a.email,
        requested_by=owner_a.id,
    )
    await db.commit()

    with pytest.raises(dsar_service.IdentityNotVerified):
        await dsar_service.execute(db, tenant_id=tenant_a.id, request_id=request.id)

    await dsar_service.mark_verified(
        db,
        tenant_id=tenant_a.id,
        request_id=request.id,
        actor_user_id=owner_a.id,
        evidence="passport-check-1",
    )
    await db.commit()
    assert request.identity_verified is True


async def test_dsar_export_produces_a_signed_and_verifiable_bundle(
    db, tenant_a, owner_a, compliance_settings
):
    request = await dsar_service.create_request(
        db,
        tenant_id=tenant_a.id,
        kind="export",
        subject_type="user",
        subject_key=owner_a.email,
        requested_by=owner_a.id,
        settings=compliance_settings,
    )
    await db.commit()
    await dsar_service.mark_verified(
        db, tenant_id=tenant_a.id, request_id=request.id, actor_user_id=owner_a.id
    )
    await db.commit()

    result = await dsar_service.execute(
        db, tenant_id=tenant_a.id, request_id=request.id, actor_user_id=owner_a.id,
        settings=compliance_settings,
    )
    await db.commit()

    assert result.request.status == dsar_service.STATUS_COMPLETED
    from pathlib import Path

    bundle = Path(result.bundle_path)
    assert bundle.is_file()
    from app.compliance_v2.audit_integrity import signing

    pem = signing.load_signer(settings=compliance_settings).public_key_pem
    verification = dsar_service.verify_bundle(bundle, public_key_pem=pem)
    assert verification["ok"] is True, verification
    ledger = await dsar_service.ledger_for(
        db, tenant_id=tenant_a.id, request_id=request.id
    )
    assert ledger, "an executed request must leave a per-table ledger"


async def test_legal_hold_blocks_erasure(db, tenant_a, owner_a, compliance_settings):
    request = await dsar_service.create_request(
        db,
        tenant_id=tenant_a.id,
        kind="erase",
        subject_type="user",
        subject_key=owner_a.email,
        requested_by=owner_a.id,
    )
    await db.commit()
    await dsar_service.mark_verified(
        db, tenant_id=tenant_a.id, request_id=request.id, actor_user_id=owner_a.id
    )
    await dsar_service.set_legal_hold(
        db,
        tenant_id=tenant_a.id,
        request_id=request.id,
        hold=True,
        actor_user_id=owner_a.id,
        reason="litigation hold 42",
    )
    await db.commit()

    blocked = await dsar_service.execute(
        db, tenant_id=tenant_a.id, request_id=request.id, actor_user_id=owner_a.id,
        settings=compliance_settings,
    )
    await db.commit()

    assert blocked.bundle_id == ""
    assert blocked.request.summary["blocked_by_legal_hold"] is True
    assert blocked.request.status != dsar_service.STATUS_COMPLETED


async def test_overdue_tick_flags_a_late_request(db, tenant_a, owner_a, compliance_settings):
    request = await dsar_service.create_request(
        db,
        tenant_id=tenant_a.id,
        kind="export",
        subject_type="user",
        subject_key=owner_a.email,
        requested_by=owner_a.id,
        settings=compliance_settings,
        now=_now() - dt.timedelta(days=40),
    )
    await db.commit()

    outcome = await dsar_service.tick(db, tenant_id=tenant_a.id)
    await db.commit()

    assert outcome["flagged"] >= 1
    assert request.due_at is not None
    assert request.summary["overdue_flagged"]


# --------------------------------------------------------------------- HIPAA ---


