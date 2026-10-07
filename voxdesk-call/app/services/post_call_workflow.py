"""Post-call adapter for the existing workflow interpreter, tables and job queue.

Only explicitly environment-bound triggers participate. Graphs and analysis
inputs are pinned by reference/digest. Database actions, execution checkpoint
and audit commit together; no network action or pretend timer is executed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.audit.service import record_event
from app.builder.workflow_repository import WorkflowRepository
from app.core.rate_limit import rate_limit
from app.db.enterprise_models import AnalysisResult, WorkflowTrigger
from app.db.models import AuditLog, Call, DurableJob, Environment, Lead, Tenant, Workflow, WorkflowExecution, WorkflowVersion
from app.db.telephony_models import PostCallStepRun
from app.domain.workflow_models import ExecutionStatus, NodeType
from app.jobs.queue import enqueue
from app.jobs.types import ExecutionResult, JobType, register_handler
from app.services import workflow_service as workflows

BINDING = "post_call_environment_id"
EVENTS = frozenset({"after_call", "on_completion", "on_failure", "on_booking"})
MAX_TRIGGERS = 20
ACTIONS = frozenset({"update_lead_status", "apply_dnc", "record_escalation_intent", "create_followup_intent"})
NODES = frozenset({NodeType.TRIGGER, NodeType.CONDITION, NodeType.ACTION, NodeType.TERMINAL, NodeType.HANDOFF})


def definition(graph, tenant_id):
    """Validate implemented capabilities rather than inheriting legacy facades."""
    value = workflows._definition_from_payload(graph, status="active")
    if value.tenant_id != str(tenant_id) or value.validate():
        raise ValueError("workflow_definition_invalid")
    if not value.entry_node or len(value.nodes) > 100 or len({n.id for n in value.nodes}) != len(value.nodes):
        raise ValueError("workflow_definition_invalid")
    if any(n.type not in NODES or (n.action and n.action.name not in ACTIONS) for n in value.nodes):
        raise ValueError("workflow_capability_unsupported")
    # Actions target the call's owned lead, never a graph-supplied arbitrary ID.
    if any(n.action and "lead_id" in n.action.params for n in value.nodes):
        raise ValueError("workflow_lead_override_unsupported")
    return value


def matches(event, call):
    status = getattr(call.status, "value", call.status)
    return (event == "after_call" or (event == "on_completion" and status == "completed")
            or (event == "on_failure" and status in {"failed", "no_answer", "cancelled"})
            or (event == "on_booking" and call.booked is True))


async def context(session, call):
    rows = list((await session.scalars(select(PostCallStepRun).where(
        PostCallStepRun.call_id == call.id, PostCallStepRun.tenant_id == call.tenant_id,
        PostCallStepRun.environment_id == call.environment_id, PostCallStepRun.pipeline_version == 1))).all())
    steps = {row.step: row for row in rows}
    if any(key not in steps or steps[key].status != "completed" for key in ("analyzed_webhook", "summary", "sentiment", "analysis_plan")):
        raise ValueError("analysis_incomplete")
    payload = {"call_id": str(call.id), "lead_id": str(call.lead_id) if call.lead_id else None,
               "call_status": getattr(call.status, "value", call.status), "summary": steps["summary"].output["summary"], "outcome": steps["summary"].output["outcome"],
               "sentiment": steps["sentiment"].output["sentiment"], "booked": call.booked, "custom": {}}
    for item in steps["analysis_plan"].output["schemas"]:
        step = steps.get(f"schema:{item['schema_id']}:v{item['version']}")
        if step is None or step.status != "completed":
            raise ValueError("analysis_incomplete")
        result = await session.scalar(select(AnalysisResult).where(
            AnalysisResult.id == uuid.UUID(step.output["analysis_result_id"]), AnalysisResult.tenant_id == call.tenant_id,
            AnalysisResult.environment_id == call.environment_id, AnalysisResult.call_id == call.id,
            AnalysisResult.status == "completed"))
        if result is None:
            raise ValueError("analysis_evidence_missing")
        payload["custom"][str(result.schema_id)] = result.result
    return payload


async def admit_post_call(session, call, tenant):
    """Caller locks Call and owns the transaction including admission audit."""
    triggers = list((await session.scalars(select(WorkflowTrigger).where(
        WorkflowTrigger.tenant_id == call.tenant_id, WorkflowTrigger.is_enabled.is_(True),
        WorkflowTrigger.event_type.in_(EVENTS),
        WorkflowTrigger.config[BINDING].as_string() == str(call.environment_id)
    ).order_by(WorkflowTrigger.id).limit(MAX_TRIGGERS + 1))).all())
    if len(triggers) > MAX_TRIGGERS:
        raise ValueError("workflow_trigger_limit")
    selected = [row for row in triggers if matches(row.event_type, call)]
    if not selected:
        return {"status": "not_requested", "job_ids": []}
    digest = workflows.request_fingerprint(await context(session, call))
    jobs = []
    for trigger in selected:
        workflow = await session.scalar(select(Workflow).where(
            Workflow.id == uuid.UUID(trigger.workflow_id), Workflow.tenant_id == call.tenant_id))
        version = None if workflow is None or workflow.published_version_id is None else await session.scalar(
            select(WorkflowVersion).where(WorkflowVersion.id == workflow.published_version_id,
                WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.tenant_id == call.tenant_id))
        payload = {"call_id": str(call.id), "trigger_id": str(trigger.id), "workflow_id": trigger.workflow_id,
                   "version_id": str(version.id) if version else None,
                   "graph_digest": workflows.request_fingerprint(version.graph_config) if version else None,
                   "analysis_digest": digest, "event_type": trigger.event_type}
        job, created = await enqueue(session, tenant_id=call.tenant_id, environment_id=call.environment_id,
            organization_id=tenant.organization_id, job_type=JobType.WORKFLOW_EXECUTION,
            idempotency_key=f"post-workflow:v1:{call.id}:{trigger.id}", payload=payload, max_attempts=4)
        if job.environment_id != call.environment_id or job.job_type != JobType.WORKFLOW_EXECUTION:
            raise ValueError("workflow_job_scope_conflict")
        if not created:
            admission = await session.scalar(select(AuditLog.id).where(AuditLog.tenant_id == tenant.id,
                AuditLog.environment_id == call.environment_id, AuditLog.event_type == "workflow.post_call_admitted",
                AuditLog.resource_id == str(job.id)))
            if admission is None or job.payload != payload or job.organization_id != tenant.organization_id:
                raise ValueError("workflow_job_identity_conflict")
        if created:
            await record_event(session, tenant_id=call.tenant_id, environment_id=call.environment_id,
                event_type="workflow.post_call_admitted", resource_type="job", resource_id=job.id)
        jobs.append(str(job.id))
    return {"status": "admitted", "job_ids": jobs}


async def execute(job, session_factory):
    """One atomic database-only graph; queue owns retries, leases and DLQ."""
    payload = job.payload or {}
    expected = {"call_id", "trigger_id", "workflow_id", "version_id", "graph_digest", "analysis_digest", "event_type"}
    if not isinstance(payload, dict) or set(payload) != expected:
        return ExecutionResult.permanent("workflow_payload_invalid")
    try:
        call_id, trigger_id, workflow_id = (uuid.UUID(payload[key]) for key in ("call_id", "trigger_id", "workflow_id"))
        version_id = uuid.UUID(payload["version_id"]) if payload["version_id"] else None
    except (TypeError, ValueError, AttributeError):
        return ExecutionResult.permanent("workflow_payload_invalid")
    async with session_factory() as session:
        call = await session.scalar(select(Call).where(Call.id == call_id, Call.tenant_id == job.tenant_id,
            Call.environment_id == job.environment_id).with_for_update())
        current_job = await session.scalar(select(DurableJob).where(DurableJob.id == job.id,
            DurableJob.tenant_id == job.tenant_id, DurableJob.environment_id == job.environment_id,
            DurableJob.organization_id == job.organization_id).with_for_update())
        admission = await session.scalar(select(PostCallStepRun).where(PostCallStepRun.call_id == call_id,
            PostCallStepRun.tenant_id == job.tenant_id, PostCallStepRun.environment_id == job.environment_id,
            PostCallStepRun.pipeline_version == 1, PostCallStepRun.step == "workflow_admission",
            PostCallStepRun.status == "completed"))
        if (admission is None or str(job.id) not in admission.output.get("job_ids", [])
                or current_job is None or current_job.idempotency_key != f"post-workflow:v1:{call_id}:{trigger_id}"):
            return ExecutionResult.permanent("workflow_admission_missing")
        now = datetime.now(timezone.utc)
        if current_job is None or current_job.payload != payload or current_job.job_type != JobType.WORKFLOW_EXECUTION:
            return ExecutionResult.permanent("workflow_job_scope_invalid")
        expiry = current_job.leased_until
        if (current_job.status != "running" or current_job.worker_id != job.worker_id or not expiry
                or expiry.replace(tzinfo=timezone.utc) <= now or current_job.cancel_requested):
            return ExecutionResult.permanent("workflow_lease_or_cancellation")
        tenant = await session.scalar(select(Tenant).where(Tenant.id == job.tenant_id, Tenant.organization_id == job.organization_id))
        env = await session.scalar(select(Environment).where(Environment.id == job.environment_id, Environment.tenant_id == job.tenant_id))
        if call is None or tenant is None or env is None or tenant.lifecycle_status != "active" or env.status != "active":
            return ExecutionResult.permanent("workflow_scope_unavailable")
        execution_id = uuid.uuid5(job.id, "post-call-workflow:v1")
        row = await session.scalar(select(WorkflowExecution).where(WorkflowExecution.id == execution_id,
            WorkflowExecution.tenant_id == job.tenant_id).with_for_update())
        if row is not None:
            if row.input_payload.get("job_id") != str(job.id) or row.input_payload.get("environment_id") != str(env.id):
                return ExecutionResult.permanent("workflow_execution_scope_invalid")
            if row.status == "completed":
                return ExecutionResult.ok("Previously committed workflow execution")
            if not current_job.replay_count:
                return ExecutionResult.permanent("workflow_execution_failed")
        trigger = await session.scalar(select(WorkflowTrigger).where(WorkflowTrigger.id == trigger_id,
            WorkflowTrigger.tenant_id == tenant.id, WorkflowTrigger.is_enabled.is_(True)))
        workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant.id))
        version = await session.scalar(select(WorkflowVersion).where(WorkflowVersion.id == version_id,
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.tenant_id == tenant.id)) if version_id else None
        if (trigger is None or trigger.config.get(BINDING) != str(env.id) or trigger.workflow_id != str(workflow_id)
                or trigger.event_type != payload["event_type"] or not matches(trigger.event_type, call)
                or workflow is None or workflow.status != "active" or version is None or version.status != "published"):
            return ExecutionResult.permanent("workflow_not_configured")
        if workflows.request_fingerprint(version.graph_config) != payload["graph_digest"]:
            return ExecutionResult.permanent("workflow_version_changed")
        try:
            graph = definition(version.graph_config, tenant.id)
            values = await context(session, call)
            if workflows.request_fingerprint(values) != payload["analysis_digest"]:
                return ExecutionResult.permanent("workflow_analysis_changed")
        except (ValueError, KeyError, TypeError):
            return ExecutionResult.permanent("workflow_unsupported_or_missing_evidence")
        if any(n.action and n.action.name in {"update_lead_status", "apply_dnc"} for n in graph.nodes):
            lead = await session.scalar(select(Lead).where(Lead.id == call.lead_id,
                Lead.tenant_id == tenant.id, Lead.environment_id == env.id).with_for_update())
            if lead is None:
                return ExecutionResult.permanent("workflow_lead_not_found")
        try:
            allowed = await rate_limit(f"workflow:execution:{tenant.id}:{env.id}", 30, 60)
        except RuntimeError:
            return ExecutionResult.permanent("workflow_redis_not_configured")
        if not allowed:
            return ExecutionResult.transient("workflow_rate_limited")
        if row is None:
            row = WorkflowExecution(id=execution_id, workflow_id=workflow.id, workflow_version_id=version.id,
                tenant_id=tenant.id, status="running", current_node_id=graph.entry_node, attempt_count=0,
                input_payload={"post_call_managed": True, "job_id": str(job.id), "call_id": str(call.id),
                               "environment_id": str(env.id)}, output_metadata={}, checkpoint_data={})
            session.add(row)
        row.output_metadata, row.checkpoint_data = {}, {}
        row.status, row.current_node_id = "running", graph.entry_node
        row.attempt_count += 1
        row.started_at = now
        await session.flush()
        execution = workflows._execution_from_row(WorkflowRepository._execution_dict(row), str(workflow.id))
        # No intermediate commit: bounded DB-only actions and checkpoint are atomic.
        savepoint = await session.begin_nested()
        result = await workflows._walk(str(tenant.id), graph, execution, values, session)
        success = result.status is ExecutionStatus.COMPLETED
        if success:
            await savepoint.commit()
        else:
            await savepoint.rollback()
        output = {"steps": [workflows._step_payload(step) for step in result.steps], "effects_committed": success}
        await WorkflowRepository(session).transition_execution(execution_id, result.status.value,
            current_node_id=result.current_node, checkpoint=output, tenant_id=tenant.id,
            output_metadata=output, error_metadata={} if success else {"code": "workflow_graph_failed"})
        await record_event(session, tenant_id=tenant.id, environment_id=env.id,
            event_type="workflow.post_call_completed" if success else "workflow.post_call_failed",
            resource_type="workflow_execution", resource_id=execution_id)
        await session.commit()
        return ExecutionResult.ok("Workflow effects and checkpoint committed") if success else ExecutionResult.permanent("workflow_graph_failed")


@register_handler(JobType.WORKFLOW_EXECUTION)
async def handle_workflow(job):
    from app.db.session import get_sessionmaker
    return await execute(job, get_sessionmaker())
