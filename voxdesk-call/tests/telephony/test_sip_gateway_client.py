"""The SIP gateway control client: originating a call, hanging up,
transferring and sending DTMF against the gateway HTTP API.

The gateway API is mocked with respx, so the tests exercise the real HTTP
path: the bearer credential, the idempotency header, the request bodies and
the retries.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.telephony.provider_errors import (
    ProviderAuthenticationError,
    ProviderNotFoundError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from app.telephony.sip.sip_gateway_client import SipGatewayClient

GATEWAY = "http://gateway.test:8080"


def _client(transport=None) -> SipGatewayClient:
    return SipGatewayClient(
        base_url=GATEWAY, token="secret", transport=transport
    )


@pytest.fixture
def no_sleep(monkeypatch):
    """The retry backoff does not wait in a test."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "app.telephony.sip.sip_gateway_client.asyncio.sleep", _no_sleep
    )


@pytest.mark.asyncio
@respx.mock
async def test_originate_sends_the_bearer_token() -> None:
    route = respx.post(f"{GATEWAY}/v1/calls").mock(
        return_value=Response(
            201, json={"id": "call-1", "status": "active", "to": "+1", "from": "+2"}
        )
    )
    client = _client()
    call = await client.originate(
        to_number="+15550001111", from_number="+15550002222", idempotency_key="k-1"
    )
    assert call.id == "call-1"

    request = route.calls[0].request
    assert request.method == "POST"
    assert str(request.url) == f"{GATEWAY}/v1/calls"
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Idempotency-Key"] == "k-1"
    body = json.loads(request.content)
    assert body["to"] == "+15550001111"
    assert body["from"] == "+15550002222"
    assert body["codec"] == "PCMU"


@pytest.mark.asyncio
@respx.mock
async def test_hangup_transfer_dtmf_and_status() -> None:
    hangup = respx.post(f"{GATEWAY}/v1/calls/call-1/hangup").mock(
        return_value=Response(200, json={"status": "ended"})
    )
    transfer = respx.post(f"{GATEWAY}/v1/calls/call-1/transfer").mock(
        return_value=Response(200, json={"status": "transferring"})
    )
    dtmf = respx.post(f"{GATEWAY}/v1/calls/call-1/dtmf").mock(
        return_value=Response(200, json={"status": "sent"})
    )
    status = respx.get(f"{GATEWAY}/v1/calls/call-1").mock(
        return_value=Response(200, json={"id": "call-1", "status": "active"})
    )
    client = _client()

    await client.hangup("call-1")
    assert hangup.called

    await client.transfer("call-1", "+15550003333")
    assert transfer.called
    assert json.loads(transfer.calls[0].request.content) == {
        "destination": "+15550003333",
        "mode": "blind",
    }

    await client.send_dtmf("call-1", "12")
    assert dtmf.called
    assert json.loads(dtmf.calls[0].request.content)["digits"] == "12"

    call = await client.call_status("call-1")
    assert status.called
    assert call.id == "call-1"


@pytest.mark.asyncio
@respx.mock
async def test_an_attended_transfer_carries_the_second_call() -> None:
    transfer = respx.post(f"{GATEWAY}/v1/calls/call-1/transfer").mock(
        return_value=Response(200, json={"status": "transferring"})
    )
    client = _client()
    await client.transfer(
        "call-1", "+15550003333", mode="attended", attended_call_id="call-2"
    )
    body = json.loads(transfer.calls[0].request.content)
    assert body["mode"] == "attended"
    assert body["attended_call_id"] == "call-2"


@pytest.mark.asyncio
async def test_the_client_refuses_to_work_without_a_token() -> None:
    client = SipGatewayClient(base_url=GATEWAY, token="")
    with pytest.raises(ProviderValidationError):
        await client.hangup("call-1")


@pytest.mark.asyncio
async def test_the_client_rejects_non_numeric_dtmf() -> None:
    client = _client()
    with pytest.raises(ProviderValidationError):
        await client.send_dtmf("call-1", "ab")


@pytest.mark.asyncio
@respx.mock
async def test_a_server_error_is_retried(no_sleep) -> None:
    route = respx.post(f"{GATEWAY}/v1/calls").mock(
        side_effect=[
            Response(503, json={"error": "unavailable"}),
            Response(200, json={"id": "call-1", "status": "active"}),
        ]
    )
    client = _client()
    call = await client.originate(to_number="+1", from_number="+2")
    assert call.id == "call-1"
    assert len(route.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_a_client_error_is_not_retried(no_sleep) -> None:
    route = respx.post(f"{GATEWAY}/v1/calls").mock(
        return_value=Response(422, json={"error": "invalid"})
    )
    client = _client()
    with pytest.raises(ProviderValidationError):
        await client.originate(to_number="+1", from_number="+2")
    assert len(route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_rejected_credential_is_reported_as_an_authentication_error(
    no_sleep,
) -> None:
    route = respx.post(f"{GATEWAY}/v1/calls").mock(
        return_value=Response(401, json={"error": "unauthorized"})
    )
    client = _client()
    with pytest.raises(ProviderAuthenticationError):
        await client.originate(to_number="+1", from_number="+2")
    assert len(route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_an_unknown_call_is_reported_as_not_found(no_sleep) -> None:
    route = respx.get(f"{GATEWAY}/v1/calls/call-that-does-not-exist").mock(
        return_value=Response(404, json={"error": "not_found"})
    )
    client = _client()
    with pytest.raises(ProviderNotFoundError):
        await client.call_status("call-that-does-not-exist")
    assert len(route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_timeout_is_typed_and_retried(no_sleep) -> None:
    import httpx

    route = respx.post(f"{GATEWAY}/v1/calls/call-1/hangup").mock(
        side_effect=httpx.ReadTimeout("the gateway did not answer")
    )
    client = _client()
    with pytest.raises(ProviderTimeoutError):
        await client.hangup("call-1")
    assert len(route.calls) == 3
