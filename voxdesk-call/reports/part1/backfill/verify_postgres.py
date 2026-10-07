"""Independent PostgreSQL sessions and Redis, on isolated localhost ports only."""
import asyncio
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


async def main():
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import settings
    from app.auth.dependencies import TenantContext
    from app.db.models import Tenant, User, UserRole, Call, CallStatus, CallDirection, Turn, Speaker, DurableJob, RequestIdempotencyReceipt
    from app.db.enterprise_models import AnalysisSchema, AnalysisResult, BackfillJob
    from app.services import post_call_analysis_service as analysis
    from app.jobs.types import JobType
    from app.jobs.worker import JobWorker
    from app.api import post_call_analysis_routes as routes

    settings.redis_url = "redis://127.0.0.1:56379/0"
    engine = create_async_engine("postgresql+asyncpg://user@127.0.0.1:55432/post_call_test")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            tenant = Tenant(name="Backfill PG contract", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7), llm_preset="fast")
            session.add(tenant)
            await session.flush()
            user = User(tenant_id=tenant.id, email=f"pg-{uuid.uuid4().hex}@example.com", full_name="PG test owner",
                        role=UserRole.OWNER, password_hash="unusable-test-account")
            call = Call(tenant_id=tenant.id, call_sid="CA" + uuid.uuid4().hex, from_number="+15551230000",
                        to_number=tenant.twilio_number, status=CallStatus.COMPLETED, direction=CallDirection.INBOUND)
            session.add_all([user, call])
            await session.flush()
            session.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Please book Tuesday."))
            schema = await analysis.create_schema(session, tenant_id=tenant.id, environment_id=call.environment_id,
                name="PG intent", description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
            await session.commit()
            tenant_id, user_id, call_id, schema_id = tenant.id, user.id, call.id, schema.id
        payload = routes.BackfillRequest(schema_id=schema_id, call_ids=[call_id], idempotency_key="pg-admission-001")

        async def admission(request):
            async with maker() as session:
                tenant, user = await session.get(Tenant, tenant_id), await session.get(User, user_id)
                ctx = TenantContext(user=user, tenant=tenant, organization_id=tenant.organization_id)
                return await routes.create_backfill(request, ctx=ctx, session=session, x_idempotency_key=None)

        # A failed audit cannot commit either jobs, the manifest or its receipt.
        original_audit = routes.record_event
        async def reject_audit(*args, **kwargs):
            raise RuntimeError("test audit failure")
        routes.record_event = reject_audit
        try:
            try:
                await admission(payload)
            except RuntimeError as exc:
                assert str(exc) == "test audit failure"
            else:
                raise AssertionError("Audit failure must propagate")
        finally:
            routes.record_event = original_audit
        async with maker() as session:
            for model in (BackfillJob, DurableJob, RequestIdempotencyReceipt):
                assert await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id)) == 0

        left, right = await asyncio.gather(admission(payload), admission(payload))
        assert left["id"] == right["id"] and left["job_ids"] == right["job_ids"]
        async with maker() as session:
            schema = await session.get(AnalysisSchema, schema_id)
            await analysis.update_schema(session, schema, {"fields": [{"name": "changed", "type": "text"}]})
            await session.commit()
        invoked = []
        async def executor(choice, prompt, timeout_ms):
            assert '"requested"' in prompt and '"changed"' not in prompt
            invoked.append(choice.model)
            await asyncio.sleep(0.1)
            return {"text": '{"requested":true}', "tokens": 9}
        async def handler(job):
            return await analysis.execute_backfill(job, maker, executor=executor)
        workers = [JobWorker(maker, handlers={JobType.ANALYSIS_BACKFILL: handler},
                             job_types=(JobType.ANALYSIS_BACKFILL,)) for _ in range(2)]
        outcomes = await asyncio.gather(*(worker.run_once() for worker in workers))
        assert len([job for job in outcomes if job is not None]) == 1
        assert next(job for job in outcomes if job is not None).status == "succeeded"
        assert len(invoked) == 1
        async with maker() as session:
            result = (await session.scalars(select(AnalysisResult).where(AnalysisResult.call_id == call_id))).one()
            assert result.schema_version == 1 and result.result == {"requested": True}
            batch = await session.get(BackfillJob, uuid.UUID(left["id"]))
            status = await analysis.backfill_status(session, batch)
            assert status["status"] == "completed" and status["processed_calls"] == 1
        print("PASS: PostgreSQL audit rollback removes manifest/jobs/receipt; concurrent admission creates one batch/job; two canonical workers claim once; pinned version survives schema edit; real persisted result drives completed progress")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
