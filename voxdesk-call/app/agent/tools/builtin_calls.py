"""Built-in telephony tools: `end_call` and `send_dtmf` (Sub-Phase 2D).

- `end_call`: Optionally speaks a farewell utterance (`TTSSpeakFrame`), pushes
  `EndTaskFrame` / `EndFrame` into the Pipecat pipeline, invokes
  `telephony_provider.hangup_call(call_sid)`, and sets `call.end_reason`.
- `send_dtmf`: Validates DTMF digits (`^[0-9*#wW]+$`), emits Pipecat
  `OutputDTMFFrame` frames for each keypad digit (`0-9`, `*`, `#`), and sends
  the full sequence (`0-9`, `*`, `#`, `w`, `W`) via `telephony_provider.send_dtmf`.
"""

from __future__ import annotations

import asyncio
import math
import re
import struct
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from pipecat.frames.frames import (
    EndFrame,
    EndTaskFrame,
    Frame,
    KeypadEntry,
    OutputAudioRawFrame,
    OutputDTMFFrame,
    OutputDTMFUrgentFrame,
    TTSSpeakFrame,
)

log = structlog.get_logger()

DTMF_PATTERN = re.compile(r"^[0-9*#wW]+$")
MAX_DTMF_DIGITS = 32
MAX_TOOL_CALLS_PER_TURN = 8

DTMF_FREQ_PAIRS: dict[str, tuple[float, float]] = {
    "1": (697.0, 1209.0),
    "2": (697.0, 1336.0),
    "3": (697.0, 1477.0),
    "4": (770.0, 1209.0),
    "5": (770.0, 1336.0),
    "6": (770.0, 1477.0),
    "7": (852.0, 1209.0),
    "8": (852.0, 1336.0),
    "9": (852.0, 1477.0),
    "*": (941.0, 1209.0),
    "0": (941.0, 1336.0),
    "#": (941.0, 1477.0),
}

END_CALL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": (
            "Politely end the active phone call once the caller's request is resolved, "
            "the caller asks to hang up, or the conversation has reached its natural conclusion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Structured hangup reason (e.g. 'completed', 'user_requested', 'unqualified', 'do_not_call').",
                },
                "farewell_message": {
                    "type": "string",
                    "description": "Optional brief closing sentence to speak before hanging up.",
                },
            },
            "required": ["reason"],
        },
    },
}

SEND_DTMF_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "send_dtmf",
        "description": (
            "Send touch-tone DTMF keypad digits on the active phone call to navigate IVR menus "
            "or enter extension/PIN numbers. Allowed characters: 0-9, *, #, w (0.5s pause), W (1.0s pause)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "digits": {
                    "type": "string",
                    "description": "DTMF digit string matching ^[0-9*#wW]+$, e.g. '1', 'w2#', or '104#'.",
                },
            },
            "required": ["digits"],
        },
    },
}


def synthesize_dtmf_pcm(
    digit: str,
    *,
    duration_ms: int = 160,
    sample_rate: int = 8000,
    amplitude: float = 0.35,
) -> bytes:
    """Synthesize 16-bit mono PCM ITU-T Q.23 dual-tone audio for carriers whose WS transport drops DTMF frames."""
    freqs = DTMF_FREQ_PAIRS.get(digit)
    if freqs is None:
        raise ValueError(f"Unsupported DTMF tone digit {digit!r}")
    f_low, f_high = freqs
    num_samples = max(1, int(sample_rate * (duration_ms / 1000.0)))
    scale = max(0.05, min(0.95, amplitude)) * 32767.0 * 0.5
    samples = bytearray(num_samples * 2)
    for i in range(num_samples):
        t = i / float(sample_rate)
        val = int(
            scale * (math.sin(2.0 * math.pi * f_low * t) + math.sin(2.0 * math.pi * f_high * t))
        )
        struct.pack_into("<h", samples, i * 2, max(-32768, min(32767, val)))
    return bytes(samples)


def enforce_tool_call_ceiling(
    current_count: int,
    *,
    limit: int = MAX_TOOL_CALLS_PER_TURN,
) -> None:
    """Raise `RuntimeError` if a single call/turn exceeds the tool invocation ceiling."""
    if current_count >= limit:
        raise RuntimeError(
            f"Tool call ceiling exceeded ({current_count} >= {limit})"
        )


