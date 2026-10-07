"""PostgreSQL checkpoint/rollback contracts on the isolated local test DB only."""
import asyncio
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
URL = "postgresql+asyncpg://user@127.0.0.1:55432/post_call_test"


async def main():
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import settings
    from app.db.models import Tenant, Call, CallStatus, CallDirection, Turn, Speaker, DurableJob
    from app.db.telephony_models import PostCallStepRun
    from app.jobs.types import JobType
    from app.outbox.models import OutboxEvent
    from app.telephony.call_state import apply_status, publish_transition
    from app.telephony.post_call import PostCallPipeline

    settings.redis_url = ""
    engine = create_async_engine(URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            tenant = Tenant(name="Post-call PG contract", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7), llm_preset="fast")
            session.add(tenant)
            await session.flush()
            call = Call(tenant_id=tenant.id, call_sid="CA" + uuid.uuid4().hex,
                        from_number="+15551230000", to_number=tenant.twilio_number,
                        status=CallStatus.IN_PROGRESS, direction=CallDirection.INBOUND)
            session.add(call)
            await session.flush()
            session.add(Turn(call_id=call.id, speaker=Speaker.USER, text="I would like an appointment."))
            await session.commit()
            tenant_id, call_id, env_id, org_id = tenant.id, call.id, call.environment_id, tenant.organization_id
            await publish_transition(session, call, apply_status(call, CallStatus.COMPLETED))
            await session.rollback()
            assert await session.scalar(select(func.count()).select_from(DurableJob).where(DurableJob.tenant_id == tenant_id, DurableJob.job_type == JobType.POST_CALL)) == 0
            assert await session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id)) == 0
            call = await session.get(Call, call_id)
            assert call.status == CallStatus.IN_PROGRESS
            await publish_transition(session, call, apply_status(call, CallStatus.COMPLETED))
            await session.commit()
        requests = []

        async def executor(choice, prompt, timeout_ms):
            name = "summary" if '"summary"' in prompt else "sentiment"
            requests.append(name)
            await asyncio.sleep(0.1)
            value = {"summary": "Caller requested an appointment.", "outcome": "appointment_requested"} if name == "summary" else {"sentiment": "neutral"}
            return {"text": json.dumps(value), "tokens": 7}

        async def consumer():
            return await PostCallPipeline(maker, executor=executor).run(
                call_id, tenant_id=tenant_id, environment_id=env_id, organization_id=org_id)

        outcomes = await asyncio.gather(consumer(), consumer())
        assert all(result.success for result in outcomes)
        assert sorted(requests) == ["sentiment", "summary"]
        async with maker() as session:
            rows = list((await session.scalars(select(PostCallStepRun).where(PostCallStepRun.call_id == call_id))).all())
            assert len(rows) == 3 and all(row.attempts == 1 and row.status == "completed" for row in rows)
            stored = await session.get(Call, call_id)
            assert stored.summary == "Caller requested an appointment."
            assert await session.scalar(select(func.count()).select_from(DurableJob).where(DurableJob.tenant_id == tenant_id, DurableJob.job_type == JobType.POST_CALL)) == 1
        print("PASS: migrated PostgreSQL ORM runtime; rollback removes terminal call/event/job; independent concurrent pipeline sessions execute each completed inference once; three durable scoped checkpoints and summary persist")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
