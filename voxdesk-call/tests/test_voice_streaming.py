from __future__ import annotations

import pytest

from app.agent.errors import ProviderUnavailableError
from app.agent.stt_stream import STTEvent
from app.tts.engine import TTSEngine, TTSRuntimePolicy
from app.tts.provider import TTSChunk, TTSProviderCapabilities, TTSProviderHealth, TTSRequest
from app.tts.registry import TTSRegistration, TTSRegistry


class _RetryProvider:
    name = "primary"
    capabilities = TTSProviderCapabilities("primary", True, True, False, True, (8000,), ("pcm_mulaw",))
    attempts = 0

    async def stream(self, request):
        self.attempts += 1
        if self.attempts == 1:
            raise ProviderUnavailableError("temporary failure", provider=self.name)
        yield TTSChunk(b"audio", 0, request.request_id, self.name, 8000, "pcm_mulaw")
        yield TTSChunk(b"", 1, request.request_id, self.name, 8000, "pcm_mulaw", is_final=True)

    async def cancel(self, request_id):
        return None

    async def health(self):
        return TTSProviderHealth(self.name, True, 0, "health")


@pytest.mark.asyncio
async def test_tts_retries_before_emitting_audio():
    provider = _RetryProvider()
    registry = TTSRegistry((TTSRegistration("primary", lambda: provider),))
    engine = TTSEngine(registry)
    request = TTSRequest(text="hello", voice_id="voice", provider="primary")

    chunks = [chunk async for chunk in engine.stream(
        request, policy=TTSRuntimePolicy("primary", max_retries=1, backoff_seconds=0)
    )]

    assert provider.attempts == 2
    assert chunks[0].audio == b"audio"
    assert chunks[-1].is_final


@pytest.mark.asyncio
async def test_stt_event_contract_preserves_partial_and_final_flags():
    event = STTEvent("hello", True, 0.99, 0.0, 0.4, "en-US", "deepgram", "request", True)
    assert event.is_final is True
    assert event.speech_final is True
