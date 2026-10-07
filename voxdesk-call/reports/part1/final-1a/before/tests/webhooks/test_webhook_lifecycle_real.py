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
async def test_callback_delivers_signed_bytes_to_local_receiver(client, db, tenant_a):
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
        response = await client.post("/telephony/status", data={
            "CallSid": call.call_sid, "CallStatus": "completed", "CallDuration": "100",
        })
        assert response.status_code == 200
        event = (await db.scalars(select(OutboxEvent).where(
            OutboxEvent.aggregate_id == str(call.id),
            OutboxEvent.event_type == "call_ended",
        ))).one()
        async with httpx.AsyncClient(transport=LocalRelay()) as http:
            async def send(**kwargs):
                return await deliver(client=http, **kwargs)
            assert await deliver_event(db, event, transport=send) == "delivered"
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
        assert await deliver_event(db, event, transport=send) == "dead_letter"
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
