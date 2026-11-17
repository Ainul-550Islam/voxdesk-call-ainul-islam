"""Durable post-call steps (PART 14, P14-04).

A post-call workflow runs after the caller has hung up, which means it runs when
nobody is watching. Three things follow:

* every step is recorded before it runs, so a worker that dies leaves evidence
  rather than silence;
* every step carries an idempotency key derived from (call, step, install), so a
  retry cannot double-post to Slack;
* a failing step does not stop the others -- the invoice note failing is not a
  reason to skip the CRM sync.

The executor in `app/workflows/executor.py` is what puts these on the durable
queue; this module owns the plan and the single-step semantics.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.channel_models import WorkflowStepRun

MAX_ATTEMPTS = 3


class PostCallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Step:
    name: str
    install_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    idempotent: bool = True

    def idempotency_key(self, call_id: uuid.UUID | str) -> str:
        raw = f"{call_id}:{self.name}:{self.install_id or ''}:{sorted(self.payload.items())!r}"
        return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def plan_steps(installs: Iterable[Any], mapping: Mapping[str, Iterable[str]]) -> list[Step]:
    """Turn enabled installs into steps, using the workflow_key -> steps map."""
    steps: list[Step] = []
    for install in installs:
        for name in mapping.get(install.workflow_key, ()):
            steps.append(
                Step(
                    name=name,
                    install_id=str(getattr(install, "id", "") or "") or None,
                    payload=dict(getattr(install, "config", {}) or {}),
                )
            )
    return steps


async def _existing(
    session: AsyncSession, tenant_id: uuid.UUID, key: str
) -> WorkflowStepRun | None:
    return (
        await session.execute(
            select(WorkflowStepRun).where(
                WorkflowStepRun.tenant_id == tenant_id,
                WorkflowStepRun.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


async def run_step(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    step: Step,
    handler: Callable[[Mapping[str, Any], Mapping[str, Any]], Awaitable[Any]],
    context: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run one step, recording the attempt. Returns the step's result."""
    key = step.idempotency_key(call_id)
    row = await _existing(session, tenant_id, key)
    if row is not None and row.status == "succeeded":
        # Retry of work already done. Report it rather than repeating it.
        return {"step": step.name, "status": "skipped", "reason": "already succeeded"}

    if row is None:
        # An install reference that is not a UUID is a caller bug; it must not
        # abort a post-call run that is otherwise fine, so it is kept in the
        # preview and dropped from the foreign key.
        install_uuid = None
        if step.install_id:
            try:
                install_uuid = uuid.UUID(str(step.install_id))
            except (ValueError, TypeError, AttributeError):
                install_uuid = None
        row = WorkflowStepRun(
            tenant_id=tenant_id,
            install_id=install_uuid,
            call_id=call_id,
            step=step.name,
            phase="post_call",
            status="running",
            idempotency_key=key,
            request_preview={
                **{k: str(v)[:120] for k, v in step.payload.items()},
                **({"install_ref": str(step.install_id)[:80]} if install_uuid is None and step.install_id else {}),
            },
        )
        session.add(row)
    else:
        row.status = "running"
    row.attempts = int(row.attempts or 0) + 1
    await session.flush()

    started = clock()
    try:
        result = await handler(step.payload, dict(context or {}))
        row.status = "succeeded"
        row.duration_ms = (clock() - started) * 1000.0
        row.last_error = None
        await session.flush()
        return {"step": step.name, "status": "succeeded", "result": result, "attempts": row.attempts}
    except Exception as exc:
        row.status = "failed"
        row.duration_ms = (clock() - started) * 1000.0
        row.last_error = str(exc)[:500]
        await session.flush()
        return {
            "step": step.name,
            "status": "failed",
            "error": str(exc)[:200],
            "attempts": row.attempts,
            "retryable": step.idempotent and row.attempts < MAX_ATTEMPTS,
        }


async def run_post_call(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    steps: Iterable[Step],
    handlers: Mapping[str, Callable[[Mapping[str, Any], Mapping[str, Any]], Awaitable[Any]]],
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run every planned step. One failure never stops the rest."""
    results: list[dict[str, Any]] = []
    for step in steps:
        handler = handlers.get(step.name)
        if handler is None:
            results.append({"step": step.name, "status": "skipped", "reason": "no handler"})
            continue
        results.append(
            await run_step(
                session,
                tenant_id=tenant_id,
                call_id=call_id,
                step=step,
                handler=handler,
                context=context,
            )
        )
    return results
