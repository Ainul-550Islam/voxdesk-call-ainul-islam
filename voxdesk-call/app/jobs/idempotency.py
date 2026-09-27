"""Database-backed idempotency for asynchronous work.

A duplicate key returns the existing record. Redis TTL is not the lock.

Batch 07 extends this module — it does not replace it, and it does not add a
second ledger. Three layers of duplicate protection now share this store:

1. **Job admission** — ``jobs.idempotency_key`` (unique per tenant) makes
   ``create_job``/``enqueue`` return the existing row instead of inserting a
   second one for the same logical operation.
2. **Handler side effects** — ``begin``/``complete`` on ``job_idempotency``:
   a handler wraps its business effect in ``run_idempotent`` so at-least-once
   delivery (a reclaim after lease expiry, a replay after DLQ) cannot double
   the effect.
3. **Business-fact keys** — the builders below give every recurring logical
   operation a *deterministic* identity. A random UUID is only acceptable
   when the business fact itself has no identity; a telephony callback, a CRM
   sync, a notification, a QA review, a lead event and an outbox delivery all
   do, so their keys are derived, reproducible, and stable across retries,
   restarts and replays.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DurableJob, JobIdempotency


async def begin(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str
) -> tuple[JobIdempotency, bool]:
    """Insert an in-progress key. The second return value is True when new."""
    row = JobIdempotency(tenant_id=tenant_id, idempotency_key=key, status="in_progress")
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = await get(session, tenant_id=tenant_id, key=key)
        if found is None:
            raise
        return found, False
    return row, True


async def get(session: AsyncSession, *, tenant_id: uuid.UUID, key: str) -> JobIdempotency | None:
    return (
        await session.execute(
            select(JobIdempotency).where(
                JobIdempotency.tenant_id == tenant_id,
                JobIdempotency.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


async def complete(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str, result_ref: str
) -> JobIdempotency:
    row = await get(session, tenant_id=tenant_id, key=key)
    if row is None:
        raise KeyError("idempotency key not found")
    if row.status == "completed":
        return row
    row.status = "completed"
    row.result_ref = result_ref[:128]
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Batch 07: deterministic business-operation keys
# ---------------------------------------------------------------------------


def telephony_callback_key(provider: str, event_id: str) -> str:
    """One provider callback event = one identity, however often it is redelivered."""
    return f"telephony:{provider}:{event_id}"[:128]


def crm_sync_key(entity: str, entity_id: str, *, revision: str = "") -> str:
    """A CRM sync of one entity (optionally one revision of it)."""
    suffix = f":{revision}" if revision else ""
    return f"crm-sync:{entity}:{entity_id}{suffix}"[:128]


def notification_key(notification_id: str, *, channel: str = "") -> str:
    suffix = f":{channel}" if channel else ""
    return f"notification:{notification_id}{suffix}"[:128]


def qa_auto_review_key(call_id: str) -> str:
    return f"qa-auto-review:{call_id}"[:128]


def lead_event_key(lead_id: str, event: str, *, sequence: str = "") -> str:
    suffix = f":{sequence}" if sequence else ""
    return f"lead:{lead_id}:{event}{suffix}"[:128]


def outbox_delivery_key(event_id: str, *, attempt: int = 0) -> str:
    """Delivery round identity for one outbox event (attempt 0 = first)."""
    return f"outbox-delivery:{event_id}:{attempt}"[:128]


def business_key(job: DurableJob) -> str:
    """The job's logical-operation identity (stable across attempts/replays)."""
    return f"{job.job_type}:{job.idempotency_key}"[:128]


def attempt_key(job: DurableJob) -> str:
    """Per-attempt identity: distinct for each execution of the same job.

    Handlers whose side effect must happen once *per attempt* (rare) use
    this; handlers whose side effect must happen once *per business fact*
    use ``business_key`` — that is the one replay-safe deduplication.
    """
    return f"{job.job_type}:{job.idempotency_key}:attempt:{job.attempt_count}"[:128]


async def run_idempotent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key: str,
    work: Callable[[], Awaitable[str]],
) -> tuple[bool, str]:
    """Run ``work`` at most once per completed ledger entry for ``key``.

    Returns ``(executed, result_ref)``. Semantics, stated honestly for an
    at-least-once platform:

    * key completed → work is skipped, the recorded ``result_ref`` returned;
    * key new → work runs, then the ledger row completes with its result;
    * key in-progress → a previous executor crashed before completing (or is
      concurrently inside the work). The work runs again — crash recovery
      requires it — so ``work`` itself must be idempotent or transactional.
      This helper bounds *uncontrolled* duplication (replays, redeliveries,
      restarts); it does not claim exactly-once, and nothing in this batch
      does.

    The caller owns the transaction: ``work`` and the ledger completion
    commit together with the business change, or not at all.
    """
    row, created = await begin(session, tenant_id=tenant_id, key=key)
    if not created and row.status == "completed":
        return False, row.result_ref
    result_ref = await work()
    await complete(session, tenant_id=tenant_id, key=key, result_ref=result_ref)
    return True, result_ref
