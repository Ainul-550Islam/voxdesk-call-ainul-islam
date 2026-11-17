"""Live-frame adapters for the PART 12 voice and call-control helpers.

The policy objects remain pure and unit-testable; these processors are the small
Pipecat boundary that applies them to a call without putting caller secrets in
transcripts or logs.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pipecat.frames.frames import (
    EndFrame,
    EndTaskFrame,
    Frame,
    InputDTMFFrame,
    InputTransportMessageFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.agent.language import LanguagePolicy, decide_language
from app.agent.tools.user_dtmf import DtmfPolicy, DtmfResult, UserDtmfCollector
from app.agent.tools.voicemail import VoicemailFlowHandler, plan_voicemail_action
from app.core.logging import log


class UserDtmfCollectionProcessor(FrameProcessor):
    """Collect caller keypad input and expose only a masked event to the LLM."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.config = dict(config or {})
        self.enabled = _as_bool(self.config.get("user_dtmf_enabled", False))
        self.policy = DtmfPolicy(
            mode=str(self.config.get("user_dtmf_mode") or "end_key"),
            end_key=str(self.config.get("user_dtmf_end_key") or "#").upper(),
            fixed_digits=_as_int(self.config.get("user_dtmf_fixed_digits"), 4),
            timeout_s=_as_float(self.config.get("user_dtmf_timeout_s"), 5.0),
            keep_last=_as_int(self.config.get("user_dtmf_keep_last"), 2),
            prompt=str(self.config.get("user_dtmf_prompt") or ""),
        )
        self.collector = UserDtmfCollector(self.policy)
        self.last_event: dict[str, Any] | None = None
        self.completed = False

    async def _emit_masked_result(self, result: DtmfResult, direction: FrameDirection) -> None:
        # The raw digits stay inside the collector. Only this redacted line is sent
        # into conversation context and is eligible for transcript persistence.
        event = result.to_event()
        self.last_event = event
        self.completed = True
        count = int(event["digit_count"])
        masked = str(event["digits_masked"] or "")
        if count:
            text = f"Caller entered keypad input {masked} ({count} digits)."
        else:
            text = "Caller ended keypad entry without entering digits."
        await self.push_frame(
            TranscriptionFrame(
                text=text,
                user_id="caller",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            direction,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not self.enabled:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputDTMFFrame):
            # Once a collection is complete, discard additional raw keypad frames rather than
            # forwarding them into a serializer or a later transcript/monitor processor.
            if self.completed:
                return
            button = getattr(frame, "button", None)
            digit = str(getattr(button, "value", button) or "").strip().upper()
            try:
                result = self.collector.feed(digit)
            except ValueError:
                # Do not include the invalid/raw keypad value in a log line.
                log.info("voice.dtmf.invalid_input")
                return
            if result is not None:
                await self._emit_masked_result(result, FrameDirection.DOWNSTREAM)
            # Keypad frames must not be interpreted as audio or leak into another
            # processor that may serialize their value.
            return

        if isinstance(frame, InputTransportMessageFrame):
            message = frame.message if isinstance(frame.message, dict) else {}
            if str(message.get("type") or "").strip().lower() == "dtmf":
                if self.completed:
                    return
                digits = str(message.get("digits") or message.get("digit") or "").strip().upper()
                for digit in digits:
                    try:
                        result = self.collector.feed(digit)
                    except ValueError:
                        # Do not include the invalid/raw keypad value in a log line.
                        log.info("voice.dtmf.invalid_input")
                        continue
                    if result is not None:
                        await self._emit_masked_result(result, FrameDirection.DOWNSTREAM)
                        break
                # Browser/WebRTC transports use a Protobuf transport-message frame rather
                # than InputDTMFFrame; consume it here so raw digits cannot enter context.
                return

        if self.completed:
            await self.push_frame(frame, direction)
            return

        if self.policy.mode == "timeout":
            result = self.collector.tick()
            if result is not None:
                await self._emit_masked_result(result, FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)


class LanguagePolicyGate(FrameProcessor):
    """Stop unsupported-language transcripts before they enter LLM context."""

    def __init__(self, policy: LanguagePolicy) -> None:
        super().__init__()
        self.policy = policy
        self.blocked_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and not isinstance(frame, InterimTranscriptionFrame)
            and getattr(frame, "language", None) is not None
        ):
            raw_language = getattr(frame, "language")
            detected = str(getattr(raw_language, "value", raw_language) or "").strip()
            decision = decide_language(detected, self.policy)
            if not decision.allowed:
                self.blocked_count += 1
                log.info("voice.language.blocked", blocked_count=self.blocked_count)
                await self.push_frame(
                    TTSSpeakFrame(text=decision.fallback_phrase or self.policy.fallback_phrase),
                    FrameDirection.DOWNSTREAM,
                )
                return
        await self.push_frame(frame, direction)


