"""Realtime voice-session facade over the existing Pipecat/Twilio transport."""
from __future__ import annotations

from typing import Any

from app.voice.agent_loop import VoiceAgentLoop


class LiveVoiceLoop:
    """Own session cancellation while reusing the existing media boundary.

    Twilio/Pipecat calls use ``run_existing_pipeline`` below; alternate
    transports can provide the typed event source consumed by ``VoiceAgentLoop``.
    This class does not create a second websocket or media engine.
    """

    def __init__(self, agent_loop: VoiceAgentLoop | None = None) -> None:
        self.agent_loop = agent_loop or VoiceAgentLoop()

    async def run(self, session_context: dict[str, Any]):
        try:
            return await self.agent_loop.handle_call(session_context)
        finally:
            await self.agent_loop.close()

    async def run_existing_pipeline(
        self, *, websocket, stream_sid: str, call_sid: str, session, tenant, call
    ) -> None:
        """Use the repository's current Pipecat pipeline for live Twilio calls."""
        from app.agent.pipeline import run_voice_agent

        try:
            await run_voice_agent(
                websocket=websocket,
                stream_sid=stream_sid,
                call_sid=call_sid,
                session=session,
                tenant=tenant,
                call=call,
            )
        finally:
            await self.agent_loop.close()

    async def turn(self, audio_input: bytes, context: dict[str, Any]) -> bytes:
        """Compatibility helper for callers with a complete turn adapter.

        A transport must supply ``process_turn``; silently returning fake audio
        is not allowed. The existing Twilio path uses ``run_existing_pipeline``.
        """
        process_turn = context.get("process_turn")
        if process_turn is None:
            raise ValueError("live voice turn requires a configured process_turn adapter")
        result = await process_turn(audio_input, context)
        if not isinstance(result, bytes):
            raise TypeError("voice turn adapter must return audio bytes")
        return result

    async def interrupt(self) -> None:
        await self.agent_loop.interrupt()

    async def close(self) -> None:
        await self.agent_loop.close()

    async def escalate(self, call_id: str, reason: str, *, session, tenant, call) -> dict[str, str | bool]:
        from app.escalation.transfer import Escalation

        return await Escalation().transfer(
            call_id,
            reason,
            session=session,
            tenant=tenant,
            call=call,
        )
