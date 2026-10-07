"""Real PostgreSQL claim locking and durable worker-crash recovery.

Requires DATABASE_URL pointing at a disposable PostgreSQL database migrated to
head. Separate spawned processes exercise the real SKIP LOCKED claim path.
No SQLite substitution or unconditional skip is used.
"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import DurableJob, JobAttempt, Organization, Tenant
from app.jobs.heartbeat import recover_abandoned
from app.jobs.repository import claim_next_job


def _claim_worker(url, tenant_id, ready, release, output):
    async def run():
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                job = await claim_next_job(
                    session, worker_id=str(os.getpid()), tenant_id=uuid.UUID(tenant_id)
                )
                output.put(str(job.id) if job else None)
                ready.set()
                if not await asyncio.to_thread(release.wait, 30):
                    raise TimeoutError("claim test coordinator did not release worker")
                await session.commit()
        finally:
            await engine.dispose()
    asyncio.run(run())


async def test_postgres_worker_crash_lease_reclaim_and_duplicate_prevention():
    url = os.environ.get("DATABASE_URL", "")
    assert url.startswith("postgresql+asyncpg://"), (
        "DATABASE_URL must target a disposable PostgreSQL database migrated to head"
    )
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()
    organization_id = None
    ctx = multiprocessing.get_context("spawn")
    processes = []
    releases = []
    try:
        async with maker() as session:
            tenant = Tenant(id=tenant_id, name="Recovery test", twilio_number=f"+{tenant_id.int % 10**14:014d}")
            session.add(tenant)
            await session.flush()
            organization_id = tenant.organization_id
            session.add(DurableJob(
                id=job_id, tenant_id=tenant_id, job_type="automation",
                idempotency_key=uuid.uuid4().hex, payload={},
                available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                created_at=datetime.now(timezone.utc),
            ))
            await session.commit()

        # Keep worker one's transaction open. Worker two must skip its locked
        # row instead of blocking or claiming the same job.
        for _ in range(2):
            ready, release, output = ctx.Event(), ctx.Event(), ctx.Queue()
            process = ctx.Process(target=_claim_worker, args=(url, str(tenant_id), ready, release, output))
            processes.append(process)
            releases.append(release)
            process.start()
            assert await asyncio.to_thread(ready.wait, 30), "worker failed to claim within timeout"
            claimed = await asyncio.to_thread(output.get, True, 5)
            if len(processes) == 1:
                assert claimed == str(job_id)
            else:
                assert claimed is None

        for release in releases:
            release.set()
        for process in processes:
            await asyncio.to_thread(process.join, 30)
            assert process.exitcode == 0

        # The process has exited but its committed lease survives. It is not
        # reclaimable until expiry; recovery then closes the first attempt.
        async with maker() as session:
            assert await claim_next_job(session, worker_id="too-early", tenant_id=tenant_id) is None
            job = await session.get(DurableJob, job_id)
            moment = job.leased_until + timedelta(seconds=1)
            result = await recover_abandoned(session, now=moment)
            assert result["requeued"] >= 1
            await session.commit()
        async with maker() as session:
            recovered = await claim_next_job(session, worker_id="replacement", tenant_id=tenant_id, now=moment)
            assert recovered.id == job_id
            assert recovered.attempt_count == 2
            await session.commit()
        async with maker() as session:
            attempts = (await session.execute(select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_number))).scalars().all()
            assert [attempt.status for attempt in attempts] == ["expired", "started"]
            assert await claim_next_job(session, worker_id="duplicate", tenant_id=tenant_id, now=moment) is None
    finally:
        for release in releases:
            release.set()
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        async with maker() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            if organization_id:
                await session.execute(delete(Organization).where(Organization.id == organization_id))
            await session.commit()
        await engine.dispose()
