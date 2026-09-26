"""Recording state, access, retention and signed grants."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.models import AuditLog, Call
from app.telephony.media_storage import safe_reference, verify
from app.telephony.recording import (
    authorize_read,
    grant_for,
    open_grant,
    request_recording,
    transition,
)
from app.telephony.recording_policy import save
from app.tenancy.isolation import Forbidden, NotFound
from tests.conftest import make_call

pytestmark = pytest.mark.asyncio


async def _ready(db, tenant):
    tenant.record_calls = True
    await db.commit()
    call = await make_call(db, tenant)
    row = await request_recording(
        db, call, provider="twilio", consent_category="one_party", consent_state="not_required"
    )
    assert transition(row, "recording") == "applied"
    assert transition(row, "recording") == "duplicate"
    assert transition(row, "processing") == "applied"
    assert transition(row, "ready") == "applied"
    return call, row


async def test_state_machine_and_duplicate_do_not_regress(db, tenant_a):
    _call, row = await _ready(db, tenant_a)
    assert row.state == "ready"
    assert transition(row, "recording") == "illegal"
    assert row.state == "ready"
    assert transition(row, "deletion_pending") == "applied"
    assert transition(row, "deleted") == "applied"
    assert transition(row, "ready") == "terminal"


async def test_consent_and_policy_block_capture(db, tenant_a):
    call = await make_call(db, tenant_a)
    with pytest.raises(Forbidden):
        await request_recording(db, call, provider="twilio", consent_category="one_party")
    tenant_a.record_calls = True
    await db.commit()
    with pytest.raises(Forbidden):
        await request_recording(db, call, provider="twilio", consent_category="unspecified")
    with pytest.raises(Forbidden):
        await request_recording(
            db, call, provider="twilio", consent_category="all_party", consent_state="unknown"
        )
    allowed = await request_recording(
        db, call, provider="twilio", consent_category="all_party", consent_state="granted"
    )
    assert allowed.state == "requested"


async def test_access_is_permissioned_audited_and_expires(
    db, tenant_a, tenant_b, owner_a, viewer_a
):
    _call, row = await _ready(db, tenant_a)
    with pytest.raises(NotFound):
        await authorize_read(
            db,
            tenant_id=tenant_b.id,
            recording_id=row.id,
            role=owner_a.role,
            actor_user_id=owner_a.id,
        )
    with pytest.raises(Forbidden):
        await authorize_read(
            db,
            tenant_id=tenant_a.id,
            recording_id=row.id,
            role=viewer_a.role,
            actor_user_id=viewer_a.id,
        )
    seen = await authorize_read(
        db, tenant_id=tenant_a.id, recording_id=row.id, role=owner_a.role, actor_user_id=owner_a.id
    )
    assert seen.id == row.id
    audits = (await db.execute(AuditLog.__table__.select())).all()
    assert any("recording_access" in str(item.detail) for item in audits)
    grant = grant_for(row, role=owner_a.role, now=1_700_000_000)
    assert verify(
        grant.token, tenant_id=row.tenant_id, recording_id=row.id, now=1_700_000_000
    ).allowed
    expired = verify(
        grant.token, tenant_id=row.tenant_id, recording_id=row.id, now=grant.expires_at + 1
    )
    assert expired.reason == "expired"
    opened = open_grant(row, grant.token, role=owner_a.role, now=grant.expires_at + 5)
    assert opened["allowed"] is False
    assert opened["url"] is None
    with pytest.raises(Forbidden):
        open_grant(row, grant.token, role=viewer_a.role, now=1_700_000_000)
    assert safe_reference("https://api.twilio.com/recording?sig=abc") is None


async def test_other_tenant_cannot_change_policy(db, tenant_a, tenant_b):
    from app.telephony.recording_policy import get_owned

    saved = await save(db, tenant_a, enabled=True, retention_days=30)
    with pytest.raises(NotFound):
        await get_owned(db, tenant_b.id, saved.id)


async def test_missing_provider_id_and_callback_duplicate(db, tenant_a):
    from app.telephony.recording import apply_provider_event

    _call, row = await _ready(db, tenant_a)
    updated, outcome = await apply_provider_event(
        db,
        tenant_id=tenant_a.id,
        provider="twilio",
        external_id="",
        target_state="ready",
        call_id=row.call_id,
    )
    assert outcome == "duplicate"
    assert updated.id == row.id
    missing, reason = await apply_provider_event(
        db,
        tenant_id=tenant_a.id,
        provider="twilio",
        external_id="RE-missing",
        target_state="ready",
    )
    assert missing is None
    assert reason == "missing"


async def test_retention_deletes_recording_unless_held(db, tenant_a):
    from app.core.retention import purge_expired_calls

    tenant_a.record_calls = True
    await db.commit()
    call = await make_call(db, tenant_a)
    call.started_at = datetime.utcnow() - timedelta(days=400)
    await request_recording(
        db, call, provider="twilio", consent_category="one_party", consent_state="granted"
    )
    await db.commit()
    summary = await purge_expired_calls(db, days=365)
    assert summary["purged_calls"] == 1
    assert summary["purged_recordings"] == 1
    gone = await db.get(Call, call.id)
    assert gone is None

    call2 = await make_call(db, tenant_a)
    call2.started_at = datetime.utcnow() - timedelta(days=400)
    await save(db, tenant_a, enabled=True, retention_days=30, legal_hold=True)
    held = await request_recording(
        db, call2, provider="twilio", consent_category="one_party", consent_state="granted"
    )
    await db.commit()
    kept = await purge_expired_calls(db, days=30)
    assert kept["purged_calls"] == 0
    assert await db.get(Call, call2.id) is not None
    assert held.state != "deleted"
