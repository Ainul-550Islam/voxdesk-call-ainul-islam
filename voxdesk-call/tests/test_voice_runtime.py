from __future__ import annotations

import pytest

from app.agent.stt_stream import STTEvent
from app.tts.provider import TTSChunk
from app.voice.agent_loop import AssistantResponse, VoiceAgentLoop


class _LLM:
    async def respond(self, text, *, context):
        return AssistantResponse("response")


class _TTS:
    def __init__(self):
        self.cancelled = []

    async def stream(self, request, *, policy=None):
        yield TTSChunk(b"out", 0, request.request_id, "fake", 8000, "pcm_mulaw")
        yield TTSChunk(b"", 1, request.request_id, "fake", 8000, "pcm_mulaw", is_final=True)

    async def cancel(self, request_id, provider=None):
        self.cancelled.append(request_id)


class _Sink:
    def __init__(self):
        self.chunks = []

    async def write(self, chunk):
        self.chunks.append(chunk)


async def _events():
    yield STTEvent("partial", False, None, None, None, "en-US", "deepgram", "r")
    yield STTEvent("hello", True, 1, 0, 1, "en-US", "deepgram", "r", True)


@pytest.mark.asyncio
async def test_agent_loop_uses_final_transcript_and_bounded_sink():
    sink = _Sink()
    loop = VoiceAgentLoop(tts=_TTS())
    state = await loop.handle_call(
        {
            "stt_events": _events(),
            "llm": _LLM(),
            "audio_sink": sink,
            "voice_id": "voice",
            "request_id": "r",
        }
    )
    assert state.final_text == "hello"
    assert [chunk.audio for chunk in sink.chunks] == [b"out", b""]
    assert state.turns[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_agent_loop_rejects_missing_transport_adapter():
    loop = VoiceAgentLoop(tts=_TTS())
    with pytest.raises(ValueError, match="stt_events"):
        await loop.handle_call({})
