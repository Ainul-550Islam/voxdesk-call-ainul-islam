from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.api.v1.telephony_webhook_routes import _commit_webhook_transaction
from app.db.telephony_models import TelephonyCallSession, TelephonyProviderEvent
from app.telephony import concurrency_policy
from app.telephony.enums import (
    CallDirection,
    ProviderEventType,
    TelephonyCallState,
    TelephonyProviderName,
)
from app.telephony.schemas import NormalizedProviderWebhookEvent
from app.telephony.webhooks import TelephonyWebhookProcessor


class _CommitFailSession:
    def __init__(self) -> None:
        self.rollback_called = False

    async def commit(self) -> None:
        raise RuntimeError("database commit failed")

    async def rollback(self) -> None:
        self.rollback_called = True


class _CommitSuccessSession:
    def __init__(self, timeline: list[object]) -> None:
        self.timeline = timeline

    async def commit(self) -> None:
        self.timeline.append("commit")

    async def rollback(self) -> None:  # pragma: no cover - success case
        raise AssertionError("rollback must not be called after a successful commit")


def _webhook_event(*, call_sid: str, event_id: str, state: TelephonyCallState):
    event_type = (
        ProviderEventType.CALL_COMPLETED
        if state == TelephonyCallState.COMPLETED
        else ProviderEventType.CALL_IN_PROGRESS
    )
    return NormalizedProviderWebhookEvent(
        provider=TelephonyProviderName.TWILIO,
        provider_event_id=event_id,
        event_type=event_type,
        provider_call_id=call_sid,
        from_number="+14155550100",
        to_number="+14155550101",
        direction=CallDirection.INBOUND,
        call_state=state,
        raw_payload={},
    )


async def _seed_call_session(db, tenant, *, status: TelephonyCallState):
    from app.db.models import Environment

    environment_id = await db.scalar(
        select(Environment.id)
        .where(Environment.tenant_id == tenant.id)
        .order_by(Environment.is_default.desc())
        .limit(1)
    )
    call = TelephonyCallSession(
        tenant_id=tenant.id,
        environment_id=environment_id,
        provider=TelephonyProviderName.TWILIO.value,
        provider_call_id=f"CA-lease-{uuid.uuid4().hex[:12]}",
        direction=CallDirection.INBOUND.value,
        from_number="+14155550100",
        to_number="+14155550101",
        status=status.value,
        started_at=datetime.now(timezone.utc),
    )
    db.add(call)
    await db.flush()
    return call


@pytest.mark.asyncio
async def test_webhook_commit_failure_compensates_queued_acquisition(monkeypatch) -> None:
    released: list[tuple[str, str]] = []

    async def fake_release(tenant_id, call_id) -> None:
        released.append((str(tenant_id), str(call_id)))

    monkeypatch.setattr(concurrency_policy, "release_call_slot", fake_release)
    session = _CommitFailSession()
    processor = TelephonyWebhookProcessor(session)  # type: ignore[arg-type]
    processor._queue_call_slot_acquisition("tenant-1", "call-1")
    processor._queue_call_slot_release("tenant-1", "terminal-call")

    with pytest.raises(RuntimeError, match="database commit failed"):
        await _commit_webhook_transaction(session, processor)  # type: ignore[arg-type]

    assert session.rollback_called is True
    assert released == [("tenant-1", "call-1")]
    assert processor._pending_slot_acquisitions == set()
    # Terminal releases must not happen when the database transaction failed.
    assert processor._pending_slot_releases == {("tenant-1", "terminal-call")}


@pytest.mark.asyncio
async def test_webhook_terminal_transition_releases_lease_after_commit(db, tenant_a, monkeypatch):
    timeline: list[object] = []

    async def fake_release(tenant_id, call_id) -> None:
        timeline.append(("release", str(tenant_id), str(call_id)))

    monkeypatch.setattr(concurrency_policy, "release_call_slot", fake_release)
    call = await _seed_call_session(
        db, tenant_a, status=TelephonyCallState.IN_PROGRESS
    )
    processor = TelephonyWebhookProcessor(db)
    event = _webhook_event(
        call_sid=call.provider_call_id,
        event_id=f"evt-terminal-{uuid.uuid4().hex[:8]}",
        state=TelephonyCallState.COMPLETED,
    )

    result = await processor.apply_normalized_event(normalized=event)
    assert result.call_state == TelephonyCallState.COMPLETED.value
    assert processor._pending_slot_releases == {
        (str(tenant_a.id), str(call.id))
    }

    # Exercise the production wrapper; DB commit must precede the external lease release.
    original_commit = db.commit

    async def commit_with_marker() -> None:
        await original_commit()
        timeline.append("commit")

    monkeypatch.setattr(db, "commit", commit_with_marker)
    await _commit_webhook_transaction(db, processor)

    assert timeline == ["commit", ("release", str(tenant_a.id), str(call.id))]
    assert processor._pending_slot_releases == set()


@pytest.mark.asyncio
async def test_duplicate_terminal_webhook_releases_stale_lease_after_commit(
    db, tenant_a, monkeypatch
) -> None:
    released: list[tuple[str, str]] = []

    async def fake_release(tenant_id, call_id) -> None:
        released.append((str(tenant_id), str(call_id)))

    monkeypatch.setattr(concurrency_policy, "release_call_slot", fake_release)
    call = await _seed_call_session(db, tenant_a, status=TelephonyCallState.COMPLETED)
    event_id = f"evt-duplicate-terminal-{uuid.uuid4().hex[:8]}"
    db.add(
        TelephonyProviderEvent(
            tenant_id=tenant_a.id,
            environment_id=call.environment_id,
            provider=TelephonyProviderName.TWILIO.value,
            provider_event_id=event_id,
            event_type=ProviderEventType.CALL_COMPLETED.value,
            call_id=call.id,
            provider_call_id=call.provider_call_id,
            payload_hash="already-processed",
            processing_status="processed",
            normalized_payload={},
        )
    )
    await db.flush()

    processor = TelephonyWebhookProcessor(db)
    result = await processor.apply_normalized_event(
        normalized=_webhook_event(
            call_sid=call.provider_call_id,
            event_id=event_id,
            state=TelephonyCallState.COMPLETED,
        )
    )

    assert result.duplicate is True
    assert processor._pending_slot_releases == {
        (str(tenant_a.id), str(call.id))
    }
    await _commit_webhook_transaction(db, processor)

    assert released == [(str(tenant_a.id), str(call.id))]
    assert processor._pending_slot_releases == set()
