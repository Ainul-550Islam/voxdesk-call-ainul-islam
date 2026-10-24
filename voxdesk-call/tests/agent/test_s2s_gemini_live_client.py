"""The Gemini Live client, exercised against a local WebSocket server.

The server speaks the Gemini Live envelope (``setup``, ``serverContent``,
``toolCall``, ``sessionResumptionUpdate``), so the test proves that the
client translates the protocol instead of proving that it can call a mock.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager

import pytest
import websockets

from app.agent.s2s.gemini_live import GeminiLiveSession
from app.agent.s2s.s2s_session import (
    ReconnectPolicy,
    S2SConfigurationError,
    S2SEventType,
)

_TOOLS = [
    {
        "function_declarations": [
            {
                "name": "transfer_to_human",
                "description": "Transfer the call to a human agent.",
                "parameters": {
                    "type": "object",
                    "properties": {"queue": {"type": "string"}},
                    "required": ["queue"],
                },
            }
        ]
    }
]


class _Provider:
    """A local server that speaks the Gemini Live envelope."""

    def __init__(self, *, script: list[dict] | None = None, drop_after_first: bool = False):
        self.received: list[dict] = []
        self.connections = 0
        self.script = list(script or [])
        self.drop_after_first = drop_after_first
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

    def keys(self) -> list[str]:
        return [str(message.get("setup") is not None) for message in self.received]

    async def _handler(self, websocket) -> None:
        self.connections += 1
        async for raw in websocket:
            message = json.loads(raw)
            self.received.append(message)
            if "setup" in message:
                for event in self.script:
                    await websocket.send(json.dumps(event))
            if self.drop_after_first:
                await websocket.close()
                return


def _session(provider: _Provider, **kwargs) -> GeminiLiveSession:
    return GeminiLiveSession(
        api_key="test-key",
        url=provider.url,
        connect_factory=lambda url, headers: websockets.connect(url, extra_headers=headers),
        **kwargs,
    )


@asynccontextmanager
async def _running(session: GeminiLiveSession):
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


async def test_the_setup_message_carries_the_model_and_the_voice() -> None:
    async with _Provider() as provider:
        session = _session(
            provider,
            system_prompt="You are a receptionist.",
            voice="Puck",
            tools=_TOOLS,
        )
        async with _running(session):
            await _wait_for(provider.received, 1)
            setup = provider.received[0]["setup"]
            assert setup["model"] == "models/gemini-2.0-flash-exp"
            assert setup["system_instruction"] == {
                "parts": [{"text": "You are a receptionist."}]
            }
            voice = setup["generation_config"]["speech_config"]["voice_config"]
            assert voice["prebuilt_voice_config"]["voice_name"] == "Puck"
            assert setup["tools"] == _TOOLS


async def test_the_caller_audio_is_sent_as_a_realtime_chunk() -> None:
    async with _Provider() as provider:
        session = _session(provider)
        async with _running(session):
            await _wait_for(provider.received, 1)
            await session.send_audio(b"\x01\x02" * 160, sample_rate=16000)
            await _wait_for(provider.received, 2)
            chunk = provider.received[1]["realtime_input"]["media_chunks"][0]
            assert chunk["mime_type"] == "audio/pcm;rate=16000"
            assert base64.b64decode(chunk["data"]) == b"\x01\x02" * 160
            assert session.input_bytes == len(b"\x01\x02" * 160)


async def test_the_provider_audio_becomes_an_audio_event() -> None:
    script = [
        {
            "serverContent": {
                "modelTurn": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm;rate=16000",
                                "data": base64.b64encode(b"\x10\x20" * 80).decode("ascii"),
                            }
                        }
                    ]
                },
                "turnComplete": True,
            }
        }
    ]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 2)
            kinds = [event.type for event in events]
            assert S2SEventType.AUDIO_OUT in kinds
            assert S2SEventType.TURN_END in kinds
            audio = next(e for e in events if e.type is S2SEventType.AUDIO_OUT)
            assert audio.audio == b"\x10\x20" * 80


async def test_an_interruption_is_translated() -> None:
    script = [{"serverContent": {"interrupted": True}}]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            assert events[0].type is S2SEventType.INTERRUPTED


async def test_a_function_call_becomes_a_tool_call() -> None:
    script = [
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "call-7",
                        "name": "transfer_to_human",
                        "args": {"queue": "billing"},
                    }
                ]
            }
        }
    ]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            event = events[0]
            assert event.type is S2SEventType.TOOL_CALL
            assert event.tool_call_id == "call-7"
            assert event.tool_name == "transfer_to_human"
            assert event.arguments == {"queue": "billing"}


async def test_a_tool_result_is_returned_to_the_provider() -> None:
    async with _Provider() as provider:
        session = _session(provider)
        async with _running(session):
            await _wait_for(provider.received, 1)
            await session.send_tool_result("call-7", "transfer_to_human", {"ok": True})
            await _wait_for(provider.received, 2)
            response = provider.received[1]["tool_response"]["function_responses"][0]
            assert response["id"] == "call-7"
            assert response["name"] == "transfer_to_human"
            assert response["response"] == {"ok": True}


async def test_an_interrupt_completes_the_turn() -> None:
    async with _Provider() as provider:
        session = _session(provider)
        async with _running(session):
            await _wait_for(provider.received, 1)
            await session.interrupt()
            await _wait_for(provider.received, 2)
            assert provider.received[1]["clientContent"]["turnComplete"] is True


async def test_the_input_transcription_is_translated() -> None:
    script = [{"inputTranscription": {"text": "I need an appointment."}}]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            assert events[0].type is S2SEventType.TRANSCRIPT_IN
            assert events[0].text == "I need an appointment."


async def test_a_provider_error_is_translated() -> None:
    script = [{"error": {"message": "quota_exceeded"}}]
    async with _Provider(script=script) as provider:
        session = _session(provider)
        async with _running(session) as events:
            await _wait_for(events, 1)
            assert events[0].type is S2SEventType.ERROR
            assert events[0].error == "quota_exceeded"


async def test_the_resumption_handle_is_remembered_and_replayed() -> None:
    script = [{"sessionResumptionUpdate": {"newHandle": "handle-42"}}]
    async with _Provider(script=script, drop_after_first=True) as provider:
        session = _session(
            provider,
            reconnect_policy=ReconnectPolicy(max_attempts=3, base_delay_seconds=0.01),
        )
        async with _running(session):
            deadline = time.monotonic() + 5.0
            while provider.connections < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert provider.connections >= 2
            assert session.resumption_handle == "handle-42"
            setups = [m for m in provider.received if "setup" in m]
            assert setups[0]["setup"]["session_resumption"] == {}
            assert setups[1]["setup"]["session_resumption"] == {"handle": "handle-42"}


async def test_a_session_without_an_api_key_is_refused() -> None:
    async with _Provider() as provider:
        session = GeminiLiveSession(api_key="", url=provider.url)
        with pytest.raises(S2SConfigurationError):
            await session.start()
