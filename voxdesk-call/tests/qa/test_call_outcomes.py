from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Call, CallStatus, TransferState
from app.qa.evidence import load_call
from app.qa.models import QAEvidence
from app.qa.outcomes import (
    CallOutcomeEvent,
    latest_call_outcomes,
    outcome_metrics,
    record_call_outcome,
)
from app.tenancy.isolation import Conflict, LifecycleDenied
from tests.specialized_agents.test_executor import _scope_and_user

UTC = timezone.utc


async def _completed_call(db, tenant, environment):
    call = Call(
        tenant_id=tenant.id,
        environment_id=environment.id,
        call_sid=f"outcome-{uuid.uuid4().hex}",
        from_number="+15551230000",
        to_number="+15551239999",
        status=CallStatus.COMPLETED,
        ended_at=datetime.now(UTC),
    )
    db.add(call)
    await db.flush()
    return call


@pytest.mark.asyncio
async def test_outcome_events_are_versioned_idempotent_and_evidence_linked(db):
    tenant, _organization, environment, user, scope = await _scope_and_user(
        db, "outcome-versioning"
    )
    call = await _completed_call(db, tenant, environment)

    first = await record_call_outcome(
        db,
        scope,
        call_id=call.id,
        actor_id=user.id,
        outcome="unresolved",
        reason_code="unable_to_resolve",
        idempotency_key="outcome-initial-001",
    )
    await db.commit()
    assert first.event.event_version == 1
    assert first.duplicate is False

    retry = await record_call_outcome(
        db,
        scope,
        call_id=call.id,
        actor_id=user.id,
        outcome="unresolved",
        reason_code="unable_to_resolve",
        idempotency_key="outcome-initial-001",
    )
    assert retry.event.id == first.event.id
    assert retry.duplicate is True

    revised = await record_call_outcome(
        db,
        scope,
        call_id=call.id,
        actor_id=user.id,
        outcome="resolved",
        reason_code="human_completed_task",
        idempotency_key="outcome-revision-002",
    )
    await db.commit()
    assert revised.event.event_version == 2

    history = await latest_call_outcomes(db, scope, call_id=call.id)
    assert [event.event_version for event in history] == [2, 1]
    evidence = await db.scalar(
        select(QAEvidence).where(
            QAEvidence.tenant_id == tenant.id,
            QAEvidence.call_id == call.id,
            QAEvidence.target_id == revised.event.id,
        )
    )
    assert evidence is not None
    assert await load_call(db, tenant.id, call.id) == call

    report = await outcome_metrics(
        db,
        scope,
        start=datetime.now(UTC) - timedelta(days=1),
        end=datetime.now(UTC) + timedelta(days=1),
    )
    assert report["classified_calls"] == 1
    assert report["resolved"] == 1
    assert report["unresolved"] == 0
    assert report["resolution_rate"] == 100.0
    assert report["resolution_rate_denominator"] == "resolved + unresolved; follow-up and handoff are excluded"


@pytest.mark.asyncio
async def test_idempotency_conflict_and_handoff_requires_connected_transfer(db):
    tenant, _organization, environment, user, scope = await _scope_and_user(
        db, "outcome-conflicts"
    )
    call = await _completed_call(db, tenant, environment)

    await record_call_outcome(
        db,
        scope,
        call_id=call.id,
        actor_id=user.id,
        outcome="resolved",
        reason_code="agent_completed_task",
        idempotency_key="outcome-conflict-01",
    )
    await db.commit()
    with pytest.raises(Conflict):
        await record_call_outcome(
            db,
            scope,
            call_id=call.id,
            actor_id=user.id,
            outcome="unresolved",
            reason_code="unable_to_resolve",
            idempotency_key="outcome-conflict-01",
        )

    with pytest.raises(LifecycleDenied, match="connected transfer"):
        await record_call_outcome(
            db,
            scope,
            call_id=call.id,
            actor_id=user.id,
            outcome="handed_off",
            reason_code="transfer_connected",
            idempotency_key="outcome-handoff-01",
        )

    call.transfer_state = TransferState.CONNECTED
    await db.flush()
    handoff = await record_call_outcome(
        db,
        scope,
        call_id=call.id,
        actor_id=user.id,
        outcome="handed_off",
        reason_code="transfer_connected",
        idempotency_key="outcome-handoff-02",
    )
    assert handoff.event.event_version == 2
