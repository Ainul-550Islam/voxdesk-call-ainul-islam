"""Durable post-call analysis core on the existing job platform.

Terminal call publication admits POST_CALL in the same transaction. Each step
locks its owned Call, checkpoints real results, and commits independently. A
completed checkpoint is never re-executed on retry. Provider failures are
recorded without raw exception/prompt text; database failures propagate.

The current executable steps finalize stored transcript evidence and persist
summary/outcome, sentiment and pinned custom-schema extraction, followed by a
transactional call_analyzed fact. That fact certifies analysis only, not QA, CRM
or workflow completion. QA sampling admits real child jobs. CRM admission uses
the existing delivery queue; workflow execution remains pending. Custom
backfill uses the existing analysis service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ProviderError
from app.ai import post_call_llm
from app.ai.models import GovernanceError
from app.core.ssrf import OutboundUrlError
from app.db.models import Call, Environment, Tenant, DurableJob
from app.db.telephony_models import PostCallStepRun
from app.jobs.idempotency import post_call_key
from app.qa.exceptions import QaError
from app.qa import sampling
from app.jobs.models import PermanentJobError
from app.jobs.queue import enqueue
from app.jobs.types import ExecutionResult, JobType, register_handler
from app.telephony.call_state import is_terminal
from app.services import post_call_analysis_service as analysis
from app.telephony.transcription import finalize_stored_turns, TranscriptUnavailable

PIPELINE_VERSION = 1
CORE_STEPS = ("analysis_plan", "transcript", "summary", "sentiment")


def _now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _WorkerAIContext:
    """Persisted tenant scope for a system job, not a fabricated human actor.

    The gateway uses tenant/tenant_id/environment_id. This context deliberately
    has no can(), role or user and cannot authorize an HTTP route.
    """
    tenant: Tenant
    environment_id: uuid.UUID

    @property
    def tenant_id(self):
        return self.tenant.id


async def enqueue_post_call(session: AsyncSession, call: Call):
    """Flush only. The caller owns commit/rollback of call, event and job."""
    if not is_terminal(call.status):
        raise ValueError("Post-call processing requires a terminal call")
    tenant = await session.scalar(select(Tenant).where(Tenant.id == call.tenant_id))
    if tenant is None or call.environment_id is None:
        raise PermanentJobError("post_call_scope_missing", "Persisted call scope is required")
    job, created = await enqueue(
        session, tenant_id=call.tenant_id, organization_id=tenant.organization_id,
        environment_id=call.environment_id, job_type=JobType.POST_CALL,
        idempotency_key=post_call_key(call.id),
        payload={"call_id": str(call.id), "pipeline_version": PIPELINE_VERSION},
        max_attempts=4,
    )
    if (job.job_type != JobType.POST_CALL or job.environment_id != call.environment_id
            or job.organization_id != tenant.organization_id
            or job.payload != {"call_id": str(call.id), "pipeline_version": PIPELINE_VERSION}):
        raise PermanentJobError("post_call_scope_conflict", "Existing job identity does not match call scope")
    return job, created


class PostCallPipeline:
    def __init__(self, session_factory, *, executor=None):
        self.session_factory = session_factory
        self.executor = executor

    async def run(self, call_id: uuid.UUID, *, tenant_id: uuid.UUID,
                  environment_id: uuid.UUID, organization_id: uuid.UUID,
                  job_id: uuid.UUID | None = None, retry_nontransient: bool = False) -> ExecutionResult:
        """One bounded attempt; scope is taken from the persisted job columns.

        One failing analysis step does not prevent independent later steps.
        Infrastructure/database failure aborts the attempt: pretending another
        step committed when the database is unavailable would be false success.
        """
        results = []
        steps = list(CORE_STEPS)
        for step in steps:
            async with self.session_factory() as session:
                call = await session.scalar(select(Call).where(
                    Call.id == call_id, Call.tenant_id == tenant_id,
                    Call.environment_id == environment_id,
                ).with_for_update().execution_options(populate_existing=True))
                if call is None:
                    raise PermanentJobError("post_call_not_found", "Call not found in job scope")
                if not is_terminal(call.status):
                    raise PermanentJobError("post_call_not_terminal", "Call is not terminal")
                tenant = await session.scalar(select(Tenant).where(
                    Tenant.id == tenant_id, Tenant.organization_id == organization_id))
                environment = await session.scalar(select(Environment).where(
                    Environment.id == environment_id, Environment.tenant_id == tenant_id))
                if tenant is None or environment is None:
                    raise PermanentJobError("post_call_not_found", "Call not found in job scope")
                if tenant.lifecycle_status != "active" or environment.status != "active":
                    raise PermanentJobError("post_call_scope_inactive", "Job scope is inactive")
                if job_id is not None:
                    job = await session.scalar(select(DurableJob).where(
                        DurableJob.id == job_id, DurableJob.tenant_id == tenant_id,
                        DurableJob.environment_id == environment_id,
                        DurableJob.organization_id == organization_id,
                        DurableJob.job_type == JobType.POST_CALL,
                    ))
                    if job is None:
                        raise PermanentJobError("post_call_job_not_found", "Job not found in scope")
                    if job.cancel_requested or job.status == "cancelled":
                        return ExecutionResult.permanent("post_call_cancelled")
                row = await session.scalar(select(PostCallStepRun).where(
                    PostCallStepRun.tenant_id == tenant_id,
                    PostCallStepRun.environment_id == environment_id,
                    PostCallStepRun.call_id == call_id, PostCallStepRun.step == step,
                    PostCallStepRun.pipeline_version == PIPELINE_VERSION,
                ))
                if row is not None and (row.status == "completed" or (
                        not row.retryable and not retry_nontransient)):
                    if step == "analysis_plan" and row.status == "completed":
                        steps.extend(self._custom_steps(row.output))
                    results.append((row.status, row.retryable))
                    continue
                if row is None:
                    row = PostCallStepRun(tenant_id=tenant_id, environment_id=environment_id,
                                          call_id=call_id, step=step, attempts=0,
                                          pipeline_version=PIPELINE_VERSION, output={}, telemetry=[])
                    session.add(row)
                row.job_id = job_id
                row.attempts += 1
                row.status, row.error, row.retryable = "running", "", False
                row.started_at, row.finished_at = _now(), None
                await session.flush()
                try:
                    # Catch provider/domain failures inside the transaction so
                    # already measured usage from a rejected JSON repair stays
                    # attributable. No broad rollback discards a paid attempt.
                    await self._execute(session, call, row, tenant, environment)
                except SQLAlchemyError:
                    raise
                except TranscriptUnavailable as exc:
                    row.status = "unsupported" if exc.code == "transcript_too_large" else "blocked"
                    row.error = exc.code
                    # Late transcription can arrive after a terminal callback.
                    row.retryable = exc.code == "no_transcript"
                except QaError as exc:
                    row.status, row.error = "blocked", str(exc.code)[:64]
                except ProviderError as exc:
                    row.status, row.error = "failed", exc.category[:64]
                    row.retryable = bool(exc.retryable)
                except GovernanceError as exc:
                    row.status, row.error = "blocked", str(exc.code)[:64]
                except OutboundUrlError:
                    row.status, row.error = "blocked", "unsafe_provider_destination"
                except Exception:
                    # This is an explicit failed checkpoint, not ignored failure.
                    # The aggregate job will fail after independent steps run.
                    row.status, row.error = "failed", "post_call_internal_error"
                row.finished_at = _now()
                await session.commit()
                if step == "analysis_plan" and row.status == "completed":
                    steps.extend(self._custom_steps(row.output))
                results.append((row.status, row.retryable))
        if all(status == "completed" for status, _ in results):
            return ExecutionResult.ok("Stored transcript, built-in and pinned custom analysis completed")
        if any(retryable for _, retryable in results):
            return ExecutionResult.transient("post_call_step_retryable", "One or more core analysis steps require retry")
        return ExecutionResult.permanent("post_call_step_incomplete", "One or more core analysis steps could not complete")

    @staticmethod
    def _custom_steps(output):
        return [f"schema:{item['schema_id']}:v{item['version']}" for item in output["schemas"]] + ["qa_sampling", "analyzed_webhook", "crm_admission"]

    async def _execute(self, session, call, row, tenant, environment):
        if row.step == "crm_admission":
            from app.integrations.crm.events import admit_post_call
            checkpoints = list((await session.scalars(select(PostCallStepRun).where(
                PostCallStepRun.tenant_id == call.tenant_id, PostCallStepRun.environment_id == call.environment_id,
                PostCallStepRun.call_id == call.id, PostCallStepRun.pipeline_version == PIPELINE_VERSION))).all())
            by_step = {item.step: item for item in checkpoints}
            barrier = by_step.get("analyzed_webhook")
            if barrier is None or barrier.status != "completed":
                row.status, row.error = "blocked", "analysis_incomplete"
                row.retryable = barrier is None or barrier.retryable
                return
            values = {key: item.output for key, item in by_step.items()
                      if key in {"summary", "sentiment"} or key.startswith("schema:")}
            from app.db.enterprise_models import AnalysisResult
            for key in list(values):
                if key.startswith("schema:"):
                    result = await session.scalar(select(AnalysisResult).where(
                        AnalysisResult.id == uuid.UUID(values[key]["analysis_result_id"]),
                        AnalysisResult.tenant_id == call.tenant_id, AnalysisResult.environment_id == call.environment_id,
                        AnalysisResult.call_id == call.id, AnalysisResult.status == "completed"))
                    if result is None:
                        raise ValueError("Pinned CRM analysis result is unavailable")
                    values[key] = {"analysis_result_id": str(result.id), "schema_version": result.schema_version,
                                   "fields": result.result}
            row.output = await admit_post_call(session, call, tenant, values)
            row.status = "completed"
            return
        if row.step == "qa_sampling":
            async with session.begin_nested():
                row.output = await sampling.admit_post_call(session, call, organization_id=tenant.organization_id)
            row.status = "completed"
            return
        if row.step == "analysis_plan":
            row.output = {"schemas": await analysis.pin_schemas(session, call)}
            row.status = "completed"
            return
        if row.step == "analyzed_webhook":
            rows = (await session.scalars(select(PostCallStepRun).where(
                PostCallStepRun.call_id == call.id, PostCallStepRun.tenant_id == call.tenant_id,
                PostCallStepRun.environment_id == call.environment_id,
                PostCallStepRun.pipeline_version == PIPELINE_VERSION))).all()
            by_step = {item.step: item for item in rows}
            plan = by_step.get("analysis_plan")
            required = set(CORE_STEPS)
            if plan is not None and plan.status == "completed":
                required.update(self._custom_steps(plan.output))
            required.discard("analyzed_webhook")
            required.discard("qa_sampling")
            required.discard("crm_admission")
            incomplete = [by_step.get(key) for key in required
                          if key not in by_step or by_step[key].status != "completed"]
            if incomplete:
                row.status, row.error = "blocked", "analysis_incomplete"
                row.retryable = any(item is None or item.retryable for item in incomplete)
                return
            ids = [by_step[key].output["analysis_result_id"] for key in sorted(required) if key.startswith("schema:")]
            from app.webhooks.call_event_bridge import publish_call_event
            await publish_call_event(session, call, "call_analyzed", {"analysis_result_ids": ids})
            row.output, row.status = {"analysis_result_ids": ids}, "completed"
            return
        if row.step == "transcript":
            _, snapshot = await finalize_stored_turns(session, call)
            row.output, row.status = snapshot, "completed"
            return
        transcript_step = await session.scalar(select(PostCallStepRun).where(
            PostCallStepRun.tenant_id == call.tenant_id,
            PostCallStepRun.environment_id == call.environment_id,
            PostCallStepRun.call_id == call.id,
            PostCallStepRun.pipeline_version == PIPELINE_VERSION,
            PostCallStepRun.step == "transcript",
        ))
        if transcript_step is None or transcript_step.status != "completed":
            row.status, row.error = "blocked", "transcript_not_finalized"
            row.retryable = bool(transcript_step is not None and transcript_step.retryable)
            return
        text, _ = await finalize_stored_turns(session, call, snapshot=transcript_step.output)
        if row.step.startswith("schema:"):
            plan = await session.scalar(select(PostCallStepRun).where(
                PostCallStepRun.call_id == call.id, PostCallStepRun.tenant_id == call.tenant_id,
                PostCallStepRun.environment_id == call.environment_id,
                PostCallStepRun.pipeline_version == PIPELINE_VERSION,
                PostCallStepRun.step == "analysis_plan"))
            definition = next(item for item in plan.output["schemas"]
                              if row.step == f"schema:{item['schema_id']}:v{item['version']}")
            output = await analysis.extract(session, _WorkerAIContext(tenant, call.environment_id),
                call, definition, text, executor=self.executor, environment_kind=environment.kind)
            row.status, row.error = output.status, output.error
            row.telemetry = list(row.telemetry or []) + list(output.invocations)
            if output.status == "completed":
                row.output = output.value
            return
        function = post_call_llm.summarize if row.step == "summary" else post_call_llm.classify_sentiment
        output = await function(session, _WorkerAIContext(tenant, call.environment_id), text,
                                call_id=call.id, environment_kind=environment.kind,
                                executor=self.executor)
        row.status, row.error = output.status, output.error
        row.telemetry = list(row.telemetry or []) + list(output.invocations)
        if output.status == "completed":
            row.output = output.value
            # Preserve existing human/runtime summary; the checkpoint still
            # stores this model's actual classification. Never infer booked=True
            # or write a human QA outcome from a model's opinion.
            if row.step == "summary" and not call.summary:
                call.summary = output.value["summary"]


def make_post_call_handler(session_factory=None, *, executor=None):
    async def handler(job):
        from app.db.session import get_sessionmaker
        payload = job.payload or {}
        if not isinstance(payload, dict) or set(payload) != {"call_id", "pipeline_version"}:
            raise PermanentJobError("post_call_payload_invalid", "Only call and pipeline references are supported")
        try:
            call_id = uuid.UUID(payload["call_id"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise PermanentJobError("post_call_payload_invalid", "Invalid call reference") from exc
        version = payload.get("pipeline_version")
        if type(version) is not int or version != PIPELINE_VERSION:
            raise PermanentJobError("post_call_version_unsupported", "Unsupported pipeline version")
        if job.environment_id is None or job.organization_id is None:
            raise PermanentJobError("post_call_scope_missing", "Persisted job scope is required")
        pipeline = PostCallPipeline(session_factory or get_sessionmaker(), executor=executor)
        return await pipeline.run(call_id, tenant_id=job.tenant_id,
                                  environment_id=job.environment_id, organization_id=job.organization_id,
                                  job_id=job.id, retry_nontransient=bool(job.replay_count))
    return handler


@register_handler(JobType.POST_CALL)
async def handle_post_call(job):
    return await make_post_call_handler()(job)
