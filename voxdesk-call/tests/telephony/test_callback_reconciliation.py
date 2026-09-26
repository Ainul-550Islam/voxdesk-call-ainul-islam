"""Duplicate, out-of-order, missing, DLQ and replay callbacks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import AuditAction, AuditLog, CallStatus
from app.telephony.call_events import accept, scrub
from app.telephony.callback_dlq import note_failure, safe_payload
from app.telephony.callback_reconciliation import ingest_callback, reconcile_call
from app.telephony.provider_errors import ProviderUnavailableError, ProviderValidationError
from app.telephony.replay import replay
from app.tenancy.isolation import Forbidden, NotFound
from tests.conftest import make_call

pytestmark = pytest.mark.asyncio


def _envelope(call, event_id, status="completed", **extra):
    return {
        "provider": "twilio",
        "event_type": "call.status",
        "external_id": call.call_sid,
        "event_id": event_id,
        "tenant_id": call.tenant_id,
        "status": status,
        "duration_seconds": 12,
        "observed_at": datetime.now(timezone.utc),
        "metadata": {"authorization": "Bearer secret", "event": event_id},
        **extra,
    }


async def test_duplicate_and_out_of_order_do_not_double_apply(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    first = await ingest_callback(db, _envelope(call, "evt-1", "completed"))
    assert first["duplicate"] is False
    assert first["side_effect"] is True
    assert call.status is CallStatus.COMPLETED
    second = await ingest_callback(db, _envelope(call, "evt-1", "completed"))
    assert second["duplicate"] is True
    assert second["side_effect"] is False
    late = await ingest_callback(db, _envelope(call, "evt-2", "ringing"))
    assert late["side_effect"] is False
    assert call.status is CallStatus.COMPLETED
    assert "Bearer" not in str(first["event"].safe_metadata)
    assert "authorization" not in first["event"].safe_metadata


async def test_missing_local_record_is_unmatched(db, tenant_a):
    call = await make_call(db, tenant_a)
    result = await ingest_callback(
        db,
        {
            "provider": "twilio",
            "event_type": "call.status",
            "external_id": "CA-not-local",
            "event_id": "evt-missing",
            "tenant_id": tenant_a.id,
            "status": "completed",
        },
    )
    assert result["outcome"] == "missing"
    assert result["event"].status == "unmatched"
    assert call.status is CallStatus.COMPLETED


async def test_stale_snapshot_does_not_overwrite_a_newer_terminal_call(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.COMPLETED)
    call.ended_at = datetime.now(timezone.utc)
    result = await reconcile_call(
        db,
        call,
        {"status": "ringing", "observed_at": datetime.now(timezone.utc) - timedelta(minutes=5)},
    )
    assert result["outcome"] == "stale_ignored"
    assert call.status is CallStatus.COMPLETED


async def test_dlq_retry_exhaustion_and_replay(db, tenant_a, tenant_b, owner_a, viewer_a):
    call = await make_call(db, tenant_a, status=CallStatus.RINGING)
    accepted = await accept(db, _envelope(call, "evt-dlq", "completed"), _boom)
    event = accepted["event"]
    event.status = "received"
    last = None
    for _ in range(5):
        last = await note_failure(db, event, ProviderUnavailableError("down", provider="twilio"))
    assert last["outcome"] == "dead_letter"
    assert event.status == "dead_letter"
    payload = safe_payload(event)
    assert "authorization" not in payload
    assert payload["error_class"] == "unavailable"
    with pytest.raises(Forbidden):
        await replay(
            db,
            tenant_id=tenant_a.id,
            event_id=event.id,
            role=viewer_a.role,
            actor_user_id=viewer_a.id,
        )
    with pytest.raises(NotFound):
        await replay(
            db,
            tenant_id=tenant_b.id,
            event_id=event.id,
            role=owner_a.role,
            actor_user_id=owner_a.id,
        )
    done = await replay(
        db, tenant_id=tenant_a.id, event_id=event.id, role=owner_a.role, actor_user_id=owner_a.id
    )
    assert done["outcome"] == "replayed"
    assert done["outcome"] != "accepted_for_retry"
    assert call.status is CallStatus.COMPLETED
    audits = (
        await db.execute(
            AuditLog.__table__.select().where(AuditLog.action == AuditAction.INTEGRATION_UPDATED)
        )
    ).all()
    assert audits


async def test_permanent_error_dead_letters_without_five_retries(db, tenant_a):
    call = await make_call(db, tenant_a)
    accepted = await accept(db, _envelope(call, "evt-perm", "completed"), _boom)
    event = accepted["event"]
    result = await note_failure(db, event, ProviderValidationError("bad", provider="twilio"))
    assert result["outcome"] == "dead_letter"
    assert event.status == "dead_letter"


async def test_scrub_drops_secrets():
    cleaned = scrub(
        {"authorization": "Bearer abc", "event_id": "1", "recording_url": "https://x?sig=1"}
    )
    assert "authorization" not in cleaned
    assert "recording_url" not in cleaned
    assert cleaned["event_id"] == "1"


async def _boom(_session, _event, _envelope):
    return "ignored", False
