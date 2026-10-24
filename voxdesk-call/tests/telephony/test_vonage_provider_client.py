"""The Vonage Voice API client: call creation, transfer, hangup and DTMF.

The Voice API is mocked with respx, so the tests exercise the real HTTP
path: the RS256 application JWT, the idempotency header, the recorded
request bodies and the retries.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.core.config import settings
from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from app.telephony.providers.vonage import VonageAdapter

VOICE_API = "https://api.nexmo.com"


@pytest.fixture
def vonage_credentials(monkeypatch):
    """A complete Vonage credential set, with a real RS256 key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    monkeypatch.setattr(settings, "vonage_api_key", "key-1", raising=False)
    monkeypatch.setattr(settings, "vonage_api_secret", "secret-1", raising=False)
    monkeypatch.setattr(settings, "vonage_application_id", "app-1", raising=False)
    monkeypatch.setattr(settings, "vonage_private_key", pem, raising=False)
    monkeypatch.setattr(settings, "vonage_signature_secret", "sig-1", raising=False)
    return pem


@pytest.fixture
def no_sleep(monkeypatch):
    """The retry backoff does not wait in a test."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(VonageAdapter, "_sleep", staticmethod(_no_sleep))


@pytest.mark.asyncio
@respx.mock
async def test_create_outbound_uses_the_voice_api(vonage_credentials) -> None:
    route = respx.post(f"{VOICE_API}/v1/calls").mock(
        return_value=Response(201, json={"uuid": "abc", "status": "started"})
    )
    adapter = VonageAdapter()
    result = await adapter.create_outbound(
        to_number="+15550001111",
        from_number="+15550002222",
        answer_url="https://example.test/answer",
        idempotency_key="key-1",
    )
    assert result.external_id == "abc"

    request = route.calls[0].request
    assert request.method == "POST"
    assert str(request.url) == f"{VOICE_API}/v1/calls"
    assert request.headers["Authorization"].startswith("Bearer ")
    assert request.headers["Idempotency-Key"] == "key-1"
    body = json.loads(request.content)
    assert body["to"][0]["number"] == "+15550001111"
    assert body["from"]["number"] == "+15550002222"


@pytest.mark.asyncio
@respx.mock
async def test_create_outbound_replays_an_idempotent_call(vonage_credentials) -> None:
    route = respx.post(f"{VOICE_API}/v1/calls").mock(
        return_value=Response(201, json={"uuid": "abc", "status": "started"})
    )
    adapter = VonageAdapter()
    first = await adapter.create_outbound(
        to_number="+15550001111",
        from_number="+15550002222",
        answer_url="https://example.test/answer",
        idempotency_key="key-1",
    )
    second = await adapter.create_outbound(
        to_number="+15550001111",
        from_number="+15550002222",
        answer_url="https://example.test/answer",
        idempotency_key="key-1",
    )
    assert first.external_id == second.external_id
    # The second call is served from the idempotency cache.
    assert len(route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_a_server_error_is_retried(vonage_credentials, no_sleep) -> None:
    route = respx.post(f"{VOICE_API}/v1/calls").mock(
        side_effect=[
            Response(503, json={"title": "unavailable"}),
            Response(201, json={"uuid": "abc", "status": "started"}),
        ]
    )
    adapter = VonageAdapter()
    result = await adapter.create_outbound(
        to_number="+15550001111",
        from_number="+15550002222",
        answer_url="https://example.test/answer",
    )
    assert result.external_id == "abc"
    assert len(route.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_a_client_error_is_not_retried(vonage_credentials, no_sleep) -> None:
    route = respx.post(f"{VOICE_API}/v1/calls").mock(
        return_value=Response(422, json={"title": "invalid"})
    )
    adapter = VonageAdapter()
    with pytest.raises(ProviderValidationError):
        await adapter.create_outbound(
            to_number="+15550001111",
            from_number="+15550002222",
            answer_url="https://example.test/answer",
        )
    assert len(route.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_hangup_transfer_and_dtmf(vonage_credentials) -> None:
    hangup = respx.delete(f"{VOICE_API}/v1/calls/abc").mock(
        return_value=Response(204)
    )
    transfer = respx.put(f"{VOICE_API}/v1/calls/abc").mock(
        return_value=Response(200, json={"uuid": "abc", "status": "started"})
    )
    dtmf = respx.put(f"{VOICE_API}/v1/calls/abc/dtmf").mock(
        return_value=Response(200, json={"message": "DTMF sent"})
    )
    talk = respx.put(f"{VOICE_API}/v1/calls/abc/talk").mock(
        return_value=Response(200, json={"message": "Talk started"})
    )
    stream = respx.put(f"{VOICE_API}/v1/calls/abc/stream").mock(
        return_value=Response(200, json={"message": "Stream started"})
    )
    adapter = VonageAdapter()

    await adapter.hangup("abc")
    assert hangup.called

    await adapter.transfer("abc", "+15550003333")
    assert transfer.called
    assert json.loads(transfer.calls[0].request.content)["action"] == "transfer"

    await adapter.send_dtmf("abc", "123")
    assert dtmf.called
    assert json.loads(dtmf.calls[0].request.content) == {"digits": "123"}

    await adapter.start_playback("abc", "hello")
    assert talk.called
    assert json.loads(talk.calls[0].request.content)["text"] == "hello"

    await adapter.start_stream("abc", "https://example.test/a.wav")
    assert stream.called
    assert json.loads(stream.calls[0].request.content)["stream_url"] == [
        "https://example.test/a.wav"
    ]


@pytest.mark.asyncio
@respx.mock
async def test_the_recording_is_started_and_stopped(vonage_credentials) -> None:
    route = respx.put(f"{VOICE_API}/v1/calls/call-1/recording").mock(
        return_value=Response(
            200, json={"recording_url": "https://api.nexmo.com/v1/files/abc"}
        )
    )
    adapter = VonageAdapter()

    started = await adapter.start_recording("call-1")
    assert started.status == "recording"
    assert started.external_id == "https://api.nexmo.com/v1/files/abc"
    assert route.calls[0].request.method == "PUT"
    assert json.loads(route.calls[0].request.content) == {"action": "start"}

    stopped = await adapter.stop_recording("call-1", "rec-1")
    assert stopped.status == "stopped"
    assert stopped.external_id == "rec-1"
    assert json.loads(route.calls[1].request.content) == {"action": "stop"}


@pytest.mark.asyncio
async def test_invalid_dtmf_is_rejected(vonage_credentials) -> None:
    adapter = VonageAdapter()
    with pytest.raises(ProviderValidationError):
        await adapter.send_dtmf("abc", "abc")


@pytest.mark.asyncio
async def test_voice_calls_need_a_configured_account() -> None:
    adapter = VonageAdapter()
    with pytest.raises(ProviderConfigurationError):
        await adapter.create_outbound(
            to_number="+15550001111",
            from_number="+15550002222",
            answer_url="https://example.test/answer",
        )


@pytest.mark.asyncio
@respx.mock
async def test_a_transport_timeout_is_typed(vonage_credentials, no_sleep) -> None:
    import httpx

    respx.post(f"{VOICE_API}/v1/calls").mock(
        side_effect=httpx.ReadTimeout("the voice api did not answer")
    )
    adapter = VonageAdapter()
    with pytest.raises(ProviderTimeoutError):
        await adapter.create_outbound(
            to_number="+15550001111",
            from_number="+15550002222",
            answer_url="https://example.test/answer",
        )
