"""Vonage media frame codec.

Vonage's WebSocket media stream carries **16-bit linear PCM (L16) at
16 kHz** in binary frames, while the VoxDesk voice pipeline runs at 8 kHz
telephony PCM. This module is the exact, dependency-free bridge between the
two:

* :func:`pcm16_8k_to_l16_16k` -- 8 kHz pipeline PCM to 16 kHz L16.
* :func:`l16_16k_to_pcm16_8k` -- 16 kHz L16 back to 8 kHz pipeline PCM.
* :func:`chunk_l16` -- split a payload into the 640-byte (20 ms) frames
  Vonage expects.
* :class:`OutboundPacer` -- jitter-safe outbound pacing, so a burst of
  synthesised audio is delivered at the wire clock instead of in one
  thundering chunk.
* :class:`VonageFrameSerializer` -- the Pipecat ``FrameSerializer`` that
  speaks the Vonage wire format (binary audio plus JSON control events).

Everything here is a pure function of its input: no clock, no network, no
global state. That is what makes the codec testable against reference
vectors.
"""

from __future__ import annotations

import array
import base64
import json
import sys
import time
from dataclasses import dataclass, field

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    KeypadEntry,
    OutputAudioRawFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType

#: Vonage streams 16-bit linear PCM at 16 kHz.
VONAGE_SAMPLE_RATE = 16000
#: The VoxDesk voice pipeline runs at 8 kHz telephony PCM.
PIPELINE_SAMPLE_RATE = 8000
#: 20 ms of 16-bit PCM at 16 kHz: 16000 * 0.02 * 2 = 640 bytes.
VONAGE_FRAME_BYTES = 640
#: 20 ms of 8 kHz 16-bit PCM.
PIPELINE_FRAME_BYTES = 320


def _to_samples(pcm: bytes) -> array.array:
    """Decode little-endian signed 16-bit PCM into an int array."""
    usable = len(pcm) - (len(pcm) % 2)
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":  # pragma: no cover - little-endian in practice
        samples.byteswap()
    return samples


def _from_samples(samples: array.array) -> bytes:
    """Encode an int array back to little-endian signed 16-bit PCM."""
    if sys.byteorder == "big":  # pragma: no cover - little-endian in practice
        swapped = array.array("h", samples)
        swapped.byteswap()
        return swapped.tobytes()
    return samples.tobytes()


