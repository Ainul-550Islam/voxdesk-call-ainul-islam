"""Worker loop with leases, heartbeats and crash recovery.

A crash leaves the row ``running`` until ``leased_until``. Recovery requeues it.
Two workers cannot both finish a claim: the second compare-and-set updates
zero rows. External delivery is at-least-once; handlers must be idempotent.

Batch 07 extends this module without replacing it:

* ``run_once`` keeps its exact call contract (session + handlers dict) but
  its terminal writes now go through the guarded ``queue.ack``/``queue.fail``
  paths, so even the legacy entrypoint can no longer let a deposed worker
  overwrite a newer holder's result, and its backoff gained the bounded
  deterministic jitter from ``app.jobs.retry``.
* ``JobWorker`` is the production loop: registry-scoped claiming (only
  server-registered job types are ever executed — a payload can name a
  type, never an import path), database-counted concurrency admission, a
  real heartbeat task that renews the lease on its own session while the
  handler runs, durable cancellation checks before execution and at the
  guarded ack, and lease reaping on every cycle.
* Handler outcome normalization: ``None`` = success, ``ExecutionResult`` =
  structured outcome, ``PermanentJobError``/``RetryableJobError`` = the
  existing explicit classes, anything else = classified by
  ``app.jobs.retry`` (unexpected exceptions are transient ``unhandled`` —
  bounded by ``max_attempts``, ending in the DLQ, never in a loop).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import log
from app.db.models import DurableJob
from app.jobs import queue
from app.jobs.concurrency import DEFAULT_LIMITS, ConcurrencyLimits
from app.jobs.heartbeat import (
    DEFAULT_LEASE_POLICY,
    LeasePolicy,
    recover_abandoned,
    spawn_heartbeat,
)
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.repository import claim_next_job, recover_expired_jobs, renew_lease
from app.jobs.retry import backoff_seconds, classify_exception
from app.jobs.types import ExecutionResult, FailureClass, registered_job_types

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
    """Backoff for the legacy path: the retry module's bounded-jitter delay.

    Same base (30s), same doubling, same 3600s cap this worker always had;
    the deterministic per-job jitter spread comes from ``app.jobs.retry``.
    """
    return backoff_seconds(job.attempt_count, key=str(job.id))


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
    """One claim-execute-resolve cycle on the caller's session (legacy entry).

    Preserved for the existing callers and tests; the terminal writes are
    the guarded ones, so a lease lost mid-handler turns the ack/fail into a
    no-op (``jobs.stale_*_rejected``) instead of corrupting the row the new
    holder now owns.
    """
    await recover_expired_jobs(session, now=now)
    job = await claim_next_job(session, worker_id=worker_id, lease_seconds=lease_seconds, now=now)
    if job is None:
        return None
    mismatch = _mismatch(job)
    if mismatch:
        await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=mismatch,
            failure_class=FailureClass.PERMANENT,
            now=now,
        )
        log.info(
            "jobs.environment_rejected",
            job_id=str(job.id),
            tenant_id=str(job.tenant_id),
            category=mismatch,
        )
        return job
    handler = handlers.get(job.job_type)
    if handler is None:
        await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category="unknown_job_type",
            failure_class=FailureClass.PERMANENT,
            now=now,
        )
        return job
    from app.jobs.runtime import run_job_handler
    try:
        await run_job_handler(job, handler)
    except PermanentJobError as exc:
        await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=exc.category,
            failure_class=FailureClass.PERMANENT,
            message=str(exc),
            now=now,
        )
        return job
    except RetryableJobError as exc:
        await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=exc.category,
            failure_class=FailureClass.TRANSIENT,
            message=str(exc),
            now=now,
        )
        return job
    except Exception as exc:
        log.info("jobs.handler_failed", job_id=str(job.id), job_type=job.job_type)
        failure_class, category = classify_exception(exc)
        await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=category,
            failure_class=failure_class,
            message=str(exc),
            now=now,
        )
        return job
    await queue.ack(session, job, worker_id=worker_id, now=now)
    return job


async def shutdown_release(session: AsyncSession, job: DurableJob, *, worker_id: str) -> None:
    """Expire this worker's lease so another worker can recover the job."""
    if job.worker_id != worker_id or job.status != "running":
        return
    job.leased_until = datetime.now(timezone.utc)
    await session.flush()


# ---------------------------------------------------------------------------
# Batch 07: the registry-scoped production worker
# ---------------------------------------------------------------------------


def build_handlers(extra: dict[str, Handler] | None = None) -> dict[str, Handler]:
    """The executable set: every server-registered type, plus explicit extras.

    The registry (``app.jobs.types``) is the only source; there is no path
    from a payload string to ``import``. Types with no registered handler
    are absent here, and the registry-scoped claim never picks them up —
    legacy receipt rows of unadopted types stay untouched rather than being
    dragged into execution and dead-lettered.
    """
    # One bootstrap owns imports for the existing persisted-type registry;
    # no second handler map, queue, or worker loop is introduced.
    from app.jobs.registry import bootstrap

    handlers = dict(bootstrap())
    handlers.update(extra or {})
    return handlers


