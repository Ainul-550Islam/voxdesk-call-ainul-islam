"""Real independent PostgreSQL sessions; isolated local QA integration evidence."""
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
    from app.auth.dependencies import TenantContext
    from app.db.models import Tenant, User, UserRole, Call, CallStatus, CallDirection, Turn, Speaker, DurableJob, RequestIdempotencyReceipt
    from app.qa.models import SamplingRule, SampleSelection, QAReview, QAReviewItem, AutoReviewRun
    from app.qa import rubrics, auto_review
    from app.api import qa_routes
    from app.audit import service as audit
    from app.jobs.types import JobType
    from app.jobs.worker import JobWorker
    from app.telephony.post_call import PostCallPipeline

    settings.redis_url = "redis://127.0.0.1:56379/0"
    engine = create_async_engine("postgresql+asyncpg://user@127.0.0.1:55432/post_call_test")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            tenant = Tenant(name="QA PG contract", twilio_number="+1555" + str(uuid.uuid4().int % 10**7).zfill(7), llm_preset="fast")
            session.add(tenant)
            await session.flush()
            user = User(tenant_id=tenant.id, email=f"qa-pg-{uuid.uuid4().hex}@example.com", full_name="PG test owner",
                        role=UserRole.OWNER, password_hash="unusable-test-account")
            call = Call(tenant_id=tenant.id, call_sid="CA" + uuid.uuid4().hex, from_number="+15551230000",
                        to_number=tenant.twilio_number, status=CallStatus.COMPLETED, direction=CallDirection.INBOUND)
            session.add_all([user, call])
            await session.flush()
            session.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Please book Tuesday."))
            card = await rubrics.create_scorecard(session, tenant_id=tenant.id, environment_id=call.environment_id,
                name="PG rubric", sections=[{"name": "Opening", "items": [{"name": "Greeting", "min_score": 0, "max_score": 100}]}])
            await session.commit()
            tenant_id, user_id, call_id, card_id, env_id, org_id = tenant.id, user.id, call.id, card.id, call.environment_id, tenant.organization_id
        payload = qa_routes.AutomaticSamplingBody(name="PG automatic", scorecard_id=card_id, percent=100)

        async def admission():
            async with maker() as session:
                tenant, user = await session.get(Tenant, tenant_id), await session.get(User, user_id)
                ctx = TenantContext(user=user, tenant=tenant, organization_id=org_id)
                return await qa_routes.create_automatic_sampling(payload, ctx=ctx, session=session, idempotency_key="qa-pg-admission-001")

        original = audit.record_event
        async def fail(*args, **kwargs):
            raise RuntimeError("injected audit failure")
        audit.record_event = fail
        try:
            try:
                await admission()
            except RuntimeError as exc:
                assert str(exc) == "injected audit failure"
            else:
                raise AssertionError("Audit failure must propagate")
        finally:
            audit.record_event = original
        async with maker() as session:
            for model in (SamplingRule, RequestIdempotencyReceipt):
                assert await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id)) == 0
        left, right = await asyncio.gather(admission(), admission())
        assert left["id"] == right["id"] and left["scorecard_id"] == str(card_id)
        builtins = []
        async def analysis_executor(choice, prompt, timeout_ms):
            key = "summary" if '"summary"' in prompt else "sentiment"
            builtins.append(key)
            await asyncio.sleep(0.1)
            value = {"summary": "Caller requested Tuesday.", "outcome": "booking_requested"} if key == "summary" else {"sentiment": "positive"}
            return {"text": json.dumps(value), "tokens": 8}
        async def pipeline():
            return await PostCallPipeline(maker, executor=analysis_executor).run(call_id, tenant_id=tenant_id, environment_id=env_id, organization_id=org_id)
        assert all(value.success for value in await asyncio.gather(pipeline(), pipeline()))
        assert sorted(builtins) == ["sentiment", "summary"]
        async with maker() as session:
            for model in (SamplingRule, SampleSelection, QAReview, AutoReviewRun):
                assert await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant_id)) == 1
            run = (await session.scalars(select(AutoReviewRun).where(AutoReviewRun.tenant_id == tenant_id))).one()
            run_id, review_id, job_id = run.id, run.review_id, run.job_id
            item = (await session.scalars(select(QAReviewItem).where(QAReviewItem.review_id == review_id))).one()
            item.human_score, item.accepted_source = 20, "human"
            await session.commit()
        invocations = []
        async def executor(choice, prompt, timeout_ms):
            data = json.loads(prompt)
            invocations.append(data)
            assert data["rubric"]["scorecard_id"] == str(card_id)
            assert data["turns"][0]["text"] == "Please book Tuesday."
            await asyncio.sleep(0.1)
            return {"text": json.dumps({"items": [{"item_id": data["rubric"]["items"][0]["id"], "score": 80,
                "turn_ids": [data["turns"][0]["turn_id"]]}]}), "tokens": 9}
        workers = [JobWorker(maker, handlers={JobType.QA_AUTO_REVIEW: auto_review.make_auto_review_handler(maker, executor=executor)},
                             job_types=(JobType.QA_AUTO_REVIEW,)) for _ in range(2)]
        outcomes = await asyncio.gather(*(worker.run_once() for worker in workers))
        assert len([job for job in outcomes if job is not None]) == 1
        assert next(job for job in outcomes if job is not None).status == "succeeded"
        assert len(invocations) == 1
        async with maker() as session:
            run = await session.get(AutoReviewRun, run_id)
            review = await session.get(QAReview, review_id)
            item = (await session.scalars(select(QAReviewItem).where(QAReviewItem.review_id == review_id))).one()
            assert run.status == "completed" and run.token_count == 9 and run.rubric_snapshot and run.transcript_snapshot
            assert item.human_score == 20 and item.accepted_source == "human" and item.ai_score == 80
            assert review.status == "created" and review.overall_score is None
            assert (await session.get(DurableJob, job_id)).status == "succeeded"
        (ROOT / "reports/part1/qa/state.json").write_text(json.dumps({"tenant_id": str(tenant_id), "rule_id": left["id"], "run_id": str(run_id), "job_id": str(job_id)}, indent=2) + "\n")
        print("PASS: PostgreSQL audit rollback; concurrent configuration receipt admission; two concurrent pipelines admit exactly one scoped selection/review/run/job; two canonical workers claim once; measured governed QA suggestion persists without replacing human score or finalizing review")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
