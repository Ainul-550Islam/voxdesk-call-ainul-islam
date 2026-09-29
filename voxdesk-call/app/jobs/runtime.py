"""Common bounded execution context over the existing durable queue and worker."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import log
from app.jobs import queue
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.registry import bootstrap
from app.jobs.types import JobPriority
from app.observability.runtime import observe_runtime_event


@dataclass(frozen=True)
class JobExecutionContext:
    tenant_id: uuid.UUID
    organization_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    request_id: str
    trace_id: str
    execution_id: str
    job_id: uuid.UUID
    job_type: str
    attempt: int


_CURRENT: ContextVar[JobExecutionContext | None] = ContextVar("voxdesk_job_context", default=None)


def current_job_context() -> JobExecutionContext | None:
    return _CURRENT.get()


def context_from_persisted_job(job) -> JobExecutionContext:
    """Scope always comes from DurableJob columns, never payload-supplied scope."""
    tenant_id = getattr(job, "tenant_id", None)
    if tenant_id is None:
        raise PermanentJobError("job_scope_missing", "persisted tenant scope is required")
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    # If a payload repeats scope, it must agree with authoritative columns.
    for key, actual in (("tenant_id", tenant_id), ("organization_id", job.organization_id), ("environment_id", job.environment_id)):
        claimed = payload.get(key)
        if claimed is not None and str(claimed) != str(actual):
            raise PermanentJobError("job_scope_mismatch", "job payload scope does not match persisted scope")
    return JobExecutionContext(
        tenant_id=tenant_id,
        organization_id=job.organization_id,
        environment_id=job.environment_id,
        request_id=str(payload.get("request_id") or job.id),
        trace_id=str(payload.get("trace_id") or job.id),
        execution_id=str(payload.get("execution_id") or job.id),
        job_id=job.id,
        job_type=str(job.job_type),
        attempt=int(job.attempt_count or 0),
    )


async def run_job_handler(job, handler: Callable[[Any], Awaitable[Any]], *, timeout_seconds: float = 900.0):
    """Apply persisted scope, correlation, bounded timeout, and safe failure logging."""
    if timeout_seconds <= 0:
        raise ValueError("job timeout must be positive")
    context = context_from_persisted_job(job)
    token: Token = _CURRENT.set(context)
    component = "specialized_job" if context.job_type.startswith("specialized.") else "deployment" if context.job_type == "deployment" else "queue"
    started = asyncio.get_running_loop().time()
    observe_runtime_event(component, "execution", "started", tenant_id=context.tenant_id, environment_id=context.environment_id, request_id=context.request_id, trace_id=context.trace_id, execution_id=context.execution_id, job_id=context.job_id)
    log.info("jobs.execution_started", job_id=str(context.job_id), job_type=context.job_type, tenant_id=str(context.tenant_id), organization_id=str(context.organization_id) if context.organization_id else None, environment_id=str(context.environment_id) if context.environment_id else None, request_id=context.request_id, trace_id=context.trace_id, execution_id=context.execution_id, attempt=context.attempt)
    try:
        try:
            result = await asyncio.wait_for(handler(job), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise RetryableJobError("job_timeout", "job execution exceeded its bounded timeout") from exc
        elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
        observe_runtime_event(component, "execution", "success", duration_ms=elapsed_ms, tenant_id=context.tenant_id, environment_id=context.environment_id, request_id=context.request_id, trace_id=context.trace_id, execution_id=context.execution_id, job_id=context.job_id)
        log.info("jobs.execution_finished", job_id=str(context.job_id), job_type=context.job_type, state="handler_returned")
        return result
    except Exception as exc:
        elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
        category = getattr(exc, "category", "unhandled")
        observe_runtime_event(component, "execution", "retrying" if getattr(exc, "retryable", False) else "failure", duration_ms=elapsed_ms, reason_code=category, tenant_id=context.tenant_id, environment_id=context.environment_id, request_id=context.request_id, trace_id=context.trace_id, execution_id=context.execution_id, job_id=context.job_id)
        log.warning("jobs.execution_failed", job_id=str(context.job_id), job_type=context.job_type, category=category, error_type=type(exc).__name__, tenant_id=str(context.tenant_id), environment_id=str(context.environment_id) if context.environment_id else None, trace_id=context.trace_id)
        raise
    finally:
        _CURRENT.reset(token)


class DurableJobRuntime:
    """Convenience API over the single canonical queue and worker implementation."""
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def enqueue(self, *, tenant_id: uuid.UUID, job_type: str, idempotency_key: str, payload: dict, organization_id: uuid.UUID, environment_id: uuid.UUID, priority: JobPriority = JobPriority.NORMAL):
        async with self.session_factory() as session:
            row, created = await queue.enqueue(session, tenant_id=tenant_id, organization_id=organization_id, environment_id=environment_id, job_type=job_type, idempotency_key=idempotency_key, payload=payload, priority=priority)
            await session.commit()
            return row, created

    async def run_once(self, *, worker_id: str | None = None):
        bootstrap()
        from app.jobs.worker import JobWorker
        return await JobWorker(self.session_factory, worker_id=worker_id).run_once()

    def handlers(self):
        return bootstrap()
