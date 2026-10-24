"""The OpenAI Realtime client, exercised against a local WebSocket server.

The server speaks the Realtime wire protocol (``session.update``, audio
deltas, transcripts, function calls, ``response.done``), so the test proves
that the client *translates* the protocol instead of proving that it can
call a mock.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager

import pytest
import websockets

from app.agent.s2s.openai_realtime import OpenAIRealtimeSession
from app.agent.s2s.s2s_session import (
    ReconnectPolicy,
    S2SConfigurationError,
    S2SEventType,
)

_TOOLS = [
    {
        "type": "function",
        "name": "transfer_to_human",
        "description": "Transfer the call to a human agent.",
        "parameters": {
            "type": "object",
            "properties": {"queue": {"type": "string"}},
            "required": ["queue"],
        },
    }
]


class _Provider:
    """A local server that speaks the Realtime protocol."""

    def __init__(self, *, script: list[dict] | None = None, drop_after_first: bool = False):
        self.received: list[dict] = []
        self.connections = 0
        self.script = list(script or [])
        self.drop_after_first = drop_after_first
        self.headers: dict[str, str] = {}
        self._server = None

    async def __aenter__(self) -> "_Provider":
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def types(self) -> list[str]:
        return [str(message.get("type")) for message in self.received]

    async def _handler(self, websocket) -> None:
        self.connections += 1
        self.headers = {k.lower(): v for k, v in websocket.request_headers.items()}
        async for raw in websocket:
            message = json.loads(raw)
            self.received.append(message)
            if message.get("type") == "session.update":
                await websocket.send(json.dumps({"type": "session.created", "session": {}}))
                for event in self.script:
                    await websocket.send(json.dumps(event))
            if self.drop_after_first:
                await websocket.close()
                return


def _session(provider: _Provider, **kwargs) -> OpenAIRealtimeSession:
    return OpenAIRealtimeSession(
        api_key="test-key",
        url=provider.url,
        connect_factory=lambda url, headers: websockets.connect(url, extra_headers=headers),
        **kwargs,
    )


@asynccontextmanager
async def _running(session: OpenAIRealtimeSession):
    """Run a session and collect the events it emits."""
    events: list = []

    async def _drain() -> None:
        async for event in session.events():
            events.append(event)

    task = asyncio.create_task(_drain())
    await session.start()
    try:
        yield events
    finally:
        await session.close()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _wait_for(events: list, count: int, timeout: float = 5.0) -> list:
    deadline = time.monotonic() + timeout
    while len(events) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    return events


async def test_the_session_update_carries_the_voice_and_the_tools() -> None:
    async with _Provider() as provider:
        session = _session(
            provider,
            system_prompt="You are a receptionist.",
            voice="verse",
            tools=_TOOLS,
        )
        async with _running(session):
            await _wait_for(provider.received, 1)
            update = provider.received[0]
            assert update["type"] == "session.update"
            payload = update["session"]
            assert payload["modalities"] == ["text", "audio"]
            assert payload["voice"] == "verse"
            assert payload["instructions"] == "You are a receptionist."
            assert payload["input_audio_format"] == "pcm16"
            assert payload["turn_detection"]["type"] == "server_vad"
            assert payload["tools"] == _TOOLS
            assert provider.headers.get("authorization") == "Bearer test-key"


async def test_the_caller_audio_is_appended_and_committed() -> None:
    async with _Provider() as provider:
        session = _session(provider)
        async with _running(session):
            await _wait_for(provider.received, 1)
            await session.send_audio(b"\x01\x02" * 160, sample_rate=16000)
            await session.commit()
            await _wait_for(provider.received, 4)
            kinds = provider.types()
            assert "input_audio_buffer.append" in kinds
            assert "input_audio_buffer.commit" in kinds
            assert "response.create" in kinds
            append = next(
                message
                for message in provider.received
                if message["type"] == "input_audio_buffer.append"
            )
            assert base64.b64decode(append["audio"]) == b"\x01\x02" * 160
            assert session.input_bytes == len(b"\x01\x02" * 160)


async def test_the_provider_audio_becomes_an_audio_event() -> None:
    script = [
        {
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(b"\x10\x20" * 80).decode("ascii"),
        }
    ]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            event = events[0]
            assert event.type is S2SEventType.AUDIO_OUT
            assert event.audio == b"\x10\x20" * 80
            assert event.provider == "openai_realtime"
            assert session.output_bytes == len(b"\x10\x20" * 80)


async def test_the_transcripts_are_translated() -> None:
    script = [
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "I need to reschedule my appointment.",
        },
        {
            "type": "response.output_audio_transcript.done",
            "transcript": "Of course, let me check that for you.",
        },
    ]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 2)
            kinds = [event.type for event in events]
            assert S2SEventType.TRANSCRIPT_IN in kinds
            assert S2SEventType.TRANSCRIPT_OUT in kinds
            spoken = next(e for e in events if e.type is S2SEventType.TRANSCRIPT_IN)
            assert spoken.text == "I need to reschedule my appointment."


async def test_a_function_call_becomes_a_tool_call() -> None:
    script = [
        {
            "type": "response.function_call_arguments.done",
            "call_id": "call-42",
            "name": "transfer_to_human",
            "arguments": json.dumps({"queue": "billing"}),
        }
    ]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            event = events[0]
            assert event.type is S2SEventType.TOOL_CALL
            assert event.tool_call_id == "call-42"
            assert event.tool_name == "transfer_to_human"
            assert event.arguments == {"queue": "billing"}


async def test_a_tool_result_is_returned_to_the_provider() -> None:
    async with _Provider() as provider:
        session = _session(provider)
        async with _running(session):
            await _wait_for(provider.received, 1)
            await session.send_tool_result("call-42", "transfer_to_human", {"ok": True})
            await _wait_for(provider.received, 2)
            item = provider.received[1]
            assert item["type"] == "conversation.item.create"
            assert item["item"]["call_id"] == "call-42"
            assert json.loads(item["item"]["output"]) == {"ok": True}


async def test_an_interrupt_cancels_the_response() -> None:
    async with _Provider() as provider:
        session = _session(provider)
        async with _running(session):
            await _wait_for(provider.received, 1)
            await session.interrupt()
            await _wait_for(provider.received, 3)
            kinds = provider.types()
            assert "response.cancel" in kinds
            assert "input_audio_buffer.clear" in kinds


async def test_the_speech_start_is_translated_into_an_interruption() -> None:
    script = [{"type": "input_audio_buffer.speech_started"}]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            assert events[0].type is S2SEventType.INTERRUPTED


async def test_the_usage_is_read_from_the_finished_response() -> None:
    script = [
        {
            "type": "response.done",
            "response": {"usage": {"input_tokens": 120, "output_tokens": 45}},
        }
    ]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            assert events[0].type is S2SEventType.TURN_END
            assert session.usage == {"input_tokens": 120, "output_tokens": 45}


async def test_a_provider_error_is_translated() -> None:
    script = [{"type": "error", "error": {"message": "rate_limit_exceeded"}}]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            assert events[0].type is S2SEventType.ERROR
            assert events[0].error == "rate_limit_exceeded"


async def test_the_socket_is_re_established_after_a_drop() -> None:
    async with _Provider(drop_after_first=True) as provider:
        session = _session(
            provider,
            reconnect_policy=ReconnectPolicy(max_attempts=3, base_delay_seconds=0.01),
        )
        async with _running(session):
            deadline = time.monotonic() + 5.0
            while provider.connections < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert provider.connections >= 2


async def test_a_session_without_an_api_key_is_refused() -> None:
    async with _Provider() as provider:
        session = OpenAIRealtimeSession(api_key="", url=provider.url)
        with pytest.raises(S2SConfigurationError):
            await session.start()