class CallAutomationProcessor(FrameProcessor):
    """Apply configured outbound call-screening and voicemail decisions to live transcripts."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        call: Any = None,
        call_sid: str = "",
        telephony_provider: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__()
        self.config = dict(config or {})
        self.call = call
        self.call_sid = call_sid or str(getattr(call, "call_sid", "") or "")
        self.telephony_provider = telephony_provider
        # FrameProcessor owns ``_clock`` for its Pipecat BaseClock; use a separate
        # name for the screening/voicemail policy's monotonic clock.
        self._monotonic_now = clock
        self._sleep = sleep
        self._started_at = clock()
        self._screening_timer_task: asyncio.Task[None] | None = None
        self._screening_enabled = _as_bool(self.config.get("call_screening_enabled", False))
        self._screening_message = str(self.config.get("call_screening_message") or "").strip()
        self._screening_wait_s = _as_float(self.config.get("call_screening_wait_s"), 20.0)
        self._screening_replies = 0
        self._screening_resolved = False
        self._voicemail_enabled = _as_bool(self.config.get("voicemail_detection_enabled", False))
        self._voicemail_action = str(self.config.get("voicemail_action") or "hangup")
        self._voicemail_message = str(self.config.get("voicemail_message") or "")
        self._voicemail_window_s = _as_float(self.config.get("voicemail_window_s"), 12.0)
        self.handled_action: dict[str, Any] | None = None

    def _cancel_screening_timer(self) -> None:
        task = self._screening_timer_task
        self._screening_timer_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _start_screening_timer(self) -> None:
        task = self._screening_timer_task
        if task is None or task.done():
            self._screening_timer_task = asyncio.create_task(self._expire_screening_wait())

    async def _expire_screening_wait(self) -> None:
        wait_s = max(1.0, min(self._screening_wait_s, 60.0))
        try:
            await self._sleep(wait_s)
        except asyncio.CancelledError:
            return
        if self._screening_resolved or self.handled_action is not None:
            return
        self._screening_resolved = True
        self.handled_action = {"action": "screening_timeout", "wait_s": wait_s}
        await self._hangup(reason="screening_timeout")

    async def cleanup(self) -> None:
        self._cancel_screening_timer()
        await super().cleanup()

    async def _hangup(self, *, reason: str) -> None:
        self._cancel_screening_timer()
        if self.call is not None:
            try:
                self.call.end_reason = reason
            except Exception:
                pass
        await self.push_frame(EndTaskFrame(), FrameDirection.DOWNSTREAM)
        await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        provider = self.telephony_provider
        if provider is not None and self.call_sid:
            try:
                await provider.hangup_call(self.call_sid)
            except Exception as exc:
                log.warning("voice.call_control.hangup_failed", error_type=type(exc).__name__)

    async def _handle_voicemail(self, text: str) -> bool:
        if not self._voicemail_enabled or self.handled_action is not None:
            return False
        if self._monotonic_now() - self._started_at > self._voicemail_window_s:
            return False
        if not VoicemailFlowHandler.is_voicemail_greeting(text):
            return False
        self._cancel_screening_timer()

        try:
            plan = plan_voicemail_action(
                action=self._voicemail_action,
                message=self._voicemail_message,
                detection_window_s=self._voicemail_window_s,
            )
        except (TypeError, ValueError):
            plan = plan_voicemail_action(action="hangup", detection_window_s=self._voicemail_window_s)
        if plan.action in {"static_message", "generated_message"} and plan.allowed:
            self.handled_action = {
                "action": plan.action,
                "message": plan.message,
                "blocked_reason": "",
            }
            try:
                self.call.end_reason = "voicemail_left"
            except Exception:
                pass
            await self.push_frame(TTSSpeakFrame(text=plan.message), FrameDirection.DOWNSTREAM)
            await self.push_frame(EndTaskFrame(), FrameDirection.DOWNSTREAM)
            await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
            if self.telephony_provider is not None and self.call_sid:
                try:
                    await self.telephony_provider.hangup_call(self.call_sid)
                except Exception as exc:
                    log.warning("voice.voicemail.hangup_failed", error_type=type(exc).__name__)
            log.info("voice.voicemail.message_queued", action=plan.action, trimmed=plan.trimmed)
        else:
            self.handled_action = {
                "action": "hangup",
                "message": "",
                "blocked_reason": plan.blocked_reason,
            }
            await self._hangup(reason="voicemail_hangup")
            log.info("voice.voicemail.hangup", blocked=bool(plan.blocked_reason))
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, (EndTaskFrame, EndFrame)):
            self._screening_resolved = True
            self._cancel_screening_timer()
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TranscriptionFrame)
            and not isinstance(frame, InterimTranscriptionFrame)
            and self.handled_action is None
        ):
            text = str(getattr(frame, "text", "") or "")
            if self._screening_enabled and not self._screening_resolved:
                from app.telephony.call_screening import ScreeningDetector, ScreeningScript, handle_screening

                result = ScreeningDetector().classify(text)
                if result.state == "screening":
                    script = ScreeningScript(
                        message=self._screening_message or "Hi, this is Ava calling about your appointment.",
                        wait_for_pickup_s=max(1.0, min(self._screening_wait_s, 60.0)),
                        repeat_limit=1,
                    )
                    action = handle_screening(
                        text,
                        detector=ScreeningDetector(),
                        script=script,
                        repeats_done=self._screening_replies,
                        voicemail_action=self._voicemail_action,
                    )
                    if action.get("action") == "speak":
                        self._screening_replies += 1
                        await self.push_frame(
                            TTSSpeakFrame(text=str(action.get("message") or script.message)),
                            FrameDirection.DOWNSTREAM,
                        )
                        self._start_screening_timer()
                    log.info("voice.call_screening.detected", platform=result.platform, action=action.get("action"))
                    return
                if result.state == "live_pickup":
                    self._screening_resolved = True
                    self._cancel_screening_timer()
                elif result.state == "voicemail":
                    self._screening_resolved = True
                    self._cancel_screening_timer()
                    if await self._handle_voicemail(text):
                        return

            if await self._handle_voicemail(text):
                return
        await self.push_frame(frame, direction)


class StructuredOutputProcessor(FrameProcessor):
    """Validate streamed JSON output before sending a speakable value to TTS."""

    FALLBACK_TEXT = "I'm sorry, I couldn't complete that response. Could you ask me another way?"
    SPEECH_KEYS = ("say", "speech", "response", "text", "message")

    def __init__(self, schema: dict[str, Any], *, strict: bool = True) -> None:
        super().__init__()
        self.schema = schema
        self.strict = bool(strict)
        self._collecting = False
        self._chunks: list[str] = []
        self.last_error: str = ""
        self.validated_count = 0

    def _to_speech(self, raw: str) -> str:
        from app.agent.providers.llm_providers import StructuredOutputError, parse_structured_response

        try:
            data = parse_structured_response(raw, self.schema)
        except StructuredOutputError as exc:
            self.last_error = str(exc)
            log.warning("voice.structured_output.invalid", reason=self.last_error[:120])
            return self.FALLBACK_TEXT
        self.last_error = ""
        self.validated_count += 1
        for key in self.SPEECH_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            self._collecting = True
            self._chunks = []
            await self.push_frame(frame, direction)
            return
        if self._collecting and isinstance(frame, TextFrame):
            self._chunks.append(str(frame.text or ""))
            return
        if self._collecting and isinstance(frame, LLMFullResponseEndFrame):
            self._collecting = False
            raw = "".join(self._chunks).strip()
            self._chunks = []
            if raw:
                await self.push_frame(TextFrame(text=self._to_speech(raw)), direction)
            await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
