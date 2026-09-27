"""Operator-safe replay of dead-letter jobs (Batch 07).

Replay answers one question: an operator has looked at a DLQ row, fixed the
cause, and wants the *same* job to run again. The contract:

* **Authorized.** Same rule the existing automation DLQ replay uses
  (``Permission.CAMPAIGN_RUN`` via ``operator_may_replay``) — one permission
  vocabulary, not a second one.
* **Tenant-scoped.** The row is matched on ``id`` *and* ``tenant_id`` inside
  the guarded update; another tenant's job does not exist (404, no leak).
* **Environment-scoped.** Replay accepts no new scope of any kind: the row
  keeps its persisted ``tenant_id``/``environment_id``, so a replayed job
  executes exactly where the original did.
* **DLQ-state verified.** Only ``dead_letter``/``quarantined`` rows replay,
  and a row with durable cancellation intent stays cancelled.
* **Race-safe and budgeted.** One conditional UPDATE performs the whole
  transition, so two operators clicking replay at once produce exactly one
  new attempt (the loser gets a 409 with the row's real state). The existing
  3-replay budget (``repository._MAX_REPLAYS``) is enforced *in the WHERE
  clause*, not read-modify-write.
* **Provenance preserved.** The same row keeps its history: ``replay_count``
  increments, ``JobAttempt`` rows from failed attempts stay, and the job
  goes back to ``queued`` with a fresh attempt budget — it is never mutated
  into a false success. What "a new safe execution attempt" means here:
  ``max_attempts`` becomes ``attempt_count + extra_attempts`` and the next
  claim records a new attempt row like any other.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.core.logging import log
from app.db.models import DurableJob, UserRole
from app.jobs.repository import get_job
from app.resources.exceptions import ResourceUnauthorized
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied

#: Same budget the existing repository replay enforces. Kept as a named
#: constant here because the guard lives in SQL (``replay_count < MAX``),
#: where the repository's Python-side check cannot run.
MAX_REPLAYS = 3

#: Rows in these states may be replayed. ``quarantined`` joins ``dead_letter``
#: so an operator can release-and-replay in either order.
REPLAYABLE = ("dead_letter", "quarantined")


def operator_may_replay(role: UserRole | None) -> bool:
    if role is None:
        return False
    return has_permission(role, Permission.CAMPAIGN_RUN)


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


async def replay_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    role: UserRole | None,
    extra_attempts: int = 5,
    now: datetime | None = None,
) -> DurableJob:
    """Requeue one DLQ job for a fresh, budgeted execution attempt.

    Raises ``ResourceUnauthorized`` (403) without permission,
    ``BoundaryDenied`` (404) for missing/cross-tenant rows, and
    ``LifecycleDenied`` (409) when the row's state or replay budget refuses.
    Flushes; the caller (route/service) owns the commit.
    """
    if not operator_may_replay(role):
        raise ResourceUnauthorized()
    moment = _now(now)

    result = await session.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.tenant_id == tenant_id,
            DurableJob.status.in_(REPLAYABLE),
            DurableJob.replay_count < MAX_REPLAYS,
            DurableJob.cancel_requested.is_(False),
        )
        .values(
            status="queued",
            replay_count=DurableJob.replay_count + 1,
            max_attempts=DurableJob.attempt_count + max(1, int(extra_attempts)),
            available_at=moment,
            completed_at=None,
            worker_id="",
            leased_until=None,
        ), execution_options={"synchronize_session": False})
    if result.rowcount == 1:
        await session.flush()
        job = await get_job(session, job_id, tenant_id=tenant_id)
        if job is not None:
            await session.refresh(job)
        log.info(
            "jobs.replayed",
            job_id=str(job_id),
            tenant_id=str(tenant_id),
            replay_count=getattr(job, "replay_count", None),
        )
        return job

    # The guarded update refused. Diagnose from the row's real state so the
    # operator gets an accurate 404/409 instead of a mystery.
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None:
        raise BoundaryDenied()
    if job.status not in REPLAYABLE:
        raise LifecycleDenied("only a dead-letter or quarantined job can be replayed")
    if job.cancel_requested:
        raise LifecycleDenied("job is cancelled")
    raise LifecycleDenied("replay budget exhausted")
