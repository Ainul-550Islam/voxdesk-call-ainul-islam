"""The Telnyx media-stream capability flag: honest, credential-derived, and gated.

The flag is not a vendor claim. Telnyx does stream AI media; *this deployment* can
only do it when it holds the API key that controls the call, the public key that
proves a webhook came from Telnyx, and the connection id the stream attaches to.
Anything less keeps the flag false and every media entry point answers
``UNSUPPORTED_CAPABILITY`` (HTTP 422) instead of answering a live caller into a
stream that cannot be authenticated.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.telephony.provider_errors import ProviderValidationError, UnsupportedCapability
from app.telephony.providers.base import HttpResult
from app.telephony.providers.telnyx import TelnyxAdapter

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ScriptedTransport:
    """Records attempts; the gate must refuse before any request is made."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, method, url, *, headers, json, timeout):
        self.calls.append({"method": method, "url": url, "json": json, "headers": dict(headers)})
        return HttpResult(200, {"data": {"result": "ok"}})

    @property
    def attempts(self) -> int:
        return len(self.calls)


@pytest.fixture(autouse=True)
def _telnyx_credentials(monkeypatch):
    """A key plus fast retries; each test adds or removes the other credentials."""
    monkeypatch.setattr(settings, "telnyx_api_key", "KEY-test-0123456789abcdef")
    monkeypatch.setattr(settings, "telnyx_call_control_max_retries", 0)
    monkeypatch.setattr(settings, "telnyx_call_control_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings, "telnyx_call_control_timeout_seconds", 2.0)
    yield


# --------------------------------------------------------------------------- #
# Media stream capability gate
# --------------------------------------------------------------------------- #


async def test_capability_is_false_until_every_credential_is_present(monkeypatch):
    adapter = TelnyxAdapter()
    for name in ("telnyx_api_key", "telnyx_public_key", "telnyx_connection_id"):
        monkeypatch.setattr(settings, name, "")

    assert adapter.supports_ai_media_stream is False
    assert adapter.missing_media_stream_settings == (
        "TELNYX_API_KEY",
        "TELNYX_PUBLIC_KEY",
        "TELNYX_CONNECTION_ID",
    )
    capability = adapter.media_stream_capability()
    assert capability["state"] == "NOT_CONFIGURED"
    assert capability["supports_ai_media_stream"] is False

    monkeypatch.setattr(settings, "telnyx_api_key", "KEY-test")
    assert adapter.supports_ai_media_stream is False
    monkeypatch.setattr(settings, "telnyx_public_key", "PUBLIC-test")
    assert adapter.supports_ai_media_stream is False
    monkeypatch.setattr(settings, "telnyx_connection_id", "1234567890")
    assert adapter.supports_ai_media_stream is True
    assert adapter.media_stream_capability() == {
        "provider": "telnyx",
        "supports_ai_media_stream": True,
        "state": "READY",
        "missing": [],
        "serializer": "TelnyxMediaStream",
        "encodings": ["PCMU", "PCMA", "L16"],
        "sample_rates": [8000],
        "pipeline_sample_rate": 16000,
        "stream_endpoint": "/telephony/telnyx/ws",
        "call_control_api": "v2",
    }


async def test_require_media_stream_raises_the_unsupported_capability_error(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_public_key", "")
    adapter = TelnyxAdapter()

    with pytest.raises(UnsupportedCapability) as error:
        adapter.require_ai_media_stream()

    assert error.value.code == "unsupported_capability"
    assert error.value.status_code == 422


async def test_start_media_stream_is_refused_without_the_full_credential_set(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_connection_id", "")
    transport = ScriptedTransport()
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(UnsupportedCapability):
        await adapter.start_media_stream("v3:call-1", stream_url="wss://example.test/telephony/telnyx/ws")

    assert transport.attempts == 0


async def test_start_media_stream_requires_a_secure_url(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_public_key", "PUBLIC-test")
    monkeypatch.setattr(settings, "telnyx_connection_id", "1234567890")
    transport = ScriptedTransport()
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(ProviderValidationError):
        await adapter.start_media_stream("v3:call-1", stream_url="ws://insecure.test/ws")

    assert transport.attempts == 0


async def test_start_media_stream_sends_the_bidirectional_rtp_payload(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_public_key", "PUBLIC-test")
    monkeypatch.setattr(settings, "telnyx_connection_id", "1234567890")
    transport = ScriptedTransport()
    adapter = TelnyxAdapter(transport=transport)

    await adapter.start_media_stream(
        "v3:call-1",
        stream_url="wss://example.test/telephony/telnyx/ws?token=abc",
        client_state="eyJjYWxsIjoiMSJ9",
    )

    call = transport.calls[0]
    assert call["url"].endswith("/calls/v3:call-1/actions/streaming_start")
    assert call["json"] == {
        "stream_url": "wss://example.test/telephony/telnyx/ws?token=abc",
        "stream_bidirectional_mode": "rtp",
        "stream_bidirectional_codec": "PCMU",
        "client_state": "eyJjYWxsIjoiMSJ9",
    }


async def test_streaming_stop_is_an_allowed_command(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_public_key", "PUBLIC-test")
    monkeypatch.setattr(settings, "telnyx_connection_id", "1234567890")
    transport = ScriptedTransport()
    adapter = TelnyxAdapter(transport=transport)

    await adapter.call_control("streaming_stop", "v3:call-1", {})

    assert transport.calls[0]["url"].endswith("/actions/streaming_stop")