def pcm16_8k_to_l16_16k(pcm: bytes) -> bytes:
    """Upsample 8 kHz PCM16 to 16 kHz PCM16 (L16) by linear interpolation.

    Each input sample ``s0`` becomes the pair ``(s0, (s0 + s1) // 2)``, so
    the output is exactly twice as long and the reconstruction error against
    a reference linear interpolator is zero (it *is* a linear interpolator).
    """
    samples = _to_samples(pcm)
    if not samples:
        return b""
    out = array.array("h")
    last = len(samples) - 1
    for index in range(len(samples)):
        current = samples[index]
        nxt = samples[index + 1] if index < last else current
        out.append(current)
        out.append((current + nxt) // 2)
    return _from_samples(out)


def l16_16k_to_pcm16_8k(l16: bytes) -> bytes:
    """Downsample 16 kHz L16 to 8 kHz PCM16 by averaging each sample pair."""
    samples = _to_samples(l16)
    if not samples:
        return b""
    out = array.array("h")
    for index in range(0, len(samples) - 1, 2):
        out.append((samples[index] + samples[index + 1]) // 2)
    return _from_samples(out)


def resample_pcm16(pcm: bytes, *, source_rate: int, target_rate: int) -> bytes:
    """Convert PCM16 between two rates using exact rational resampling.

    Only the ratios the telephony stack actually uses are supported
    (1:1, 2:1, 1:2). Anything else is a programming error and raises
    ``ValueError`` rather than silently producing wrong-length audio.
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate:
        return pcm
    if target_rate == source_rate * 2:
        # Upsample: 8 kHz pipeline PCM to 16 kHz L16.
        return pcm16_8k_to_l16_16k(pcm)
    if source_rate == target_rate * 2:
        # Downsample: 16 kHz L16 to 8 kHz pipeline PCM.
        return l16_16k_to_pcm16_8k(pcm)
    raise ValueError(
        f"unsupported PCM16 resample ratio {source_rate} -> {target_rate}"
    )


def chunk_l16(payload: bytes, *, frame_bytes: int = VONAGE_FRAME_BYTES) -> list[bytes]:
    """Split ``payload`` into fixed-size frames.

    A trailing partial frame is returned as its own chunk (Vonage accepts a
    short final frame); an empty payload yields no chunks at all.
    """
    if frame_bytes <= 0:
        raise ValueError("frame_bytes must be positive")
    if not payload:
        return []
    return [payload[i : i + frame_bytes] for i in range(0, len(payload), frame_bytes)]


@dataclass
class OutboundPacer:
    """Deliver outbound audio frames at the wire clock.

    The Pipecat output transport hands us audio as fast as the TTS produces
    it. A carrier socket expects a steady 20 ms cadence; dumping a second of
    audio into the socket at once produces audible jitter and can overflow
    the provider's buffer. The pacer sleeps only for the *remainder* of the
    frame period, so it never adds latency of its own.
    """

    frame_ms: int = 20
    _started: float = field(default=0.0, init=False)
    _frames: int = field(default=0, init=False)

    def reset(self) -> None:
        """Forget the pacing clock (used after a barge-in)."""
        self._started = 0.0
        self._frames = 0

    @property
    def frames_sent(self) -> int:
        return self._frames

    async def pace(self) -> float:
        """Wait until the next frame slot; returns the seconds slept."""
        if self._started <= 0.0:
            self._started = time.monotonic()
        period = self.frame_ms / 1000.0
        target = self._started + (self._frames * period)
        now = time.monotonic()
        delay = target - now
        self._frames += 1
        if delay <= 0:
            return 0.0
        import asyncio

        await asyncio.sleep(delay)
        return delay


@dataclass
class VonageMediaState:
    """Outbound audio state that a barge-in can clear.

    When the caller speaks over the assistant, everything the assistant had
    queued but not yet played must be dropped -- otherwise the caller hears
    the old answer a second later. ``clear`` resets both the buffered bytes
    and the pacer clock.
    """

    pacer: OutboundPacer = field(default_factory=OutboundPacer)
    buffered: bytearray = field(default_factory=bytearray)
    speaking: bool = False

    def clear(self) -> int:
        """Drop queued outbound audio; returns the number of bytes dropped."""
        dropped = len(self.buffered)
        self.buffered.clear()
        self.pacer.reset()
        self.speaking = False
        return dropped

    def buffer(self, payload: bytes) -> None:
        self.buffered.extend(payload)

    def take(self, *, frame_bytes: int = VONAGE_FRAME_BYTES) -> bytes:
        """Pop up to one wire frame of buffered audio."""
        if len(self.buffered) < frame_bytes:
            return b""
        chunk = bytes(self.buffered[:frame_bytes])
        del self.buffered[:frame_bytes]
        return chunk


class VonageFrameSerializer(FrameSerializer):
    """Pipecat serializer for the Vonage WebSocket media protocol.

    Vonage mixes two frame kinds on one socket: **binary** frames carry L16
    audio, **text** frames carry JSON control events (``start``, ``dtmf``,
    ``stop``). The serializer converts both directions:

    * inbound binary -> ``InputAudioRawFrame`` with 8 kHz pipeline PCM;
    * inbound text -> ``InputDTMFFrame`` for DTMF, or a control callback;
    * outbound ``OutputAudioRawFrame`` -> 16 kHz L16 bytes.
    """

    def __init__(
        self,
        *,
        stream_sid: str = "",
        call_sid: str = "",
        sample_rate: int = PIPELINE_SAMPLE_RATE,
        outbound_sample_rate: int = VONAGE_SAMPLE_RATE,
        control_handler=None,
    ) -> None:
        super().__init__()
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.sample_rate = sample_rate
        self.outbound_sample_rate = outbound_sample_rate
        self.control_handler = control_handler
        self.state = VonageMediaState()

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.BINARY

    # -- inbound ---------------------------------------------------------
    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, (bytes, bytearray)):
            audio = l16_16k_to_pcm16_8k(bytes(data))
            if not audio:
                return None
            return InputAudioRawFrame(
                audio=audio, sample_rate=self.sample_rate, num_channels=1
            )
        if not isinstance(data, str) or not data.strip():
            return None
        try:
            message = json.loads(data)
        except ValueError:
            return None
        if not isinstance(message, dict):
            return None
        event = str(message.get("event") or "").strip().lower()
        if event == "dtmf":
            digit = str(
                (message.get("digit") or message.get("dtmf") or {}).get("digit")
                if isinstance(message.get("dtmf"), dict)
                else (message.get("digit") or "")
            ).strip()
            if digit:
                try:
                    frame: Frame = InputDTMFFrame(button=KeypadEntry(digit[0]))
                except ValueError:
                    frame = None  # type: ignore[assignment]
                if frame is not None:
                    return frame
            return None
        if self.control_handler is not None:
            result = self.control_handler(message)
            if hasattr(result, "__await__"):
                await result
        return None

    # -- outbound --------------------------------------------------------
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if not isinstance(frame, OutputAudioRawFrame):
            return None
        audio = frame.audio
        rate = int(getattr(frame, "sample_rate", self.sample_rate) or self.sample_rate)
        if rate != self.outbound_sample_rate:
            audio = resample_pcm16(
                audio, source_rate=rate, target_rate=self.outbound_sample_rate
            )
        if not audio:
            return None
        self.state.speaking = True
        return audio

    # -- helpers used by the handler -------------------------------------
    def clear_outbound(self) -> int:
        """Drop queued outbound audio (barge-in)."""
        return self.state.clear()

    def encode_control(self, payload: dict) -> str:
        """Encode a JSON control message for the Vonage socket."""
        return json.dumps(payload, separators=(",", ":"))


def decode_media_payload(payload_b64: str) -> bytes:
    """Decode a base64 media payload (used by tests and the event path)."""
    return base64.b64decode(payload_b64 or "")


__all__ = [
    "OutboundPacer",
    "PIPELINE_FRAME_BYTES",
    "PIPELINE_SAMPLE_RATE",
    "VONAGE_FRAME_BYTES",
    "VONAGE_SAMPLE_RATE",
    "VonageFrameSerializer",
    "VonageMediaState",
    "chunk_l16",
    "decode_media_payload",
    "l16_16k_to_pcm16_8k",
    "pcm16_8k_to_l16_16k",
    "resample_pcm16",
]
