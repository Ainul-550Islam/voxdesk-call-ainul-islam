"""Independent PostgreSQL pipelines/workers; deterministic model test executor."""
import asyncio
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


async def main():
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import settings
    from app.db.models import Tenant, Call, CallStatus, CallDirection, Turn, Speaker, Lead, LeadStatus, DurableJob, WorkflowExecution, AuditLog
    from app.db.enterprise_models import WorkflowTrigger, AnalysisResult
    from app.builder.workflow_repository import WorkflowRepository
    from app.services import post_call_workflow as runtime, post_call_analysis_service as analysis
    from app.telephony.post_call import PostCallPipeline
    from app.qa import rubrics, sampling
    from app.qa.models import AutoReviewRun, QAReview
    from app.jobs.types import JobType
    from app.jobs.worker import JobWorker
    from app.outbox.models import OutboxEvent
    settings.redis_url = "redis://127.0.0.1:56379/0"
    engine = create_async_engine("postgresql+asyncpg://user@127.0.0.1:55432/post_call_test")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            tenant = Tenant(name="Workflow PG contract", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7), llm_preset="fast")
            session.add(tenant)
            await session.flush()
            lead = Lead(tenant_id=tenant.id, name="Test lead", phone="+15551230000", status=LeadStatus.CALLED)
            session.add(lead)
            await session.flush()
            call = Call(tenant_id=tenant.id, lead_id=lead.id, call_sid="CA" + uuid.uuid4().hex, from_number=lead.phone,
                to_number=tenant.twilio_number, status=CallStatus.COMPLETED, direction=CallDirection.INBOUND)
            session.add(call)
            await session.flush()
            session.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Please book Tuesday"))
            await analysis.create_schema(session, tenant_id=tenant.id, environment_id=call.environment_id,
                name="Intent", description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
            card = await rubrics.create_scorecard(session, tenant_id=tenant.id, environment_id=call.environment_id,
                name="PG card", sections=[{"name": "Opening", "items": [{"name": "Clear", "required": True, "min_score": 0, "max_score": 100}]}])
            await sampling.create_rule(session, tenant_id=tenant.id, environment_id=call.environment_id,
                name="Every call", kind="percentage", percent=100, scorecard_id=card.id)
            graph = {"id": "pg-" + uuid.uuid4().hex, "tenant_id": str(tenant.id), "name": "Qualify",
                "entry_node": "act", "nodes": [
                    {"id": "act", "type": "action", "action": {"name": "update_lead_status", "params": {"status": "qualified"}}, "next": "end"},
                    {"id": "end", "type": "terminal"}]}
            repo = WorkflowRepository(session)
            flow = await repo.create_workflow(tenant.id, "Qualify", graph, slug=graph["id"])
            version = await repo.create_version(flow["db_id"], graph, 1, tenant_id=tenant.id)
            await repo.publish_version(tenant.id, flow["db_id"], version["id"])
            session.add(WorkflowTrigger(tenant_id=tenant.id, workflow_id=str(flow["db_id"]), event_type="after_call",
                config={runtime.BINDING: str(call.environment_id)}))
            await session.commit()
            call_id, tenant_id, env_id, org_id, lead_id = call.id, tenant.id, call.environment_id, tenant.organization_id, lead.id
        invocations = []
        async def executor(choice, prompt, timeout_ms):
            name = "custom" if '"requested"' in prompt else "summary" if '"summary"' in prompt else "sentiment"
            invocations.append(name)
            await asyncio.sleep(0.05)
            value = {"requested": True} if name == "custom" else {"summary": "Tuesday requested", "outcome": "request"} if name == "summary" else {"sentiment": "neutral"}
            return {"text": json.dumps(value), "tokens": 7}
        async def pipeline():
            return await PostCallPipeline(maker, executor=executor).run(call_id, tenant_id=tenant_id,
                environment_id=env_id, organization_id=org_id)
        assert all(result.success for result in await asyncio.gather(pipeline(), pipeline()))
        assert sorted(invocations) == ["custom", "sentiment", "summary"]
        async def handler(job):
            return await runtime.execute(job, maker)
        workers = [JobWorker(maker, handlers={JobType.WORKFLOW_EXECUTION: handler}, job_types=(JobType.WORKFLOW_EXECUTION,)) for _ in range(2)]
        results = await asyncio.gather(*(worker.run_once() for worker in workers))
        claimed = [job for job in results if job is not None]
        assert len(claimed) == 1 and claimed[0].status == "succeeded"
        async with maker() as session:
            assert (await session.get(Lead, lead_id)).status == LeadStatus.QUALIFIED
            for model in (AnalysisResult, QAReview, AutoReviewRun, WorkflowExecution):
                assert await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id)) == 1
            execution = (await session.scalars(select(WorkflowExecution).where(WorkflowExecution.tenant_id == tenant_id))).one()
            assert execution.status == "completed" and execution.output_metadata["effects_committed"] is True and execution.attempt_count == 1
            assert await session.scalar(select(func.count()).select_from(DurableJob).where(DurableJob.tenant_id == tenant_id,
                DurableJob.job_type == JobType.WORKFLOW_EXECUTION)) == 1
            assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == tenant_id,
                AuditLog.event_type == "workflow.post_call_completed")) == 1
            assert await session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.event_type == "call_analyzed")) == 1
        print("PASS: two PostgreSQL pipelines invoke each analysis once and admit one workflow job; two independent workers claim once; pinned workflow actually qualifies lead with one atomic execution/checkpoint/audit. Custom result, QA review/run and analyzed outbox fact coexist for the same call. Model is an injected test executor, not live.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
