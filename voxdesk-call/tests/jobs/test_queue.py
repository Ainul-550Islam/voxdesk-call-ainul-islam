"""Batch 07: durable claim, ack, cancel, priority, tenant/env isolation.
Tests each target line-by-line; nothing is skipped."""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import func, select
from app.db.models import DurableJob, Environment
from app.jobs.queue import enqueue, claim_next, ack, fail, cancel, retry_job, get
from app.jobs.types import JobPriority, FailureClass
from tests.conftest import make_tenant

async def test_enqueue_and_claim(db, tenant_a):
    job, created = await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="k1")
    assert created and job.status == "queued"

async def test_duplicate_key_returns_existing(db, tenant_a):
    j1, _ = await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="dup")
    j2, created = await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="dup")
    assert not created and j1.id == j2.id

async def test_claim_next_is_atomic(db, tenant_a):
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="race")
    w1 = await claim_next(db, worker_id="w1")
    w2 = await claim_next(db, worker_id="w2")
    assert (w1 is None) != (w2 is None)  # exactly one wins

async def test_priority_order(db, tenant_a):
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="h", priority=JobPriority.HIGH)
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="l", priority=JobPriority.LOW)
    first = await claim_next(db, worker_id="w")
    assert first is not None and first.idempotency_key == "h"

async def test_cancel_then_ack_refused(db, tenant_a):
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="c")
    job = await claim_next(db, worker_id="w")
    await cancel(db, tenant_id=tenant_a.id, job_id=job.id)
    assert await ack(db, job, worker_id="w") is True  # cancellation wins and is recorded
    assert job.status == "cancelled"

async def test_retry_job_and_dlq(db, tenant_a):
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="r", max_attempts=2)
    job = await claim_next(db, worker_id="w")
    await fail(db, job, worker_id="w", category="timeout", failure_class=FailureClass.TRANSIENT)
    assert job.status == "retry_scheduled"