async def _resolve_result(
    session: AsyncSession,
    job: DurableJob,
    *,
    worker_id: str,
    result: object | None,
) -> str:
    """Normalize a handler return into the guarded terminal write."""
    if isinstance(result, ExecutionResult):
        if result.success:
            await queue.ack(session, job, worker_id=worker_id)
            return "succeeded"
        outcome = await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=result.category or "handler_failed",
            failure_class=result.failure_class or FailureClass.TRANSIENT,
            message=result.message,
        )
        return outcome
    await queue.ack(session, job, worker_id=worker_id)
    return "succeeded"


async def execute_claimed(
    session: AsyncSession,
    job: DurableJob,
    *,
    worker_id: str,
    handlers: dict[str, Handler],
) -> str:
    """Run one claimed job to a guarded terminal state. Returns the outcome.

    Order of decisions: persisted scope first (a row whose payload targets a
    different environment/tenant than the row itself is dead-lettered
    without execution), then durable cancellation, then the registry
    lookup, then the handler. ``'stale'`` means the lease was lost and no
    write landed — the row belongs to its newer holder.
    """
    mismatch = _mismatch(job)
    if mismatch:
        log.info(
            "jobs.environment_rejected",
            job_id=str(job.id),
            tenant_id=str(job.tenant_id),
            category=mismatch,
        )
        return await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=mismatch,
            failure_class=FailureClass.PERMANENT,
        )
    if job.cancel_requested:
        finalized = await queue.finalize_cancel(session, job, worker_id)
        return "cancelled" if finalized else "stale"
    handler = handlers.get(job.job_type)
    if handler is None:
        return await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category="unknown_job_type",
            failure_class=FailureClass.PERMANENT,
        )
    from app.jobs.runtime import run_job_handler
    try:
        result = await run_job_handler(job, handler)
    except PermanentJobError as exc:
        return await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=exc.category,
            failure_class=FailureClass.PERMANENT,
            message=str(exc),
        )
    except RetryableJobError as exc:
        return await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=exc.category,
            failure_class=FailureClass.TRANSIENT,
            message=str(exc),
        )
    except Exception as exc:
        failure_class, category = classify_exception(exc)
        log.info(
            "jobs.handler_failed",
            job_id=str(job.id),
            job_type=job.job_type,
            category=category,
        )
        return await queue.fail(
            session,
            job,
            worker_id=worker_id,
            category=category,
            failure_class=failure_class,
            message=str(exc),
        )
    return await _resolve_result(session, job, worker_id=worker_id, result=result)


class JobWorker:
    """Durable job worker for the scheduler process (and for tests).

    One cycle: reap abandoned leases → claim (registry types only, under
    database-counted concurrency limits) → commit the claim so the lease is
    visible to every other worker → heartbeat on its own sessions while the
    handler runs → guarded terminal write → commit. The claim is committed
    *before* execution, so a crash mid-handler is a lease expiry, never a
    lost or invisible attempt.

    The worker never trusts itself over the row: identity, tenant and
    environment come from the persisted job, and every terminal write is
    conditional on this worker still holding the lease.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str | None = None,
        handlers: dict[str, Handler] | None = None,
        limits: ConcurrencyLimits = DEFAULT_LIMITS,
        policy: LeasePolicy = DEFAULT_LEASE_POLICY,
        job_types: tuple[str, ...] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id or worker_identity()
        self.extra_handlers = dict(handlers or {})
        self.limits = limits
        self.policy = policy
        #: ``None`` restricts the claim to whatever the registry holds at
        #: run time; an explicit tuple pins it (tests, dedicated workers).
        self.job_types = job_types

    async def run_once(self, *, now: datetime | None = None) -> DurableJob | None:
        handlers = build_handlers(self.extra_handlers)
        types = self.job_types
        if types is None:
            types = tuple(registered_job_types()) or None
        async with self.session_factory() as session:
            await recover_abandoned(session, now=now)
            await session.commit()
            job = await queue.claim_next(
                session,
                worker_id=self.worker_id,
                lease_seconds=self.policy.lease_seconds,
                now=now,
                job_types=types,
                limits=self.limits,
            )
            if job is None:
                await session.commit()
                return None
            await session.commit()  # the lease is durable before the handler runs
            stop = asyncio.Event()
            beat_task = await spawn_heartbeat(
                self.session_factory,
                job_id=job.id,
                worker_id=self.worker_id,
                policy=self.policy,
                stop=stop,
            )
            try:
                outcome = await execute_claimed(
                    session, job, worker_id=self.worker_id, handlers=handlers
                )
            finally:
                stop.set()
                await beat_task
            await session.commit()
            log.info(
                "jobs.cycle",
                job_id=str(job.id),
                job_type=job.job_type,
                outcome=outcome,
                worker_id=self.worker_id,
            )
            return job

    async def run_forever(self, *, stop: asyncio.Event, poll_seconds: float = 2.0) -> None:
        """Drain until ``stop`` is set; idle cycles wait ``poll_seconds``.

        Safe for several processes at once: every step is a conditional
        database operation, and a cycle that finds nothing costs one claim
        query.
        """
        while not stop.is_set():
            job = await self.run_once()
            if job is not None:
                continue  # drain: there may be more work right now
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except (asyncio.TimeoutError, TimeoutError):
                continue


__all__ = [
    "Handler",
    "JobWorker",
    "build_handlers",
    "execute_claimed",
    "heartbeat",
    "run_once",
    "shutdown_release",
    "worker_identity",
]
