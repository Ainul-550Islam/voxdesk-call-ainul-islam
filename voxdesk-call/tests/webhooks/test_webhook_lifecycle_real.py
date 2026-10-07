"""Real local HTTP receipt via a test-only network relay, not a guard bypass.

Production validates the configured public HTTPS URL. This test transport then
relays the signed bytes to an isolated loopback HTTP receiver. This proves wire
receipt/signing, not public TLS, DNS-rebinding defense or hosted deployment.
"""
import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import CallStatus, WebhookDelivery
from app.outbox.dispatcher import deliver_event
from app.outbox.models import OutboxEvent
from app.webhooks.delivery import deliver
from app.webhooks.repository import create_subscription
from app.webhooks.signing import verify
from tests.conftest import make_call


@pytest.mark.asyncio
async def test_callback_delivers_signed_bytes_to_local_receiver(client, db, tenant_a, sessionmaker_, monkeypatch):
    import asyncio

    received = []

    async def receive(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        headers = {}
        for line in head.decode().split("\r\n")[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()
        body = await reader.readexactly(int(headers["content-length"]))
        received.append((headers, body))
        writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class LocalRelay(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            async with httpx.AsyncHTTPTransport() as transport:
                local = httpx.Request(
                    request.method, f"http://127.0.0.1:{port}/hooks",
                    headers=request.headers, content=await request.aread(),
                )
                response = await transport.handle_async_request(local)
                await response.aread()
                return response

    secret = "test-only-shared-secret"
    await create_subscription(db, tenant_id=tenant_a.id,
                              endpoint="https://receiver.example.com/hooks",
                              secret=secret, event_types=["call_ended"])
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    try:
        from app.core.config import settings
        from twilio.request_validator import RequestValidator
        monkeypatch.setattr(settings, "twilio_skip_webhook_verify", False)
        monkeypatch.setattr(settings, "twilio_auth_token", "local-contract-token")
        form = {"CallSid": call.call_sid, "CallStatus": "completed", "CallDuration": "100"}
        signature = RequestValidator(settings.twilio_auth_token).compute_signature("http://test/telephony/status", form)
        response = await client.post("/telephony/status", data=form, headers={"X-Twilio-Signature": signature})
        assert response.status_code == 200
        event = (await db.scalars(select(OutboxEvent).where(
            OutboxEvent.aggregate_id == str(call.id),
            OutboxEvent.event_type == "call_ended",
        ))).one()
        async with httpx.AsyncClient(transport=LocalRelay()) as http:
            async def send(**kwargs):
                return await deliver(client=http, **kwargs)
            from app.outbox import dispatcher
            from app.jobs.worker import JobWorker
            from app.jobs.types import JobType
            monkeypatch.setattr(dispatcher, "deliver", send)
            scheduled = await dispatcher.dispatch_due(db)
            assert scheduled["scheduled"] == 1
            await db.commit()
            worker = JobWorker(sessionmaker_, handlers={JobType.OUTBOX_DELIVERY: dispatcher.make_outbox_handler(sessionmaker_)},
                               job_types=(JobType.OUTBOX_DELIVERY,))
            job = await worker.run_once()
            assert job is not None and job.status == "succeeded"
            await db.refresh(event)
            assert event.status == "delivered"
            assert await worker.run_once() is None
            assert await deliver_event(db, event, transport=send) == "delivered"
        assert len(received) == 1
        headers, body = received[0]
        assert verify(secret, body, headers["x-voxdesk-signature"])
        assert headers["x-voxdesk-timestamp"] == headers["x-voxdesk-signature"].split(",")[0][2:]
        assert headers["x-voxdesk-event"] == str(event.id)
        assert json.loads(body)["payload"]["call_id"] == str(call.id)
        delivery = (await db.scalars(select(WebhookDelivery))).one()
        assert delivery.status == "succeeded"
        assert delivery.attempt == 1
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://127.0.0.1", "https://169.254.169.254",
                                     "https://metadata.google.internal", "http://example.com"])
async def test_ssrf_rejected_before_transport(url):
    def forbidden(request):
        pytest.fail("Unsafe destination reached transport")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        result = await deliver(url=url, body=b"{}", secret="test", event_id="test", client=client)
    assert result.kind == "permanent"
    assert result.category == "invalid_endpoint"


@pytest.mark.asyncio
async def test_http_failures_retry_then_exhaust_to_dlq(db, tenant_a):
    from app.webhooks.call_event_bridge import publish_call_event

    call = await make_call(db, tenant_a)
    await create_subscription(db, tenant_id=tenant_a.id,
                              endpoint="https://receiver.example.com/hooks",
                              secret="test", event_types=["call_ended"])
    event, _ = await publish_call_event(db, call, "call_ended")
    event.max_attempts = 2
    seen = []

    def unavailable(request):
        assert str(request.url) == "https://receiver.example.com/hooks"
        assert verify("test", request.content, request.headers["X-VoxDesk-Signature"])
        seen.append(request)
        return httpx.Response(503, text="Unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as http:
        async def send(**kwargs):
            return await deliver(client=http, **kwargs)
        # Model the round number maintained by the existing dispatcher. This
        # exercises HTTP classification/budget, not job claim concurrency.
        event.attempt_count = 1
        assert await deliver_event(db, event, transport=send) == "pending"
        row = (await db.scalars(select(WebhookDelivery))).one()
        assert row.status == "queued"
        assert row.next_attempt_at is not None
        event.attempt_count = 2
        assert await deliver_event(db, event, now=event.available_at, transport=send) == "dead_letter"
    assert len(seen) == 2
    assert event.last_error_category == "attempts_exhausted"
    assert row.status != "succeeded"
    assert row.attempt == 2


@pytest.mark.asyncio
async def test_custom_headers_timeout_and_bounded_response_are_real():
    seen = []

    def receive(request):
        seen.append(request)
        assert request.headers["X-Customer-Trace"] == "tenant-trace"
        assert request.extensions["timeout"]["read"] == 1.5
        assert verify("test", request.content, request.headers["X-VoxDesk-Signature"])
        return httpx.Response(202, text="a" * 5000)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
        result = await deliver(url="https://example.com/hooks", body=b"{}", secret="test",
                               event_id="event-1", timeout_seconds=1.5,
                               headers={"X-Customer-Trace": "tenant-trace"}, client=http)
    assert len(seen) == 1
    assert result.kind == "success" and result.status == 202
    assert result.latency_ms >= 0
    assert result.response_snippet == "a" * 4096


@pytest.mark.asyncio
async def test_injected_client_cannot_enable_redirect_following():
    seen = []

    def redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://169.254.169.254"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as http:
        result = await deliver(url="https://example.com/hooks", body=b"{}", secret="test",
                               event_id="event-1", client=http)
    assert len(seen) == 1
    assert result.kind == "permanent" and result.category == "redirect_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"Host": "internal"}, {"X-VoxDesk-Signature": "forged"},
    {"Content-Length": "0"}, {"X-Test": "value\r\nHost: internal"},
])
async def test_protected_or_injected_headers_never_reach_transport(headers):
    def forbidden(request):
        pytest.fail("Invalid headers reached HTTP transport")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        result = await deliver(url="https://example.com/hooks", body=b"{}", secret="test",
                               event_id="event-1", headers=headers, client=http)
    assert result.kind == "permanent" and result.category == "invalid_headers"


