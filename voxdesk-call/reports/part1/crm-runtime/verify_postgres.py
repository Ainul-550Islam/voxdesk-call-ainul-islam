"""Scoped CRM concurrency and acknowledgement on an isolated migrated PostgreSQL DB."""
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
    from app.db.models import Tenant, Call, CallStatus, CallDirection, Turn, Speaker, CrmIntegration, CrmProviderType, CrmSync, CrmSyncStatus, CrmEvent
    from app.telephony.post_call import PostCallPipeline
    from app.integrations.crm import service
    from app.integrations.crm.models import CrmResult
    from app.integrations.crm.base import Capability
    settings.redis_url = "redis://127.0.0.1:56379/0"
    engine = create_async_engine("postgresql+asyncpg://user@127.0.0.1:55432/post_call_test")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    original = service.build_provider
    seen = []
    class Provider:
        name = "recording-fake"
        def supports(self, capability):
            return capability in {Capability.UPSERT_CONTACT, Capability.CREATE_NOTE}
        async def upsert_contact(self, contact):
            seen.append("contact")
            await asyncio.sleep(0.1)
            return CrmResult("pg-test-contact")
        async def create_note(self, contact_id, activity):
            assert contact_id == "pg-test-contact" and json.loads(activity.body)["sentiment"] == {"sentiment": "neutral"}
            seen.append("note")
            return CrmResult("pg-test-note")
    try:
        async with maker() as session:
            tenant = Tenant(name="CRM PG contract", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7), llm_preset="fast")
            session.add(tenant)
            await session.flush()
            call = Call(tenant_id=tenant.id, call_sid="CA" + uuid.uuid4().hex, from_number="+15551230000",
                to_number=tenant.twilio_number, status=CallStatus.COMPLETED, direction=CallDirection.INBOUND)
            session.add(call)
            await session.flush()
            session.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Please call Tuesday"))
            session.add(CrmIntegration(tenant_id=tenant.id, provider=CrmProviderType.WEBHOOK, is_enabled=True,
                config={"post_call_analysis_environment_id": str(call.environment_id)}))
            await session.commit()
            call_id, tenant_id, env_id, org_id = call.id, tenant.id, call.environment_id, tenant.organization_id
        async def executor(choice, prompt, timeout_ms):
            value = {"summary": "Caller requested Tuesday", "outcome": "callback"} if '"summary"' in prompt else {"sentiment": "neutral"}
            return {"text": json.dumps(value), "tokens": 7}
        async def pipeline():
            return await PostCallPipeline(maker, executor=executor).run(call_id, tenant_id=tenant_id,
                environment_id=env_id, organization_id=org_id)
        assert all(result.success for result in await asyncio.gather(pipeline(), pipeline()))
        async with maker() as session:
            assert await session.scalar(select(func.count()).select_from(CrmEvent).where(CrmEvent.tenant_id == tenant_id)) == 1
            sync = (await session.scalars(select(CrmSync).where(CrmSync.tenant_id == tenant_id))).one()
            sync_id = sync.id
        service.build_provider = lambda *args: Provider()
        async def worker():
            async with maker() as session:
                sync = await session.get(CrmSync, sync_id)
                return await service.process_sync(session, sync)
        outcomes = await asyncio.gather(worker(), worker())
        assert sum(row.status == CrmSyncStatus.SYNCED for row in outcomes) == 1
        assert seen == ["contact", "note"]
        async with maker() as session:
            sync = await session.get(CrmSync, sync_id)
            assert sync.status == CrmSyncStatus.SYNCED and sync.external_id == "pg-test-note" and sync.attempt_count == 1
        print("PASS: independent PostgreSQL pipelines admit one CRM event/sync; independent CRM workers claim once; recording-fake contact/note each invoked once; note acknowledgement drives persisted success")
    finally:
        service.build_provider = original
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
