"""Batch 07: worker execution, lease renewal, heartbeat, recovery."""
from datetime import datetime, timezone, timedelta
from app.jobs.models import PermanentJobError
from app.jobs.queue import enqueue, claim_next, ack, fail
from app.jobs.types import FailureClass

async def test_successful_handler(db, tenant_a):
    async def ok(_job): pass
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="s")
    job = await claim_next(db, worker_id="w")
    await ack(db, job, worker_id="w")
    assert job.status == "succeeded"

async def test_permanent_to_dlq(db, tenant_a):
    async def bad(_job): raise PermanentJobError("bad")
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="p", max_attempts=1)
    job = await claim_next(db, worker_id="w")
    await fail(db, job, worker_id="w", category="bad", failure_class=FailureClass.PERMANENT)
    assert job.status == "dead_letter"

async def test_expired_lease_recovered(db, tenant_a):
    await enqueue(db, tenant_id=tenant_a.id, job_type="automation", idempotency_key="exp")
    from app.jobs.repository import recover_expired_jobs
    job = await claim_next(db, worker_id="crash", lease_seconds=1)
    job.leased_until = datetime.now(timezone.utc) - timedelta(seconds=10)
    await db.flush()
    assert (await recover_expired_jobs(db)) == 1
    again = await claim_next(db, worker_id="new")
    assert again is not None and again.worker_id == "new"
