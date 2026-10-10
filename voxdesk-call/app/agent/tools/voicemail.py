"""Voicemail Detection & Action Handler wrapping Pipecat `VoicemailDetector` (Sub-Phase 2D).

Supports three agent-configurable actions when an answering machine / voicemail is detected:
- `hangup`: Immediately terminate the call (`call.end_reason = "voicemail_hangup"`).
- `leave_message`: Wait for the voicemail greeting to finish / beep, speak the configured
  `voicemail_message` via `TTSSpeakFrame`, and then hang up (`call.end_reason = "voicemail_left"`).
- `ignore`: Continue the conversation normally without hanging up.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from pipecat.extensions.voicemail.voicemail_detector import VoicemailDetector
from pipecat.frames.frames import EndFrame, EndTaskFrame, Frame, TTSSpeakFrame

log = structlog.get_logger()


class VoicemailAction(str, enum.Enum):
    HANGUP = "hangup"
    LEAVE_MESSAGE = "leave_message"
    IGNORE = "ignore"


_VOICEMAIL_REGEX = re.compile(
    r"(?:please\s+leave\s+(?:a\s+|your\s+)?message|"
    r"after\s+the\s+(?:beep|tone)|"
    r"not\s+available\s+to\s+take\s+your\s+call|"
    r"reached\s+the\s+voicemail\s+(?:box\s+)?of|"
    r"person\s+you\s+are\s+trying\s+to\s+reach|"
    r"mailbox\s+is\s+full|"
    r"at\s+the\s+tone[,\s]+please\s+record)",
    re.IGNORECASE,
)


class VoicemailFlowHandler:
    """Manages voicemail detection classification and post-detection actions (`hangup`, `leave_message`, `ignore`)."""

    def __init__(
        self,
        *,
        action: str | VoicemailAction = VoicemailAction.HANGUP,
        voicemail_message: str | None = None,
        call: Any | None = None,
        call_sid: str | None = None,
        telephony_provider: Any | None = None,
        frame_pusher: Callable[[Frame], Awaitable[None]] | None = None,
    ) -> None:
        try:
            self.action = VoicemailAction(str(action or "hangup").strip().lower())
        except ValueError:
            self.action = VoicemailAction.HANGUP
        self.voicemail_message = (
            (voicemail_message or "").strip()
            or "Hello, we tried reaching you. Please call us back at your earliest convenience. Thank you!"
        )
        self.call = call
        self.call_sid = call_sid or getattr(call, "call_sid", None) or ""
        self.telephony_provider = telephony_provider
        self.frame_pusher = frame_pusher
        self.detected: bool = False
        self.handled: bool = False
        self.emitted_frames: list[Frame] = []

    @staticmethod
    def is_voicemail_greeting(transcript: str) -> bool:
        """Heuristic fast-path check for common voicemail greeting phrases."""
        return bool(_VOICEMAIL_REGEX.search(transcript or ""))

    async def _push(self, frame: Frame) -> None:
        self.emitted_frames.append(frame)
        if self.frame_pusher is not None:
            await self.frame_pusher(frame)

    async def on_voicemail_detected(self, processor: Any | None = None) -> dict[str, Any]:
        """Callback invoked when `VoicemailDetector` (or greeting classifier) detects voicemail."""
        self.detected = True
        if self.handled:
            return {"detected": True, "action": self.action.value, "already_handled": True}
        self.handled = True

        if self.action == VoicemailAction.IGNORE:
            log.info("voicemail.detected.ignored", call_sid=self.call_sid)
            return {
                "detected": True,
                "action": VoicemailAction.IGNORE.value,
                "frames_emitted": [],
            }

        if self.action == VoicemailAction.HANGUP:
            if self.call is not None and hasattr(self.call, "end_reason"):
                self.call.end_reason = "voicemail_hangup"
            await self._push(EndTaskFrame())
            await self._push(EndFrame())
            if self.telephony_provider is not None and self.call_sid:
                try:
                    await self.telephony_provider.hangup_call(self.call_sid)
                except Exception as exc:
                    log.warning("voicemail.hangup_failed", error=str(exc)[:160])
            log.info("voicemail.detected.hangup", call_sid=self.call_sid)
            return {
                "detected": True,
                "action": VoicemailAction.HANGUP.value,
                "end_reason": "voicemail_hangup",
                "frames_emitted": ["EndTaskFrame", "EndFrame"],
            }

        # VoicemailAction.LEAVE_MESSAGE
        if self.call is not None and hasattr(self.call, "end_reason"):
            self.call.end_reason = "voicemail_left"

        speak_frame = TTSSpeakFrame(text=self.voicemail_message)
        if processor is not None and hasattr(processor, "push_frame"):
            await processor.push_frame(speak_frame)
            self.emitted_frames.append(speak_frame)
        else:
            await self._push(speak_frame)

        await self._push(EndTaskFrame())
        await self._push(EndFrame())
        if self.telephony_provider is not None and self.call_sid:
            try:
                await self.telephony_provider.hangup_call(self.call_sid)
            except Exception as exc:
                log.warning("voicemail.leave_message_hangup_failed", error=str(exc)[:160])

        log.info(
            "voicemail.detected.left_message",
            call_sid=self.call_sid,
            message_len=len(self.voicemail_message),
        )
        return {
            "detected": True,
            "action": VoicemailAction.LEAVE_MESSAGE.value,
            "end_reason": "voicemail_left",
            "voicemail_message": self.voicemail_message,
            "frames_emitted": ["TTSSpeakFrame", "EndTaskFrame", "EndFrame"],
        }

    async def inspect_transcript(self, text: str) -> dict[str, Any] | None:
        """Inspect an opening utterance and trigger `on_voicemail_detected` if it matches voicemail."""
        if self.is_voicemail_greeting(text):
            return await self.on_voicemail_detected()
        return None


def build_voicemail_detector(
    *,
    llm: Any,
    handler: VoicemailFlowHandler,
    voicemail_response_delay: float = 2.0,
) -> VoicemailDetector:
    """Wrap Pipecat's `VoicemailDetector` and bind `on_voicemail_detected` to `handler`."""
    detector = VoicemailDetector(
        llm=llm,
        voicemail_response_delay=voicemail_response_delay,
    )

    @detector.event_handler("on_voicemail_detected")
    async def _handle_vm(processor):
        await handler.on_voicemail_detected(processor)

    return detector
