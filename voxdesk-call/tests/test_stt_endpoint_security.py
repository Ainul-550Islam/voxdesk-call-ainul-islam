"""Regression tests for the fixed-origin Deepgram WebSocket boundary."""
from __future__ import annotations

import pytest

from app.agent.errors import ProviderConfigurationError, ProviderUnavailableError
from app.agent.stt_stream import DeepgramStreamingProvider, STTStreamRequest


@pytest.mark.parametrize(
    "endpoint",
    (
        "ws://api.deepgram.com",
        "wss://127.0.0.1:2375",
        "wss://169.254.169.254/latest/meta-data",
        "wss://attacker.example",
        "wss://user:pass@api.deepgram.com",
        "wss://api.deepgram.com:444",
        "wss://api.deepgram.com/v1/listen?redirect=https://127.0.0.1",
        "wss://api.deepgram.com.evil.example",
    ),
)
def test_provider_rejects_untrusted_or_ambiguous_websocket_origins(endpoint):
    with pytest.raises(ProviderConfigurationError):
        DeepgramStreamingProvider("test-key", endpoint=endpoint)


def test_listen_query_encodes_user_controlled_values_and_keeps_fixed_origin():
    provider = DeepgramStreamingProvider("test-key")
    request = STTStreamRequest(
        language="en-US&model=attacker",
        model="nova-3&redirect=wss://127.0.0.1",
    )

    url = provider._listen_url(request)

    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    assert "language=en-US%26model%3Dattacker" in url
    assert "model=nova-3%26redirect%3Dwss%3A%2F%2F127.0.0.1" in url
    assert "&redirect=" not in url
    assert "test-key" not in url


@pytest.mark.asyncio
async def test_stream_connection_errors_keep_api_key_out_of_url(monkeypatch):
    provider = DeepgramStreamingProvider("prompt7-secret-sentinel")
    captured = {}

    async def reject_connection(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        raise OSError("connection unavailable")

    async def no_audio():
        if False:
            yield b""

    monkeypatch.setattr("app.agent.stt_stream.websockets.connect", reject_connection)
    with pytest.raises(ProviderUnavailableError):
        async for _ in provider.stream(no_audio(), STTStreamRequest()):
            pass

    assert captured["url"].startswith("wss://api.deepgram.com/v1/listen?")
    assert "prompt7-secret-sentinel" not in captured["url"]
    headers_key = provider._headers_keyword
    assert captured["kwargs"][headers_key]["Authorization"] == "Token prompt7-secret-sentinel"
    assert "prompt7-secret-sentinel" not in repr(captured["kwargs"].get("additional_headers", {}))
