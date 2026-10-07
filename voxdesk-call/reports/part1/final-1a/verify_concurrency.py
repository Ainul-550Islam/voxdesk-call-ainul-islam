"""PostgreSQL transaction/locking contracts on the isolated migration DB."""
import asyncio
import base64
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ["IDENTITY_ENCRYPTION_KEYS"] = "pg-contract:" + base64.urlsafe_b64encode(os.urandom(32)).decode()



async def main():
    import asyncpg
    import httpx
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.db.models import WebhookDelivery, WebhookSubscription
    from app.outbox.models import OutboxEvent
    from app.outbox.publisher import publish
    from app.outbox.dispatcher import deliver_event
    from app.webhooks.delivery import deliver
    from app.webhooks.repository import create_subscription, rotate_secret
    from app.webhooks.signing import verify

    database = await asyncpg.connect("postgresql://user@127.0.0.1:55432/webhook_test")
    organization, tenant = uuid.uuid4(), uuid.uuid4()
    await database.execute("INSERT INTO organizations(id,name,slug,status,created_at,updated_at) VALUES($1,$2,$3,'active',now(),now())",
                           organization, "Worker contract", str(organization))
    await database.execute("INSERT INTO tenants(id,organization_id,name,twilio_number) VALUES($1,$2,$3,$4)",
                           tenant, organization, "Worker contract", "+1555" + str(uuid.uuid4().int % 10**7).zfill(7))
    await database.close()
    engine = create_async_engine("postgresql+asyncpg://user@127.0.0.1:55432/webhook_test")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            sub = await create_subscription(session, tenant_id=tenant, endpoint="https://8.8.8.8/hooks",
                                             secret="concurrency-contract-secret", event_types=["webhook.test"])
            event, _ = await publish(session, tenant_id=tenant, event_type="webhook.test",
                                     payload={"test": True}, idempotency_key=str(uuid.uuid4()))
            event_id, subscription_id = event.id, sub.id
            await session.commit()
        arrived = []
        async def receive(request):
            assert verify("concurrency-contract-secret", request.content, request.headers["X-VoxDesk-Signature"])
            arrived.append(request)
            await asyncio.sleep(0.1)  # overlap independent DB sessions while one owns the event lock
            return httpx.Response(204)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            async def send(**kwargs):
                return await deliver(client=http, **kwargs)
            async def consumer():
                async with maker() as session:
                    row = await session.get(OutboxEvent, event_id)
                    outcome = await deliver_event(session, row, transport=send)
                    await session.commit()
                    return outcome
            outcomes = await asyncio.gather(consumer(), consumer())
        assert outcomes == ["delivered", "delivered"] and len(arrived) == 1
        async with maker() as session:
            assert await session.scalar(select(func.count()).select_from(WebhookDelivery).where(
                WebhookDelivery.event_id == str(event_id), WebhookDelivery.tenant_id == tenant)) == 1
            row = await session.scalar(select(WebhookDelivery).where(WebhookDelivery.event_id == str(event_id)))
            assert row.attempt == 1 and row.status == "succeeded"
        async def rotation(secret):
            async with maker() as session:
                # Deliberately pre-load before lock acquisition to test refresh.
                await session.get(WebhookSubscription, subscription_id)
                await rotate_secret(session, tenant_id=tenant, environment_id=None,
                                    subscription_id=subscription_id, secret=secret)
                await session.commit()
        await asyncio.gather(rotation("concurrent-rotation-one"), rotation("concurrent-rotation-two"))
        async with maker() as session:
            assert (await session.get(WebhookSubscription, subscription_id)).secret_version == 3
            rolled_back_id = uuid.uuid4()
            await publish(session, tenant_id=tenant, event_type="webhook.test", payload={"test": True},
                          idempotency_key=str(rolled_back_id))
            await session.rollback()
            assert await session.scalar(select(OutboxEvent.id).where(
                OutboxEvent.tenant_id == tenant, OutboxEvent.idempotency_key == str(rolled_back_id))) is None
        print("PASS: two independent PostgreSQL sessions serialize delivery; one HTTP request/ledger; concurrent rotations reach version 3; publication rollback leaves no event")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
