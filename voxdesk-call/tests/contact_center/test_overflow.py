"""Overflow tells the truth. No callback without a job. No transfer without telephony."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.contact_center.models import Queue
from app.contact_center.overflow import decide, normalize_policy
from app.contact_center.queues import create_queue
from app.contact_center.service import assign_next, enqueue
from app.db.models import DurableJob
from app.telephony.transfer_service import TransferOutcome, TransferResult, TransferState
from tests.acd_support import live_call, production


def test_missing_escalation_number_is_not_success():
    queue = Queue(
        tenant_id=__import__("uuid").uuid4(),
        environment_id=__import__("uuid").uuid4(),
        name="Esc",
        overflow_policy={"kind": "escalate", "escalation_number": "", "max_wait_seconds": 1},
    )
    decision = decide(queue)
    assert decision.action == "escalate_unavailable"
    with pytest.raises(Exception):
        normalize_policy({"kind": "escalate", "escalation_number": ""})


@pytest.mark.asyncio
async def test_callback_is_requested_only_when_the_job_exists(db, tenant_a):
    env = await production(db, tenant_a)
    queue = await create_queue(
        db,
        tenant_id=tenant_a.id,
        name="Callbacks",
        environment_id=env.id,
        overflow_policy={"kind": "callback", "max_wait_seconds": 30},
    )
    await db.commit()
    call = await live_call(db, tenant_a, env)
    queue.overflow_policy = {
        "kind": "callback",
        "max_wait_seconds": 30,
        "max_waiting": 0,
        "escalation_number": "",
    }
    await db.commit()
    waiting = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert waiting.outcome == "waiting"
    waiting.entry.enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    await db.commit()
    result = await assign_next(db, tenant_id=tenant_a.id, queue_id=queue.id)
    await db.commit()
    assert result.callback_requested is True
    assert result.outcome == "callback_requested"
    assert result.transfer_accepted is False
    job = (
        await db.execute(
            select(DurableJob).where(DurableJob.idempotency_key == f"acd-callback:{result.entry.id}")
        )
    ).scalar_one()
    assert job.job_type == "acd_callback"
    assert job.payload["call_id"] == str(call.id)
    assert result.entry.status == "overflowed"


@pytest.mark.asyncio
async def test_escalate_success_requires_telephony_acceptance(db, tenant_a, monkeypatch):
    env = await production(db, tenant_a)
    queue = await create_queue(
        db,
        tenant_id=tenant_a.id,
        name="Escalate",
        environment_id=env.id,
        overflow_policy={
            "kind": "escalate",
            "escalation_number": "+15557654321",
            "max_wait_seconds": 30,
        },
    )
    await db.commit()
    call = await live_call(db, tenant_a, env)

    async def _rejected(*_args, **_kwargs):
        return TransferResult(
            outcome=TransferOutcome.TRANSFER_FAILED,
            message="no",
            state=TransferState.FAILED,
        )

    monkeypatch.setattr("app.telephony.transfer_service.request_transfer", _rejected)
    waiting = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    waiting.entry.enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    await db.commit()
    failed = await assign_next(db, tenant_id=tenant_a.id, queue_id=queue.id)
    await db.commit()
    assert failed.transfer_accepted is False
    assert failed.outcome == "escalate_failed"
    assert failed.assignment is None

    call_two = await live_call(db, tenant_a, env)

    async def _accepted(*_args, **_kwargs):
        return TransferResult(
            outcome=TransferOutcome.TRANSFER_STARTED,
            message="ok",
            state=TransferState.DIALING,
        )

    monkeypatch.setattr("app.telephony.transfer_service.request_transfer", _accepted)
    waiting_two = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call_two.id)
    waiting_two.entry.enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    await db.commit()
    accepted = await assign_next(db, tenant_id=tenant_a.id, queue_id=queue.id)
    await db.commit()
    assert accepted.transfer_accepted is True
    assert accepted.outcome == "escalate_accepted"
    assert accepted.assignment is None
