"""Lease renewal and abandoned-worker recovery (Batch 07).

Rules this module enforces:

* **A worker renews only its own lease.** ``renew_lease`` is a conditional
  update keyed on ``worker_id`` + ``status='running'``; after a reap and a
  re-claim the row carries the new worker's id, so the old worker's renewal
  matches zero rows and it learns it lost the job.
* **A second worker never steals an actively heartbeating job.** Claiming
  requires ``leased_until`` to be NULL or in the past; every beat pushes it
  forward, so a live worker's job is invisible to other claimants.
* **An expired lease becomes reclaimable.** ``recover_abandoned`` requeues
  running rows whose lease lapsed — a crashed worker cannot strand a job
  forever — and closes the dangling attempt rows as ``expired`` so the
  provenance stays honest.
* **Durable cancellation beats recovery.** A crashed job that had
  ``cancel_requested`` set is finalised as ``cancelled``, not requeued, and
  claimable rows with a pending cancel are finalised too.

Heartbeat cadence: renew at ``lease / 3`` so two missed beats still leave
the lease valid, and a worker that lost its lease stops beating instead of
fighting the new holder.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import log
from app.db.models import DurableJob, JobAttempt
from app.jobs.models import AttemptState
from app.jobs.repository import renew_lease


@dataclass(frozen=True)
class LeasePolicy:
    """Lease duration and heartbeat cadence for one worker."""

    lease_seconds: int = 30
    #: Renew at a third of the lease: two missed beats still expire it, one
    #: missed beat does not.
    heartbeat_seconds: int = 10


DEFAULT_LEASE_POLICY = LeasePolicy()


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


def lease_active(job: DurableJob, *, now: datetime | None = None) -> bool:
    """Is this row's lease still held (running and not expired)?"""
    if job.status != "running" or job.leased_until is None:
        return False
    return job.leased_until > _now(now)


async def beat(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_POLICY.lease_seconds,
    now: datetime | None = None,
) -> bool:
    """Extend this worker's lease. ``False`` means ownership was lost.

    Delegates to ``repository.renew_lease`` — one renewal implementation —
    whose guard (``worker_id`` match + ``status='running'``) is what makes a
    renewal by a deposed worker a no-op instead of a theft.
    """
    return await renew_lease(
        session, job_id, worker_id=worker_id, lease_seconds=lease_seconds, now=now
    )


async def recover_abandoned(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Reap expired leases and finalise durable cancellations.

    Safe to run from every scheduler instance on every tick: every statement
    is a conditional update, so concurrent reapers split the work by row and
    a repeated tick finds nothing to do.
    """
    moment = _now(now)
    expired_ids = list(
        (
            await session.execute(
                select(DurableJob.id).where(
                    DurableJob.status == "running",
                    DurableJob.leased_until.is_not(None),
                    DurableJob.leased_until < moment,
                )
            )
        ).scalars()
    )

    cancelled = 0
    if expired_ids:
        # Close the dangling attempt rows first: provenance says "this attempt
        # expired", not "this attempt is somehow still running".
        await session.execute(
            update(JobAttempt)
            .where(
                JobAttempt.job_id.in_(expired_ids),
                JobAttempt.status == AttemptState.STARTED.value,
            )
            .values(status=AttemptState.EXPIRED.value, finished_at=moment), execution_options={"synchronize_session": False})
        cancelled_result = await session.execute(
            update(DurableJob)
            .where(
                DurableJob.id.in_(expired_ids),
                DurableJob.status == "running",
                DurableJob.cancel_requested.is_(True),
            )
            .values(
                status="cancelled",
                completed_at=moment,
                worker_id="",
                leased_until=None,
            ), execution_options={"synchronize_session": False})
        cancelled = int(cancelled_result.rowcount or 0)
        requeued_result = await session.execute(
            update(DurableJob)
            .where(
                DurableJob.id.in_(expired_ids),
                DurableJob.status == "running",
            )
            .values(
                status="queued",
                worker_id="",
                leased_until=None,
                available_at=moment,
            ), execution_options={"synchronize_session": False})
        requeued = int(requeued_result.rowcount or 0)
    else:
        requeued = 0

    # A cancel that landed while the job sat claimable (requeued by an
    # earlier crash, or requested between claim attempts) is finalised here
    # so the claim filter's exclusion never strands it.
    finalized_result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.status.in_(("queued", "retry_scheduled")),
            DurableJob.cancel_requested.is_(True),
        )
        .values(status="cancelled", completed_at=moment, worker_id="", leased_until=None), execution_options={"synchronize_session": False})
    cancel_finalized = int(finalized_result.rowcount or 0)
    cancelled += cancel_finalized

    await session.flush()
    if requeued or cancelled:
        log.info("jobs.recovered", requeued=requeued, cancelled=cancelled)
    return {"requeued": requeued, "cancelled": cancelled}


async def spawn_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    worker_id: str,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
    stop: asyncio.Event,
) -> asyncio.Task[None]:
    """Background task that keeps this worker's lease alive during a handler.

    Ephemeral infrastructure, deliberately: it carries no business state, so
    if the process dies the task dies with it and the *lease* — not the task
    — is what recovery keys on. Each beat uses its own short-lived session so
    it never interleaves with the worker's claim/ack session. When a beat
    reports lost ownership the loop stops; the worker's guarded ack will
    reject the stale result afterwards.
    """

    async def _loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=policy.heartbeat_seconds)
                return  # stop was set during the wait
            except (asyncio.TimeoutError, TimeoutError):
                pass
            try:
                async with session_factory() as beat_session:
                    renewed = await beat(
                        beat_session,
                        job_id,
                        worker_id=worker_id,
                        lease_seconds=policy.lease_seconds,
                    )
                    await beat_session.commit()
                if not renewed:
                    log.info(
                        "jobs.heartbeat_lost",
                        job_id=str(job_id),
                        worker_id=worker_id,
                    )
                    return
            except Exception as exc:  # keep beating through a transient DB blip
                log.info(
                    "jobs.heartbeat_failed",
                    job_id=str(job_id),
                    error=str(exc)[:200],
                )

    return asyncio.get_running_loop().create_task(_loop())


def lease_window(*, now: datetime | None = None, policy: LeasePolicy = DEFAULT_LEASE_POLICY):
    """(moment, leased_until) pair for one claim — exported for tests."""
    moment = _now(now)
    return moment, moment + timedelta(seconds=policy.lease_seconds)
