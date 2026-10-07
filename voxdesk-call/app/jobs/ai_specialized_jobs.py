"""Durable job handlers for specialized-agent execution references.

The existing ``jobs`` table and registry remain authoritative. Payloads carry
only tenant/org/environment and execution IDs, never prompts or raw source
content. A provider work item that lacks a privacy-approved durable input
reference fails explicitly instead of inventing or reconstructing data.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.logging import log
from app.db.models import DurableJob
from app.db.session import get_sessionmaker
from app.jobs.models import PermanentJobError
from app.jobs.types import register_handler
from app.specialized_agents.enums import ExecutionStatus


SPECIALIZED_TRANSLATION = "specialized.translation"
SPECIALIZED_INSIGHT = "specialized.insight"
SPECIALIZED_FORECAST = "specialized.forecast"
SPECIALIZED_ANOMALY = "specialized.anomaly"


def _uuid_value(value: object, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


async def enqueue_specialized(session, *, tenant_id: uuid.UUID, organization_id: uuid.UUID, environment_id: uuid.UUID, execution_id: uuid.UUID, agent_type: str, idempotency_key: str, max_attempts: int = 5):
    from app.jobs.queue import enqueue
    from app.specialized_agents.executor import SpecializedExecutionRecord
    from app.db.models import Environment, Organization, Tenant
    from app.governance.context import GovernanceScope
    from app.governance.evidence import append_event

    types = {"translation": SPECIALIZED_TRANSLATION, "insight": SPECIALIZED_INSIGHT, "forecasting": SPECIALIZED_FORECAST, "anomaly": SPECIALIZED_ANOMALY}
    job_type = types.get(agent_type)
    if job_type is None:
        raise ValueError("unsupported asynchronous specialized agent")
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
        raise ValueError("a valid idempotency key is required")
    execution = await session.scalar(select(SpecializedExecutionRecord).where(
        SpecializedExecutionRecord.id == execution_id,
        SpecializedExecutionRecord.tenant_id == tenant_id,
        SpecializedExecutionRecord.organization_id == organization_id,
        SpecializedExecutionRecord.environment_id == environment_id,
        SpecializedExecutionRecord.agent_type == agent_type,
    ))
    if execution is None:
        raise ValueError("specialized execution not found in the requested tenant and environment")
    tenant = await session.get(Tenant, tenant_id)
    organization = await session.get(Organization, organization_id)
    environment = await session.get(Environment, environment_id)
    if (
        tenant is None or organization is None or environment is None
        or tenant.organization_id != organization_id
        or environment.tenant_id != tenant_id
    ):
        raise ValueError("specialized job hierarchy is invalid")
    scope = GovernanceScope(tenant=tenant, organization=organization, environment=environment)
    payload = {"tenant_id": str(tenant_id), "organization_id": str(organization_id), "environment_id": str(environment_id), "execution_id": str(execution_id), "agent_type": agent_type}
    job, created = await enqueue(session, tenant_id=tenant_id, organization_id=organization_id, environment_id=environment_id, job_type=job_type, idempotency_key=idempotency_key, payload=payload, max_attempts=max_attempts)
    if not created and ((job.payload or {}) != payload or job.job_type != job_type):
        raise ValueError("specialized job idempotency key conflicts with another execution")
    if created:
        await append_event(
            session, scope, event_type="specialized_agent_job_enqueued",
            payload={"job_id": str(job.id), "execution_id": str(execution_id), "agent_type": agent_type, "job_type": job_type},
            actor_user_id=None, actor_type="service", correlation_id=execution.trace_id,
            subject_type="specialized_agent_execution", subject_id=str(execution_id),
        )
    return job, created


async def _run_job(job: DurableJob, expected_type: str) -> None:
    payload = dict(job.payload or {})
    tenant_id = _uuid_value(payload.get("tenant_id"), "tenant_id")
    organization_id = _uuid_value(payload.get("organization_id"), "organization_id")
    environment_id = _uuid_value(payload.get("environment_id"), "environment_id")
    execution_id = _uuid_value(payload.get("execution_id"), "execution_id")
    if tenant_id != job.tenant_id or organization_id != job.organization_id or environment_id != job.environment_id:
        raise ValueError("job scope does not match persisted payload")
    if payload.get("agent_type") != expected_type or job.status not in {"running", "queued", "retry_scheduled"}:
        raise ValueError("unknown or invalid specialized job state")
    maker = get_sessionmaker()
    async with maker() as session:
        from app.db.rls import set_tenant_context
        await set_tenant_context(session, tenant_id)
        from app.db.models import Tenant, Organization, Environment
        tenant = await session.get(Tenant, tenant_id)
        environment = await session.get(Environment, environment_id)
        organization = await session.get(Organization, organization_id)
        if tenant is None or environment is None or organization is None or tenant.organization_id != organization_id or environment.tenant_id != tenant_id:
            raise ValueError("persisted job scope is invalid")
        row = await session.scalar(select(__import__("app.specialized_agents.executor", fromlist=["SpecializedExecutionRecord"]).SpecializedExecutionRecord).where(__import__("app.specialized_agents.executor", fromlist=["SpecializedExecutionRecord"]).SpecializedExecutionRecord.id == execution_id, __import__("app.specialized_agents.executor", fromlist=["SpecializedExecutionRecord"]).SpecializedExecutionRecord.tenant_id == tenant_id, __import__("app.specialized_agents.executor", fromlist=["SpecializedExecutionRecord"]).SpecializedExecutionRecord.organization_id == organization_id, __import__("app.specialized_agents.executor", fromlist=["SpecializedExecutionRecord"]).SpecializedExecutionRecord.environment_id == environment_id, __import__("app.specialized_agents.executor", fromlist=["SpecializedExecutionRecord"]).SpecializedExecutionRecord.agent_type == expected_type))
        if row is None:
            raise ValueError("specialized execution not found in job scope")
        if row.status in {ExecutionStatus.SUCCEEDED.value, ExecutionStatus.REVIEW_REQUIRED.value}:
            log.info("specialized_job.already_terminal", job_id=str(job.id), execution_id=str(row.id), tenant_id=str(tenant_id), environment_id=str(environment_id))
            return
        # There is intentionally no provider handler that can reconstruct raw
        # user inputs from fingerprints. Durable inputs must be supplied by a
        # future privacy-policy-approved document store.
        row.status = ExecutionStatus.FAILED.value
        row.completed_at = dt.datetime.now(dt.timezone.utc)
        row.failure_code = "durable_input_unavailable"
        row.result = {"error_code": "durable_input_unavailable"}
        from app.governance.context import GovernanceScope
        from app.governance.evidence import append_event
        scope = GovernanceScope(tenant=tenant, organization=organization, environment=environment)
        evidence = await append_event(
            session, scope, event_type="specialized_agent_async_failed",
            payload={"job_id": str(job.id), "execution_id": str(row.id), "agent_type": expected_type, "reason_code": "durable_input_unavailable", "input_fingerprint": row.input_fingerprint},
            actor_user_id=None, correlation_id=row.trace_id, subject_type="specialized_agent_execution", subject_id=str(row.id),
        )
        row.evidence_root_hash = evidence.event_hash
        await session.flush()
        log.warning("specialized_job.failed", job_id=str(job.id), execution_id=str(row.id), tenant_id=str(tenant_id), environment_id=str(environment_id), reason="durable_input_unavailable", trace_id=row.trace_id, request_id=row.request_id)
        await session.commit()
        raise PermanentJobError("durable_input_unavailable", "privacy-approved durable execution input is unavailable")


@register_handler(SPECIALIZED_TRANSLATION)
async def handle_translation(job: DurableJob):
    await _run_job(job, "translation")


@register_handler(SPECIALIZED_INSIGHT)
async def handle_insight(job: DurableJob):
    await _run_job(job, "insight")


@register_handler(SPECIALIZED_FORECAST)
async def handle_forecast(job: DurableJob):
    await _run_job(job, "forecasting")


@register_handler(SPECIALIZED_ANOMALY)
async def handle_anomaly(job: DurableJob):
    await _run_job(job, "anomaly")
