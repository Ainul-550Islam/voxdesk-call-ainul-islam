"""Durable scheduled-job admission and due-job discovery (Batch 07).

The schedule truth is the ``jobs`` row: a scheduled job is an enqueued job
whose ``available_at`` lies in the future. There is no in-memory timer, no
cron dict, no process-local next-run table — a scheduler restart (or a
second scheduler instance) loses nothing and invents nothing, because
everything this module does is either a durable insert with an idempotency
key or a conditional update.

Consequences, stated plainly:

* **Repeated ticks are idempotent.** ``schedule_job`` dedupes on
  ``(tenant_id, idempotency_key)``; ``tick``'s recovery statements match
  only rows still in the abandoned state, so the second tick is a no-op.
* **Multiple scheduler instances never duplicate execution.** Discovery is
  read-only; *execution* goes through the claim (``FOR UPDATE SKIP LOCKED``
  on PostgreSQL, compare-and-set elsewhere), which has exactly one winner
  per row. Two instances ticking concurrently split the reaper work by row
  and cannot double-run a job.
* **Due discovery** (``due_jobs``) is the same filtered, fairly-ordered
  view the claim uses — what an operator sees and what a worker can take
  never disagree.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DurableJob
from app.jobs.heartbeat import recover_abandoned
from app.jobs.queue import enqueue
from app.jobs.repository import _CLAIMABLE
from app.jobs.types import JobPriority


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


async def schedule_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_type: str,
    idempotency_key: str,
    run_at: datetime,
    payload: dict | None = None,
    priority: JobPriority | int = JobPriority.NORMAL,
    environment_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    max_attempts: int = 5,
) -> tuple[DurableJob, bool]:
    """Admit a job that becomes claimable at ``run_at``.

    Durable by construction: the row *is* the schedule. A ``run_at`` in the
    past is simply due immediately. The idempotency key makes re-admission
    (a retried API call, a second scheduler boot) return the existing row.
    Flushes; the caller commits.
    """
    return await enqueue(
        session,
        tenant_id=tenant_id,
        job_type=job_type,
        idempotency_key=idempotency_key,
        payload=payload,
        priority=priority,
        environment_id=environment_id,
        organization_id=organization_id,
        max_attempts=max_attempts,
        available_at=_now(run_at),
    )


async def due_jobs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
    job_types: tuple[str, ...] | None = None,
    tenant_id: uuid.UUID | None = None,
) -> list[DurableJob]:
    """Read-only discovery of claimable rows whose time has come.

    Mirrors the claim filter (claimable states, due, unleased-or-expired,
    no pending cancellation) so discovery and execution agree; ordering is
    the fair claim order (aged-first, then priority, then FIFO).
    """
    from app.jobs.repository import _claim_filters, _claim_order

    moment = _now(now)
    stmt = (
        select(DurableJob)
        .where(*_claim_filters(moment, tenant_id, job_types=job_types))
        .order_by(*_claim_order(moment, None))
        .limit(max(1, min(int(limit), 500)))
    )
    return list((await session.execute(stmt)).scalars())


async def count_due(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    tenant_id: uuid.UUID | None = None,
) -> int:
    """Queue depth: how many rows are due this moment.

    ``tenant_id=None`` counts the whole table (scheduler metrics); the
    operator API always passes the authenticated tenant so one tenant's
    dashboard never learns another's backlog.
    """
    from app.jobs.repository import _claim_filters

    moment = _now(now)
    stmt = (
        select(func.count())
        .select_from(DurableJob)
        .where(*_claim_filters(moment, tenant_id))
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def list_scheduled(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
    limit: int = 50,
) -> list[DurableJob]:
    """This tenant's not-yet-due scheduled work, soonest first."""
    moment = _now(now)
    stmt = (
        select(DurableJob)
        .where(
            DurableJob.tenant_id == tenant_id,
            DurableJob.status.in_(_CLAIMABLE),
            DurableJob.available_at > moment,
        )
        .order_by(DurableJob.available_at)
        .limit(max(1, min(int(limit), 200)))
    )
    return list((await session.execute(stmt)).scalars())


async def tick(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """One scheduler pass: reap abandoned leases, finalise cancels, report depth.

    Every statement is conditional, so concurrent/repeated ticks are safe
    and split work by row. Execution is *not* part of the tick — the worker
    claims separately, which is what keeps multi-instance scheduling from
    ever duplicating a run. Flushes; the caller commits.
    """
    recovered = await recover_abandoned(session, now=now)
    due = await count_due(session, now=now)
    return {"requeued": recovered["requeued"], "cancelled": recovered["cancelled"], "due": due}
