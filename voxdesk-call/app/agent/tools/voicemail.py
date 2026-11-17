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
from dataclasses import dataclass
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

# ---------------------------------------------------------------------------
# PART 12 (P12-13) — configurable voicemail actions, word cap, PII guard
# ---------------------------------------------------------------------------

#: The three actions the specification exposes. `leave_message` is kept as an alias of
#: `static_message` so the older configuration keeps working.
VOICEMAIL_ACTIONS: tuple[str, ...] = ("hangup", "static_message", "generated_message")

MAX_GENERATED_WORDS = 40

#: Anything that looks like an identifier we must not read into a stranger's mailbox.
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"\+?\d{7,15}\b")),
)


def _pii_hits(text: str) -> list[str]:
    return [label for label, pattern in _PII_PATTERNS if pattern.search(text or "")]


def substitute_dynamic_variables(text: str, variables: dict[str, Any] | None = None) -> str:
    """Replace `{{name}}` with a value; unknown names are left visible rather than blanked.

    Leaving `{{appointment_time}}` in the text is ugly but honest — a blank tells the listener
    nothing and hides a configuration mistake from the operator reading the transcript.
    """
    variables = variables or {}

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = variables.get(key)
        return str(value) if value not in (None, "") else match.group(0)

    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", repl, text or "")


def enforce_word_cap(text: str, *, limit: int = MAX_GENERATED_WORDS) -> tuple[str, bool]:
    """Trim to `limit` words on a sentence boundary when possible. Returns `(text, trimmed)`."""
    words = (text or "").split()
    if len(words) <= limit:
        return (text or "").strip(), False
    clipped = " ".join(words[:limit])
    for stop in (".", "!", "?"):
        index = clipped.rfind(stop)
        if index > len(clipped) // 2:
            return clipped[: index + 1].strip(), True
    return clipped.rstrip(",;:") + ".", True


@dataclass(frozen=True)
class VoicemailPlan:
    """What the agent will do once voicemail is detected, decided before anything is said."""

    action: str
    message: str = ""
    detection_window_s: float = 12.0
    blocked_reason: str = ""
    trimmed: bool = False
    generated: bool = False

    @property
    def allowed(self) -> bool:
        return self.action != "hangup" and not self.blocked_reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "message": self.message,
            "detection_window_s": self.detection_window_s,
            "blocked_reason": self.blocked_reason,
            "trimmed": self.trimmed,
            "generated": self.generated,
            "allowed": self.allowed,
        }


def plan_voicemail_action(
    *,
    action: str = "hangup",
    message: str = "",
    variables: dict[str, Any] | None = None,
    detection_window_s: float = 12.0,
    generated_text: str = "",
) -> VoicemailPlan:
    """Resolve the configured voicemail action into a plan.

    `generated_message` requires text produced by the LLM; if it is missing or leaks an identifier
    the plan falls back to hangup with a stated reason instead of reading numbers into a machine.
    """
    resolved = str(action or "hangup").strip().lower()
    if resolved == "leave_message":
        resolved = "static_message"
    if resolved not in VOICEMAIL_ACTIONS:
        raise ValueError(
            f"unknown voicemail action {action!r}; expected one of {list(VOICEMAIL_ACTIONS)}"
        )
    # `or 12.0` would turn an explicit 0 into the default, hiding a caller that asked for
    # "no window at all"; only `None` means "use the default".
    raw_window = 12.0 if detection_window_s is None else float(detection_window_s)
    window = max(1.0, min(raw_window, 60.0))

    if resolved == "hangup":
        return VoicemailPlan(action="hangup", detection_window_s=window)

    if resolved == "static_message":
        spoken = substitute_dynamic_variables(message, variables).strip()
        hits = _pii_hits(spoken)
        if hits:
            return VoicemailPlan(
                action="hangup",
                detection_window_s=window,
                blocked_reason=f"static message contains {', '.join(hits)}",
            )
        spoken, trimmed = enforce_word_cap(spoken)
        return VoicemailPlan(
            action="static_message", message=spoken, detection_window_s=window, trimmed=trimmed
        )

    candidate = (generated_text or "").strip() or substitute_dynamic_variables(message, variables).strip()
    if not candidate:
        return VoicemailPlan(
            action="hangup", detection_window_s=window, blocked_reason="no message was generated"
        )
    hits = _pii_hits(candidate)
    if hits:
        return VoicemailPlan(
            action="hangup",
            detection_window_s=window,
            blocked_reason=f"generated message contains {', '.join(hits)}",
        )
    spoken, trimmed = enforce_word_cap(candidate)
    return VoicemailPlan(
        action="generated_message",
        message=spoken,
        detection_window_s=window,
        trimmed=trimmed,
        generated=True,
    )

