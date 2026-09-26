"""Durable job claims, leases, isolation and idempotency."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.models import DurableJob, Environment, UserRole
from app.jobs.idempotency import begin
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.repository import (
    claim_next_job,
    create_job,
    recover_expired_jobs,
    replay_dead_letter,
)
from app.jobs.worker import run_once
from app.resources.exceptions import ResourceUnauthorized
from app.tenancy.isolation import BoundaryDenied
from tests.conftest import make_tenant


async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id, Environment.kind == "production"
            )
        )
    ).scalar_one()


async def _staging(db, tenant) -> Environment:
    row = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging", status="active"
    )
    db.add(row)
    await db.flush()
    return row


async def test_duplicate_idempotency_key_returns_the_same_job(db, tenant_a):
    first, created = await create_job(
        db, tenant_id=tenant_a.id, job_type="ping", idempotency_key="same-key"
    )
    second, again = await create_job(
        db, tenant_id=tenant_a.id, job_type="ping", idempotency_key="same-key"
    )
    assert created is True and again is False
    assert first.id == second.id
    count = await db.scalar(select(func.count()).select_from(DurableJob))
    assert count == 1


async def test_idempotency_record_is_unique(db, tenant_a):
    row, created = await begin(db, tenant_id=tenant_a.id, key="event-1")
    again, second = await begin(db, tenant_id=tenant_a.id, key="event-1")
    assert created is True and second is False
    assert row.id == again.id


async def test_cross_tenant_job_is_invisible(db, tenant_a, tenant_b):
    job, _created = await create_job(
        db, tenant_id=tenant_a.id, job_type="ping", idempotency_key="owned"
    )
    from app.jobs.repository import get_job

    assert await get_job(db, job.id, tenant_id=tenant_b.id) is None
    from app.automation.dlq import replay

    with pytest.raises(BoundaryDenied):
        await replay(db, tenant_id=tenant_b.id, job_id=job.id, role=UserRole.OWNER)


async def test_single_winner_on_concurrent_claim(concurrent_sessionmaker):
    maker = concurrent_sessionmaker
    async with maker() as db:
        tenant = await make_tenant(db, "Race")
        tenant_id = tenant.id
    async with maker() as db:
        await create_job(db, tenant_id=tenant_id, job_type="ping", idempotency_key="race")
        await db.commit()

    async def claim(worker: str):
        async with maker() as session:
            job = await claim_next_job(session, worker_id=worker)
            won = None if job is None else job.id
            await session.commit()
            return won

    first, second = await asyncio.gather(claim("worker-a"), claim("worker-b"))
    winners = [item for item in (first, second) if item is not None]
    assert len(winners) == 1


async def test_expired_lease_is_recovered(db, tenant_a):
    job, _created = await create_job(
        db, tenant_id=tenant_a.id, job_type="ping", idempotency_key="lease"
    )
    moment = datetime.now(timezone.utc)
    claimed = await claim_next_job(db, worker_id="crashed", lease_seconds=30, now=moment)
    assert claimed is not None
    claimed.leased_until = moment - timedelta(minutes=5)
    await db.flush()
    recovered = await recover_expired_jobs(db, now=moment)
    assert recovered == 1
    again = await claim_next_job(db, worker_id="replacement")
    assert again is not None and again.id == job.id
    assert again.worker_id == "replacement"


async def test_permanent_error_is_dead_letter_and_replay_is_authorized(db, tenant_a):
    await create_job(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="poison")

    async def boom(_job):
        raise PermanentJobError("validation_error")

    job = await run_once(db, worker_id="w", handlers={"automation": boom})
    assert job is not None and job.status == "dead_letter"
    from app.automation.dlq import replay

    with pytest.raises(ResourceUnauthorized):
        await replay(db, tenant_id=tenant_a.id, job_id=job.id, role=UserRole.VIEWER)
    replayed = await replay(db, tenant_id=tenant_a.id, job_id=job.id, role=UserRole.OWNER)
    assert replayed.status == "queued"
    assert replayed.tenant_id == tenant_a.id
    for _ in range(2):
        replayed.status = "dead_letter"
        await replay_dead_letter(db, replayed)
    replayed.status = "dead_letter"
    with pytest.raises(ValueError):
        await replay_dead_letter(db, replayed)


async def test_retryable_failure_is_bounded(db, tenant_a):
    await create_job(
        db, tenant_id=tenant_a.id, job_type="ping", idempotency_key="retry", max_attempts=2
    )

    async def flaky(_job):
        raise RetryableJobError("timeout")

    first = await run_once(db, worker_id="w", handlers={"ping": flaky})
    assert first.status == "retry_scheduled"
    first.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    second = await run_once(db, worker_id="w2", handlers={"ping": flaky})
    assert second.status == "dead_letter"
    assert second.attempt_count == 2


async def test_cross_environment_job_is_not_executed(db, tenant_a):
    production = await _production(db, tenant_a)
    staging = await _staging(db, tenant_a)
    called = {"n": 0}

    async def handler(_job):
        called["n"] += 1

    await create_job(
        db,
        tenant_id=tenant_a.id,
        environment_id=production.id,
        job_type="ping",
        idempotency_key="scope",
        payload={"target_environment_id": str(staging.id)},
    )
    job = await run_once(db, worker_id="w", handlers={"ping": handler})
    assert job.status == "dead_letter"
    assert job.last_error_category == "environment_mismatch"
    assert called["n"] == 0
    assert job.environment_id == production.id


async def test_duplicate_key_across_connections_keeps_one_row(concurrent_sessionmaker):
    maker = concurrent_sessionmaker
    async with maker() as db:
        tenant = await make_tenant(db, "Idem")
        tenant_id = tenant.id

    async def insert():
        async with maker() as session:
            _job, created = await create_job(
                session, tenant_id=tenant_id, job_type="ping", idempotency_key="once"
            )
            await session.commit()
            return created

    results = await asyncio.gather(insert(), insert(), return_exceptions=True)
    assert not any(isinstance(item, Exception) for item in results)
    assert results.count(True) == 1
    async with maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(DurableJob).where(DurableJob.tenant_id == tenant_id)
        )
    assert count == 1
    assert uuid.UUID(str(tenant_id))