def validate_dtmf_digits(digits: str, *, max_length: int = MAX_DTMF_DIGITS) -> str:
    """Validate a DTMF digit sequence against `^[0-9*#wW]+$` and `max_length`."""
    cleaned = (digits or "").strip()
    if len(cleaned) > max_length:
        raise ValueError(
            f"DTMF sequence length ({len(cleaned)}) exceeds maximum ({max_length})."
        )
    if not cleaned or not DTMF_PATTERN.fullmatch(cleaned):
        raise ValueError(
            f"Invalid DTMF sequence {digits!r}. Allowed characters are 0-9, *, #, w, W."
        )
    return cleaned


async def execute_end_call(
    *,
    reason: str = "completed",
    farewell_message: str | None = None,
    call: Any | None = None,
    call_sid: str | None = None,
    telephony_provider: Any | None = None,
    frame_pusher: Callable[[Frame], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Execute the `end_call` tool: push farewell + EndFrame and hang up the carrier leg."""
    resolved_reason = (reason or "completed").strip()[:64] or "completed"
    resolved_sid = call_sid or getattr(call, "call_sid", None) or ""

    if call is not None:
        if hasattr(call, "end_reason"):
            call.end_reason = resolved_reason

    frames_emitted: list[str] = []
    if frame_pusher is not None:
        if farewell_message and farewell_message.strip():
            await frame_pusher(TTSSpeakFrame(text=farewell_message.strip()))
            frames_emitted.append("TTSSpeakFrame")
        await frame_pusher(EndTaskFrame())
        frames_emitted.append("EndTaskFrame")
        await frame_pusher(EndFrame())
        frames_emitted.append("EndFrame")

    provider_result: dict[str, Any] | None = None
    if telephony_provider is not None and resolved_sid:
        try:
            res = await telephony_provider.hangup_call(resolved_sid)
            provider_result = res.as_dict() if hasattr(res, "as_dict") else dict(res)
        except Exception as exc:
            log.warning(
                "tool.end_call.provider_hangup_failed",
                call_sid=resolved_sid,
                error=str(exc)[:160],
            )
            provider_result = {"status": "error", "error": str(exc)[:160]}

    log.info(
        "tool.end_call.executed",
        call_sid=resolved_sid,
        reason=resolved_reason,
        frames_emitted=frames_emitted,
    )
    return {
        "ok": True,
        "action": "end_call",
        "reason": resolved_reason,
        "call_sid": resolved_sid,
        "frames_emitted": frames_emitted,
        "provider_result": provider_result,
    }


async def execute_send_dtmf(
    *,
    digits: str,
    call: Any | None = None,
    call_sid: str | None = None,
    telephony_provider: Any | None = None,
    frame_pusher: Callable[[Frame], Awaitable[None]] | None = None,
    honor_pauses: bool = False,
    urgent: bool = False,
    synthesize_audio: bool = False,
    sample_rate: int = 8000,
) -> dict[str, Any]:
    """Execute the `send_dtmf` tool: emit `OutputDTMFFrame` / `OutputDTMFUrgentFrame` frames and call `telephony_provider.send_dtmf`."""
    validated = validate_dtmf_digits(digits)
    resolved_sid = call_sid or getattr(call, "call_sid", None) or ""

    emitted_buttons: list[str] = []
    pcm_bytes_emitted = 0
    if frame_pusher is not None:
        for ch in validated:
            if ch == "w":
                if honor_pauses:
                    await asyncio.sleep(0.5)
                continue
            if ch == "W":
                if honor_pauses:
                    await asyncio.sleep(1.0)
                continue
            entry = KeypadEntry(ch)
            dtmf_frame = OutputDTMFUrgentFrame(button=entry) if urgent else OutputDTMFFrame(button=entry)
            await frame_pusher(dtmf_frame)
            if synthesize_audio:
                pcm = synthesize_dtmf_pcm(ch, sample_rate=sample_rate)
                pcm_bytes_emitted += len(pcm)
                await frame_pusher(
                    OutputAudioRawFrame(audio=pcm, sample_rate=sample_rate, num_channels=1)
                )
            emitted_buttons.append(ch)

    provider_result: dict[str, Any] | None = None
    if telephony_provider is not None and resolved_sid:
        res = await telephony_provider.send_dtmf(resolved_sid, validated)
        provider_result = res if isinstance(res, dict) else {"status": "sent"}

    log.info(
        "tool.send_dtmf.executed",
        call_sid=resolved_sid,
        digits=validated,
        emitted_buttons=emitted_buttons,
        pcm_bytes_emitted=pcm_bytes_emitted,
    )
    return {
        "ok": True,
        "action": "send_dtmf",
        "digits": validated,
        "emitted_buttons": emitted_buttons,
        "pcm_bytes_emitted": pcm_bytes_emitted,
        "call_sid": resolved_sid,
        "provider_result": provider_result,
    }
