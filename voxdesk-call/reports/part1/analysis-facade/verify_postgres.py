"""Verify compatibility admission and observations on migrated PostgreSQL/Redis."""
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
    from app.db.models import Tenant, User, UserRole, Call, CallStatus, CallDirection, Turn, Speaker, DurableJob
    from app.auth.dependencies import TenantContext
    from app.services import post_call_analysis_service as analysis
    from app.api import post_call_analysis_routes as routes
    from app.jobs.types import JobType
    from app.jobs.worker import JobWorker
    settings.redis_url = "redis://127.0.0.1:56379/0"
    engine = create_async_engine("postgresql+asyncpg://user@127.0.0.1:55432/post_call_test")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            tenant = Tenant(name="Extended analysis PG", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7), llm_preset="fast")
            session.add(tenant)
            await session.flush()
            owner = User(tenant_id=tenant.id, email=f"pg-{uuid.uuid4().hex}@example.com", full_name="PG contract owner",
                role=UserRole.OWNER, password_hash="unusable-test-account")
            call = Call(tenant_id=tenant.id, call_sid="CA" + uuid.uuid4().hex, from_number="+15551230000",
                to_number=tenant.twilio_number, status=CallStatus.COMPLETED, direction=CallDirection.INBOUND)
            session.add_all([owner, call])
            await session.flush()
            session.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Please book Tuesday"))
            schema = await analysis.create_schema(session, tenant_id=tenant.id, environment_id=call.environment_id,
                name="Intent", description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
            await session.commit()
            tenant_id, user_id, env_id = tenant.id, owner.id, call.environment_id
            payload = {"schema_id": str(schema.id), "call_ids": [str(call.id)], "idempotency_key": "pg-extended-001"}
        async def admit(compatibility):
            async with maker() as session:
                tenant, owner = await session.get(Tenant, tenant_id), await session.get(User, user_id)
                ctx = TenantContext(user=owner, tenant=tenant, organization_id=tenant.organization_id)
                if compatibility:
                    return await routes.extended_bulk(payload, ctx=ctx, session=session)
                return await routes.create_backfill(routes.BackfillRequest.model_validate(payload), ctx=ctx,
                    session=session, x_idempotency_key=None)
        left, right = await asyncio.gather(admit(True), admit(False))
        assert left["id"] == right["id"] and left["job_ids"] == right["job_ids"]
        async with maker() as session:
            assert await session.scalar(select(func.count()).select_from(DurableJob).where(DurableJob.tenant_id == tenant_id)) == 1
            metrics = await analysis.observed_metrics(session, tenant_id=tenant_id, environment_id=env_id)
            assert metrics["jobs"] == [{"job_type": JobType.ANALYSIS_BACKFILL, "status": "queued", "count": 1}]
        async def executor(*args):
            return {"text": '{"requested":true}', "tokens": 7}
        async def handler(job):
            return await analysis.execute_backfill(job, maker, executor=executor)
        worker = JobWorker(maker, handlers={JobType.ANALYSIS_BACKFILL: handler}, job_types=(JobType.ANALYSIS_BACKFILL,))
        assert (await worker.run_once()).status == "succeeded"
        async with maker() as session:
            stats = await analysis.observed_stats(session, tenant_id=tenant_id, environment_id=env_id)
            assert stats["counts"] == {"schemas": 1, "results": 1, "backfill_manifests": 1}
            assert stats["result_provenance"] == [{"provenance": "provider", "count": 1}]
            metrics = await analysis.observed_metrics(session, tenant_id=tenant_id, environment_id=env_id)
            assert metrics["jobs"] == [{"job_type": JobType.ANALYSIS_BACKFILL, "status": "succeeded", "count": 1}]
            audit = await analysis.audit_page(session, tenant_id=tenant_id, environment_id=env_id, limit=10, offset=0)
            assert audit["total"] == 1 and audit["logs"][0]["event_type"] == "analysis.backfill_admitted"
            assert (await analysis.observed_stats(session, tenant_id=uuid.uuid4(), environment_id=env_id))["counts"]["results"] == 0
        print("PASS: concurrent compatibility/canonical admission shares one PostgreSQL receipt/manifest/job; actual worker acknowledgement, stored result/provenance and scoped audit drive observations; model executor is an injected deterministic test fixture, not live")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
