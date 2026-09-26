"""Worker loop with leases, heartbeats and crash recovery.

A crash leaves the row ``running`` until ``leased_until``. Recovery requeues it.
Two workers cannot both finish a claim: the second compare-and-set updates
zero rows. External delivery is at-least-once; handlers must be idempotent.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import DurableJob
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.repository import (
    claim_next_job,
    mark_succeeded,
    move_to_dlq,
    recover_expired_jobs,
    renew_lease,
    schedule_retry,
)

Handler = Callable[[DurableJob], Awaitable[None]]


def worker_identity(prefix: str = "worker") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _mismatch(job: DurableJob) -> str | None:
    payload = job.payload or {}
    target = payload.get("target_environment_id") or payload.get("resource_environment_id")
    if job.environment_id and target and str(job.environment_id) != str(target):
        return "environment_mismatch"
    target_tenant = payload.get("target_tenant_id")
    if target_tenant and str(job.tenant_id) != str(target_tenant):
        return "tenant_mismatch"
    return None


def _delay(job: DurableJob) -> int:
    raw = min(3600, 30 * (2 ** max(0, job.attempt_count - 1)))
    return max(1, raw)


async def heartbeat(
    session: AsyncSession, job: DurableJob, *, worker_id: str, lease_seconds: int = 30
) -> bool:
    return await renew_lease(session, job.id, worker_id=worker_id, lease_seconds=lease_seconds)


async def run_once(
    session: AsyncSession,
    *,
    worker_id: str,
    handlers: dict[str, Handler],
    lease_seconds: int = 30,
    now: datetime | None = None,
) -> DurableJob | None:
    await recover_expired_jobs(session, now=now)
    job = await claim_next_job(session, worker_id=worker_id, lease_seconds=lease_seconds, now=now)
    if job is None:
        return None
    mismatch = _mismatch(job)
    if mismatch:
        await move_to_dlq(session, job, category=mismatch, now=now)
        log.info(
            "jobs.environment_rejected",
            job_id=str(job.id),
            tenant_id=str(job.tenant_id),
            category=mismatch,
        )
        return job
    handler = handlers.get(job.job_type)
    if handler is None:
        await move_to_dlq(session, job, category="unknown_job_type", now=now)
        return job
    try:
        await handler(job)
    except PermanentJobError as exc:
        await move_to_dlq(session, job, category=exc.category, now=now)
        return job
    except RetryableJobError as exc:
        if job.attempt_count >= job.max_attempts:
            await move_to_dlq(session, job, category=exc.category, now=now)
        else:
            await schedule_retry(
                session, job, category=exc.category, delay_seconds=_delay(job), now=now
            )
        return job
    except Exception:
        log.info("jobs.handler_failed", job_id=str(job.id), job_type=job.job_type)
        if job.attempt_count >= job.max_attempts:
            await move_to_dlq(session, job, category="unhandled", now=now)
        else:
            await schedule_retry(
                session, job, category="unhandled", delay_seconds=_delay(job), now=now
            )
        return job
    await mark_succeeded(session, job, now=now)
    return job


async def shutdown_release(session: AsyncSession, job: DurableJob, *, worker_id: str) -> None:
    """Expire this worker's lease so another worker can recover the job."""
    if job.worker_id != worker_id or job.status != "running":
        return
    job.leased_until = datetime.now(timezone.utc)
    await session.flush()
