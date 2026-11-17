"""Durable workflow executor on `app.jobs` (PART 14, P14-04).

The executor is deliberately thin: it enqueues one durable job per (call, step,
install) with an idempotency key the queue already honours, and the worker calls
back into `app.workflows.post_call.run_step`. That means a workflow step inherits
everything the job system already has -- leases, retries, dead-letter, replay --
instead of growing a second, weaker retry mechanism.

Failure policy, stated once so a reviewer can disagree with it:

* a step that raises is recorded `failed` and retried up to the job's budget;
* a step marked `idempotent=False` in the preset catalogue is **not** retried
  blindly: it is re-queued only after the same call's reconcile step has run;
* a step whose job exhausts its attempts lands in the dead-letter queue, where
  an operator can see it; it never disappears silently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.channel_models import WorkflowStepRun
from app.jobs.queue import enqueue
from app.workflows.post_call import MAX_ATTEMPTS, Step, run_step

#: The job type the worker claims. Registering a handler for it is the job
#: system's job (see `app/jobs/registry.py`), not this module's.
JOB_TYPE = "workflow.execution"


@dataclass(frozen=True)
class EnqueueResult:
    step: str
    job_id: str | None
    created: bool
    reason: str | None = None


async def enqueue_step(
    session: AsyncSession, *, tenant_id: uuid.UUID, call_id: uuid.UUID, step: Step
) -> EnqueueResult:
    key = f"workflow:{step.idempotency_key(call_id)}"
    job, created = await enqueue(
        session,
        tenant_id=tenant_id,
        job_type=JOB_TYPE,
        idempotency_key=key,
        payload={"call_id": str(call_id), "step": step.name, "install_id": step.install_id},
        max_attempts=MAX_ATTEMPTS,
    )
    return EnqueueResult(step=step.name, job_id=str(getattr(job, "id", "") or ""), created=created)


async def enqueue_post_call(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    steps: Iterable[Step],
) -> list[EnqueueResult]:
    results: list[EnqueueResult] = []
    for step in steps:
        results.append(
            await enqueue_step(session, tenant_id=tenant_id, call_id=call_id, step=step)
        )
    return results


async def execute(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    step_name: str,
    handlers: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one step from a claimed job. This is what the worker calls."""
    handler = handlers.get(step_name)
    step = Step(name=step_name, payload=dict(payload or {}))
    if handler is None:
        return {"step": step_name, "status": "skipped", "reason": "no handler registered"}
    return await run_step(
        session,
        tenant_id=tenant_id,
        call_id=call_id,
        step=step,
        handler=handler,
        context=context,
    )


async def step_history(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every attempt for a call, newest first -- the audit the executor promises."""
    rows = (
        await session.execute(
            select(WorkflowStepRun)
            .where(WorkflowStepRun.tenant_id == tenant_id, WorkflowStepRun.call_id == call_id)
            .order_by(WorkflowStepRun.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "step": row.step,
            "status": row.status,
            "attempts": row.attempts,
            "duration_ms": row.duration_ms,
            "last_error": row.last_error,
            "idempotency_key": row.idempotency_key,
        }
        for row in rows
    ]


async def outstanding(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """How many steps are still pending or running. The library page shows this."""
    rows = (
        await session.execute(
            select(WorkflowStepRun).where(
                WorkflowStepRun.tenant_id == tenant_id,
                WorkflowStepRun.status.in_(("pending", "running")),
            )
        )
    ).scalars().all()
    return len(rows)