@pytest.mark.asyncio
async def test_informational_response_is_not_delivery_success():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(101),
    )) as http:
        result = await deliver(url="https://example.com/hooks", body=b"{}", secret="test",
                               event_id="event-1", client=http)
    assert result.kind == "permanent" and result.category == "unexpected_status"


@pytest.mark.asyncio
async def test_api_send_failure_redrive_and_real_worker_use_canonical_settings(client, db, owner_a,
                                                                            sessionmaker_, webhook_configuration,
                                                                            monkeypatch):
    import uuid
    from tests.conftest import auth_headers
    from app.outbox import dispatcher
    from app.jobs.types import JobType
    from app.jobs.worker import JobWorker
    from app.db.models import WebhookSubscription, AuditLog

    auth = await auth_headers(client, owner_a)
    secret = "initial-client-signing-secret"
    created = await client.post("/api/webhooks", json={
        "url": "https://8.8.8.8/hooks", "secret": secret,
        "headers": {"X-Client-Key": "client-header-secret"}, "timeout_seconds": 2,
        "retry_policy": {"max_attempts": 1}, "events": ["call_ended"],
    }, headers=auth)
    assert created.status_code == 201, created.text
    subscription_id = created.json()["id"]
    received = []
    expected_secret = [secret]

    def receiver(request):
        assert request.headers["X-Client-Key"] == "client-header-secret"
        assert request.extensions["timeout"]["read"] == 2
        assert verify(expected_secret[0], request.content, request.headers["X-VoxDesk-Signature"])
        assert json.loads(request.content)["event_type"] == "webhook.test"
        received.append(request)
        return httpx.Response(503 if len(received) == 1 else 204, text="diagnostic body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as http:
        async def send(**kwargs):
            return await deliver(client=http, **kwargs)
        monkeypatch.setattr(dispatcher, "deliver", send)
        keyed = {**auth, "Idempotency-Key": str(uuid.uuid4())}
        tested = await client.post(f"/api/webhooks/{subscription_id}/test", json={}, headers=keyed)
        assert tested.status_code == 200, tested.text
        result = tested.json()
        assert result["http_status"] == 503 and result["success"] is False
        assert result["status"] == "dead_letter" and result["duration_ms"] >= 0
        assert result["response_body"] == "diagnostic body"
        repeated = await client.post(f"/api/webhooks/{subscription_id}/test", json={}, headers=keyed)
        assert repeated.status_code == 200
        assert len(received) == 1
        dlq = await client.get(f"/api/webhooks/{subscription_id}/dlq", headers=auth)
        assert dlq.json()["total"] == 1
        rotated = await client.post(f"/api/webhooks/{subscription_id}/rotate-secret", json={}, headers=auth)
        assert rotated.status_code == 200
        expected_secret[0] = rotated.json()["secret"]
        redriven = await client.post(f"/api/webhooks/{subscription_id}/deliveries/{result['id']}/retry", headers=auth)
        assert redriven.status_code == 200, redriven.text
        assert redriven.json()["status"] == "queued"
        assert len(received) == 1  # redrive enqueues; it does not pretend HTTP success
        await db.commit()
        queued = await dispatcher.dispatch_due(db)
        assert queued["scheduled"] == 1
        await db.commit()
        worker = JobWorker(sessionmaker_, handlers={JobType.OUTBOX_DELIVERY: dispatcher.make_outbox_handler(sessionmaker_)},
                           job_types=(JobType.OUTBOX_DELIVERY,))
        job = await worker.run_once()
        assert job.status == "succeeded"
    assert len(received) == 2
    assert received[0].content == received[1].content
    row = await db.get(WebhookDelivery, uuid.UUID(result["id"]))
    assert row.status == "succeeded" and row.attempt == 2 and row.replay_count == 1
    assert "diagnostic body" not in row.response_envelope
    subscription = await db.get(WebhookSubscription, uuid.UUID(subscription_id))
    assert "client-header-secret" not in subscription.headers_envelope
    events = (await db.scalars(select(AuditLog.event_type).where(AuditLog.tenant_id == owner_a.tenant_id))).all()
    assert {"webhook.test_requested", "webhook.test_observed", "webhook.replayed"} <= set(events)
    rejected = await client.post(f"/api/webhooks/{subscription_id}/deliveries/{result['id']}/retry", headers=auth)
    assert rejected.status_code == 409


@pytest.mark.asyncio
async def test_dns_private_answer_fails_closed_before_real_http(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
    ])
    result = await deliver(url="https://attacker.example.com/hook", body=b"{}", secret="test", event_id="dns-case")
    assert result.kind == "permanent" and result.status == 0
    assert result.category == "invalid_endpoint"


@pytest.mark.live
@pytest.mark.asyncio
async def test_opt_in_live_signed_receiver():
    """Opt-in receiver must verify HMAC and echo the verified event identity."""
    import os
    import uuid
    if os.environ.get("VOXDESK_WEBHOOK_LIVE") != "1":
        pytest.skip("Set VOXDESK_WEBHOOK_LIVE=1 and configure the signed echo receiver")
    url = os.environ.get("VOXDESK_WEBHOOK_LIVE_URL")
    secret = os.environ.get("VOXDESK_WEBHOOK_LIVE_SECRET")
    assert url and secret, "Live URL and signing secret are required when live is enabled"
    event_id = str(uuid.uuid4())
    result = await deliver(url=url, body=json.dumps({"event_id": event_id, "event_type": "webhook.test"}).encode(),
                           secret=secret, event_id=event_id)
    assert result.kind == "success" and 200 <= result.status < 300
    echo = json.loads(result.response_snippet)
    assert echo["received_event_id"] == event_id
    assert echo["signature_valid"] is True
