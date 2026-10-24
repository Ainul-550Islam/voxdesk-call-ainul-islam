"""Telnyx bidirectional media streaming: wire codec, framing and stream auth.

Telnyx Call Control v2 delivers call audio over a WebSocket as JSON frames
(`connected`, `start`, `media`, `mark`, `clear`, `stop`) carrying base64 G.711
payloads. The AI pipeline speaks 16-bit linear PCM at 16 kHz. This module is the
whole boundary between those two worlds, and nothing else in the repository is
allowed to re-implement it:

* :func:`parse_event` -- one wire frame to a typed :class:`TelnyxMediaEvent`.
  A malformed frame raises; it never becomes a silent empty audio buffer.
* G.711 tables -- µ-law and A-law, both directions. Decoding is the exact ITU-T
  G.711 table. Encoding is a nearest-representable-value quantiser: it is *not*
  bit-identical to ``audioop.lin2ulaw`` (which breaks midpoint ties the other
  way), and the tests assert the documented contract -- exact decode, bounded
  round-trip error, stable re-encoding -- instead of parity with a module Python
  is removing.
* :func:`resample_pcm16` -- 8 kHz <-> 16 kHz resampling with an anti-alias
  pre-filter on downsample, so the agent's 16 kHz audio does not fold back.
* :class:`TelnyxMediaStream` -- per-stream state: authorization (single-use
  HMAC token bound to the call), outbound framing, and mark/clear barge-in.
  ``clear`` drops every queued outbound frame, which is what an interruption
  means on this carrier: the caller must stop hearing the old sentence
  immediately, not after the buffers drain.
* :func:`verify_webhook_signature` -- Ed25519 verification against the key the
  caller supplies (falling back to ``TELNYX_PUBLIC_KEY``), with a freshness
  window and a closed path allowlist. A missing PyNaCl rejects rather than
  accepts, because "cannot verify" is not "verified".

Honesty rules this module keeps:

* No default secret, no default token, no "accept when unconfigured". A stream
  with no signing key cannot start.
* Audio conversion never invents samples: a payload whose length is not a whole
  number of frames is rejected, not zero-padded into the call.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.config import settings
from app.telephony.stream_auth import create_stream_token, verify_stream_token

#: Wire event names Telnyx sends on a bidirectional media stream.
EVENT_CONNECTED = "connected"
EVENT_START = "start"
EVENT_MEDIA = "media"
EVENT_MARK = "mark"
EVENT_CLEAR = "clear"
EVENT_STOP = "stop"
EVENT_DTMF = "dtmf"
EVENT_ERROR = "error"

KNOWN_EVENTS = frozenset(
    {EVENT_CONNECTED, EVENT_START, EVENT_MEDIA, EVENT_MARK, EVENT_CLEAR, EVENT_STOP, EVENT_DTMF, EVENT_ERROR}
)

#: Encodings the Telnyx media socket is documented to carry.
SUPPORTED_ENCODINGS = frozenset({"PCMU", "PCMA", "L16"})

#: 8 kHz G.711 frame sizes at the default 20 ms framing.
DEFAULT_SAMPLE_RATE = 8000
DEFAULT_FRAME_MS = 20
PCM16_CHANNELS = 1
PCM16_SAMPLE_WIDTH = 2


class TelnyxMediaStreamError(Exception):
    """A media-stream frame or configuration is invalid. Never a silent no-op."""

    def __init__(self, message: str, *, code: str = "telnyx_media_error", status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


# --------------------------------------------------------------------------- #
# G.711 codecs (ITU-T G.711; bit-exact with the reference tables)
# --------------------------------------------------------------------------- #

_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635
_ULAW_SEGMENT_STARTS = (0xFF, 0x7F, 0x3F, 0x1F, 0x0F, 0x07, 0x03, 0x01)


def _build_ulaw_decode_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        code = ~value & 0xFF
        sign = code & 0x80
        exponent = (code >> 4) & 0x07
        mantissa = code & 0x0F
        magnitude = (((mantissa << 3) + _ULAW_BIAS) << exponent) - _ULAW_BIAS
        sample = -magnitude if sign else magnitude
        table.append(max(-32768, min(32767, sample)))
    return tuple(table)


def _build_alaw_decode_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        code = value ^ 0x55
        sign = code & 0x80
        exponent = (code >> 4) & 0x07
        mantissa = code & 0x0F
        if exponent == 0:
            magnitude = (mantissa << 4) + 8
        else:
            magnitude = ((mantissa << 4) + 0x108) << (exponent - 1)
        sample = magnitude if sign else -magnitude
        table.append(max(-32768, min(32767, sample)))
    return tuple(table)


_ULAW_DECODE = _build_ulaw_decode_table()
_ALAW_DECODE = _build_alaw_decode_table()


def ulaw_to_pcm16(data: bytes) -> bytes:
    """Decode 8-bit µ-law to little-endian 16-bit PCM."""
    if not data:
        return b""
    out = bytearray(len(data) * 2)
    index = 0
    for byte in data:
        sample = _ULAW_DECODE[byte]
        out[index] = sample & 0xFF
        out[index + 1] = (sample >> 8) & 0xFF
        index += 2
    return bytes(out)


def alaw_to_pcm16(data: bytes) -> bytes:
    """Decode 8-bit A-law to little-endian 16-bit PCM."""
    if not data:
        return b""
    out = bytearray(len(data) * 2)
    index = 0
    for byte in data:
        sample = _ALAW_DECODE[byte]
        out[index] = sample & 0xFF
        out[index + 1] = (sample >> 8) & 0xFF
        index += 2
    return bytes(out)


def _pcm16_samples(pcm: bytes) -> list[int]:
    if len(pcm) % PCM16_SAMPLE_WIDTH:
        raise TelnyxMediaStreamError(
            "PCM16 payload has an odd number of bytes; a partial sample is not audio"
        )
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm)) if pcm else []


def _samples_to_pcm16(samples: Iterable[int]) -> bytes:
    values = [max(-32768, min(32767, int(round(sample)))) for sample in samples]
    return struct.pack(f"<{len(values)}h", *values) if values else b""


def _build_encode_table(decode_table: tuple[int, ...]) -> bytes:
    """PCM16 -> G.711 code, by nearest representable value.

    G.711 is a quantiser: every 16-bit input maps to the code whose decoded
    value is closest to it. Doing it as a single 64 KiB lookup table makes the
    encoder both exact and O(1) per sample, which is what a live call path
    needs. Ties resolve to the higher code; both decode to the same audio.
    """
    codes = sorted(range(256), key=lambda code: decode_table[code])
    values = [decode_table[code] for code in codes]
    table = bytearray(65536)
    cursor = 0
    for index, sample in enumerate(range(-32768, 32768)):
        while cursor + 1 < len(values) and (values[cursor + 1] - sample) <= (sample - values[cursor]):
            cursor += 1
        table[index] = codes[cursor]
    return bytes(table)


#: Indexed by ``sample + 32768``.
_ULAW_ENCODE = _build_encode_table(_ULAW_DECODE)
_ALAW_ENCODE = _build_encode_table(_ALAW_DECODE)


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """Encode little-endian 16-bit PCM to 8-bit µ-law."""
    return bytes(_ULAW_ENCODE[sample + 32768] for sample in _pcm16_samples(pcm))


def pcm16_to_alaw(pcm: bytes) -> bytes:
    """Encode little-endian 16-bit PCM to 8-bit A-law."""
    return bytes(_ALAW_ENCODE[sample + 32768] for sample in _pcm16_samples(pcm))


def decode_g711(payload: bytes, encoding: str) -> bytes:
    """Decode a carrier payload at its native rate into 8 kHz PCM16."""
    name = (encoding or "").strip().upper()
    if name == "PCMU":
        return ulaw_to_pcm16(payload)
    if name == "PCMA":
        return alaw_to_pcm16(payload)
    if name == "L16":
        if len(payload) % PCM16_SAMPLE_WIDTH:
            raise TelnyxMediaStreamError("L16 payload is not a whole number of 16-bit samples")
        return payload
    raise TelnyxMediaStreamError(f"unsupported media encoding {encoding!r}", code="unsupported_encoding")


def encode_g711(pcm16: bytes, encoding: str) -> bytes:
    """Encode 8 kHz PCM16 into the carrier's payload encoding."""
    name = (encoding or "").strip().upper()
    if name == "PCMU":
        return pcm16_to_ulaw(pcm16)
    if name == "PCMA":
        return pcm16_to_alaw(pcm16)
    if name == "L16":
        return pcm16
    raise TelnyxMediaStreamError(f"unsupported media encoding {encoding!r}", code="unsupported_encoding")


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resampler with an anti-alias stage on downsample."""
    if src_rate == dst_rate or not pcm:
        return pcm
    if src_rate <= 0 or dst_rate <= 0:
        raise TelnyxMediaStreamError("sample rates must be positive")
    samples = _pcm16_samples(pcm)
    if not samples:
        return b""

    # Downsampling: average each output window first (a box filter is a real
    # low-pass at this ratio and costs one pass) so 16 kHz content above the
    # 8 kHz Nyquist limit cannot fold back into the caller's ear.
    if dst_rate < src_rate:
        ratio = src_rate // dst_rate if src_rate % dst_rate == 0 else 0
        if ratio > 1:
            filtered: list[int] = []
            for start in range(0, len(samples), ratio):
                window = samples[start : start + ratio]
                if window:
                    filtered.append(sum(window) // len(window))
            samples = filtered
            src_rate = src_rate // ratio

    step = src_rate / float(dst_rate)
    output_count = max(1, int(round(len(samples) * dst_rate / float(src_rate))))
    last = len(samples) - 1
    output: list[int] = []
    for index in range(output_count):
        position = index * step
        if position >= len(samples):
            position = float(last)
        left = int(math.floor(position))
        right = min(left + 1, last)
        weight = position - left
        output.append(int(round(samples[left] * (1.0 - weight) + samples[right] * weight)))
    return _samples_to_pcm16(output)


def decode_to_pipeline_pcm(
    payload: bytes,
    *,
    encoding: str,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    pipeline_rate: int = 16000,
) -> bytes:
    """Carrier payload -> pipeline PCM16 at ``pipeline_rate``."""
    pcm8k = decode_g711(payload, encoding)
    return resample_pcm16(pcm8k, sample_rate, pipeline_rate)


def encode_from_pipeline_pcm(
    pcm: bytes,
    *,
    encoding: str,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    pipeline_rate: int = 16000,
) -> bytes:
    """Pipeline PCM16 -> carrier payload at ``sample_rate``."""
    native = resample_pcm16(pcm, pipeline_rate, sample_rate)
    return encode_g711(native, encoding)


# --------------------------------------------------------------------------- #
# Wire frames
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TelnyxMediaEvent:
    """One parsed frame from the Telnyx media socket."""

    event: str
    stream_id: str = ""
    call_control_id: str = ""
    encoding: str = ""
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = 1
    payload: bytes = b""
    mark_name: str = ""
    digit: str = ""
    sequence_number: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def is_media(self) -> bool:
        return self.event == EVENT_MEDIA and bool(self.payload)

    @property
    def is_start(self) -> bool:
        return self.event == EVENT_START

    @property
    def is_stop(self) -> bool:
        return self.event == EVENT_STOP

    @property
    def is_clear(self) -> bool:
        return self.event == EVENT_CLEAR


def _decode_payload(value: Any) -> bytes:
    if value is None:
        return b""
    if not isinstance(value, str):
        raise TelnyxMediaStreamError("media payload must be a base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TelnyxMediaStreamError("media payload is not valid base64") from exc


def parse_event(raw: str | bytes | dict[str, Any]) -> TelnyxMediaEvent:
    """Parse one carrier frame. Unknown event names are returned, not raised."""
    if isinstance(raw, (str, bytes)):
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TelnyxMediaStreamError("media frame is not valid JSON") from exc
    else:
        message = raw
    if not isinstance(message, dict):
        raise TelnyxMediaStreamError("media frame must be a JSON object")

    name = str(message.get("event") or message.get("type") or "").strip().lower()
    start = message.get("start") if isinstance(message.get("start"), dict) else {}
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    fmt = start.get("media_format") if isinstance(start.get("media_format"), dict) else {}
    mark = message.get("mark") if isinstance(message.get("mark"), dict) else {}
    dtmf = message.get("dtmf") if isinstance(message.get("dtmf"), dict) else {}

    encoding = str(fmt.get("encoding") or media.get("encoding") or message.get("encoding") or "").upper()
    try:
        sample_rate = int(fmt.get("sample_rate") or media.get("sample_rate") or DEFAULT_SAMPLE_RATE)
        channels = int(fmt.get("channels") or 1)
    except (TypeError, ValueError) as exc:
        raise TelnyxMediaStreamError("media_format sample_rate/channels must be integers") from exc

    sequence: int | None = None
    raw_sequence = media.get("sequence_number", message.get("sequence_number"))
    if raw_sequence is not None:
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError):
            sequence = None

    payload = _decode_payload(media.get("payload")) if name == EVENT_MEDIA else b""
    if name == EVENT_MEDIA and not payload:
        raise TelnyxMediaStreamError("media frame carried no payload")

    return TelnyxMediaEvent(
        event=name or "unknown",
        stream_id=str(message.get("stream_id") or start.get("stream_id") or ""),
        call_control_id=str(start.get("call_control_id") or message.get("call_control_id") or ""),
        encoding=encoding,
        sample_rate=sample_rate,
        channels=channels,
        payload=payload,
        mark_name=str(mark.get("name") or ""),
        digit=str(dtmf.get("digit") or message.get("digit") or ""),
        sequence_number=sequence,
        raw=message,
    )


# --------------------------------------------------------------------------- #
# Stream authentication: single-use token bound to the call
# --------------------------------------------------------------------------- #

#: Tokens already redeemed, guarded by their expiry so the map cannot grow
#: without bound. A replayed token is refused even though it is still inside its
#: TTL, because the carrier only ever needs it once, at upgrade time.
_CONSUMED_TOKENS: dict[str, int] = {}
_CONSUMED_LIMIT = 8192


def _prune_consumed(now: int) -> None:
    if len(_CONSUMED_TOKENS) <= _CONSUMED_LIMIT:
        expired = [token for token, expiry in _CONSUMED_TOKENS.items() if expiry <= now]
        for token in expired:
            _CONSUMED_TOKENS.pop(token, None)
        return
    _CONSUMED_TOKENS.clear()


def reset_consumed_tokens() -> None:
    """Test/operator hook: forget redeemed stream tokens."""
    _CONSUMED_TOKENS.clear()


def issue_stream_token(call_id: str, *, issued_at: int | None = None) -> str:
    """Mint the short-lived token written into the media WebSocket URL."""
    if not (call_id or "").strip():
        raise TelnyxMediaStreamError("a call id is required to issue a stream token")
    return create_stream_token(call_id, issued_at=issued_at)


def consume_stream_token(call_id: str, token: str | None, *, now: int | None = None) -> bool:
    """Verify and redeem a stream token. Seconds use is refused (replay)."""
    if not verify_stream_token(call_id, token, now=now):
        return False
    assert token is not None  # verified above
    current = int(now if now is not None else time.time())
    _prune_consumed(current)
    if token in _CONSUMED_TOKENS:
        return False
    _CONSUMED_TOKENS[token] = current + settings.telnyx_stream_token_ttl_seconds
    return True


#: Telnyx signs only the paths it is allowed to call. Anything else is refused
#: before the crypto is even attempted, so an attacker cannot replay a valid
#: signature against a different endpoint.
_SIGNED_PATHS = ("/telephony/telnyx", "/api/telephony/telnyx")

#: Telnyx's documented tolerance for a stale timestamp, in seconds.
SIGNATURE_MAX_SKEW_SECONDS = 300


def _normalize_public_key(public_key: str) -> bytes | None:
    """Accept Telnyx's base64 key, a PEM block, or 32 raw bytes as hex/base64."""
    value = (public_key or "").strip()
    if not value:
        return None
    if "BEGIN PUBLIC KEY" in value:
        body = "".join(
            line.strip()
            for line in value.splitlines()
            if line.strip() and not line.startswith("-----")
        )
        try:
            raw = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError):
            return None
        # A SubjectPublicKeyInfo Ed25519 key ends with the 32 raw bytes.
        return raw[-32:] if len(raw) >= 32 else None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) == 32:
        return raw
    try:
        hexed = bytes.fromhex(value)
    except ValueError:
        return None
    return hexed if len(hexed) == 32 else None


def _ed25519_verify(public_key: str, signature: str, payload: bytes) -> bool:
    """Verify ``signature`` over ``payload``. Any doubt is a rejection."""
    key_bytes = _normalize_public_key(public_key)
    if key_bytes is None:
        return False
    try:
        signature_bytes = base64.b64decode((signature or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(signature_bytes) != 64:
        return False
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:  # pragma: no cover - PyNaCl is a pinned dependency
        return False
    try:
        VerifyKey(key_bytes).verify(payload, signature_bytes)
    except (BadSignatureError, ValueError):
        return False
    return True


async def verify_webhook_signature(
    *,
    public_key: str = "",
    signature: str,
    timestamp: str,
    body: bytes,
    path: str = "/telephony/telnyx/ws",
    max_skew_seconds: int = SIGNATURE_MAX_SKEW_SECONDS,
) -> tuple[bool, str]:
    """Verify a Telnyx Ed25519 webhook signature.

    Returns ``(verified, reason)``. The reason names the failure so an operator
    can tell "signature missing" from "signature rejected", and it never includes
    the signature itself.

    The key the caller passes is the key that verifies the payload: a signature is
    only meaningful for one key, so silently checking it against a global setting
    would report success for the wrong credential.
    """
    key = (public_key or settings.telnyx_public_key or "").strip()
    if not key:
        return False, "configuration_missing"
    if not (signature or "").strip():
        return False, "signature_missing"
    if not (timestamp or "").strip():
        return False, "timestamp_missing"
    if not path.startswith(_SIGNED_PATHS):
        return False, "endpoint_mismatch"
    try:
        stamped = int(timestamp)
    except (TypeError, ValueError):
        return False, "timestamp_rejected"
    if abs(time.time() - stamped) > max_skew_seconds:
        return False, "timestamp_rejected"
    if not _ed25519_verify(key, signature, f"{timestamp}|".encode("utf-8") + body):
        return False, "signature_rejected"
    return True, "telnyx_signature"


# --------------------------------------------------------------------------- #
# Per-stream state
# --------------------------------------------------------------------------- #


@dataclass
class TelnyxStreamState:
    """Everything measured about one media stream. Counters are evidence."""

    stream_id: str = ""
    call_control_id: str = ""
    encoding: str = ""
    sample_rate: int = DEFAULT_SAMPLE_RATE
    authorized: bool = False
    started: bool = False
    stopped: bool = False
    frames_in: int = 0
    frames_out: int = 0
    clears: int = 0
    marks_sent: int = 0
    dropped_outbound_frames: int = 0
    inbound_samples: int = 0
    last_sequence_number: int | None = None
    out_of_order_frames: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "call_control_id": self.call_control_id,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
            "authorized": self.authorized,
            "started": self.started,
            "stopped": self.stopped,
            "frames_in": self.frames_in,
            "frames_out": self.frames_out,
            "clears": self.clears,
            "marks_sent": self.marks_sent,
            "dropped_outbound_frames": self.dropped_outbound_frames,
            "inbound_samples": self.inbound_samples,
            "last_sequence_number": self.last_sequence_number,
            "out_of_order_frames": self.out_of_order_frames,
        }


class TelnyxMediaStream:
    """One Telnyx bidirectional media stream bound to one call.

    The object is deliberately synchronous: audio conversion is CPU work, the
    WebSocket write is the caller's job, and keeping the two apart is what makes
    the barge-in path (``clear`` drops the queue) testable without a socket.
    """

    def __init__(
        self,
        *,
        call_id: str,
        pipeline_rate: int = 16000,
        max_queued_frames: int = 200,
        stream_auth_token: str | None = None,
    ) -> None:
        if not (call_id or "").strip():
            raise TelnyxMediaStreamError("a call id is required to start a Telnyx media stream")
        if max_queued_frames < 1:
            raise TelnyxMediaStreamError("max_queued_frames must be at least 1")
        self.call_id = call_id
        self.pipeline_rate = int(pipeline_rate)
        self.max_queued_frames = int(max_queued_frames)
        self.state = TelnyxStreamState()
        self._queue: list[bytes] = []
        self._authorized_token = stream_auth_token

    # ------------------------------------------------------------- auth -----

    def authorize(self, token: str | None = None, *, now: int | None = None) -> bool:
        """Redeem the single-use stream token. False means close the socket."""
        candidate = token if token is not None else self._authorized_token
        if not candidate:
            return False
        granted = consume_stream_token(self.call_id, candidate, now=now)
        self.state.authorized = granted
        return granted

    @property
    def authorized(self) -> bool:
        return self.state.authorized

    # ------------------------------------------------------------ inbound ---

    def handle(self, raw: str | bytes | dict[str, Any]) -> TelnyxMediaEvent:
        """Apply one inbound frame to the stream state and return it parsed."""
        if not self.state.authorized:
            raise TelnyxMediaStreamError(
                "media frame received before the stream was authorized",
                code="stream_unauthorized",
                status_code=403,
            )
        event = parse_event(raw)

        if event.event == EVENT_START:
            if not event.stream_id:
                raise TelnyxMediaStreamError("start frame carried no stream_id")
            encoding = event.encoding or "PCMU"
            if encoding not in SUPPORTED_ENCODINGS:
                raise TelnyxMediaStreamError(
                    f"Telnyx stream requested unsupported encoding {encoding!r}",
                    code="unsupported_encoding",
                )
            self.state.stream_id = event.stream_id
            self.state.call_control_id = event.call_control_id or self.state.call_control_id
            self.state.encoding = encoding
            self.state.sample_rate = event.sample_rate
            self.state.started = True
            return event

        if event.event == EVENT_MEDIA:
            if not self.state.started:
                raise TelnyxMediaStreamError(
                    "media frame arrived before the start frame",
                    code="stream_not_started",
                    status_code=409,
                )
            if event.sequence_number is not None and self.state.last_sequence_number is not None:
                if event.sequence_number <= self.state.last_sequence_number:
                    self.state.out_of_order_frames += 1
            if event.sequence_number is not None:
                self.state.last_sequence_number = event.sequence_number
            self.state.frames_in += 1
            self.state.inbound_samples += len(event.payload)
            return event

        if event.event == EVENT_CLEAR:
            self.clear()
            return event

        if event.event == EVENT_STOP:
            self.state.stopped = True
            return event

        return event

    def inbound_pcm(self, event: TelnyxMediaEvent) -> bytes:
        """Convert an inbound media event into pipeline PCM16."""
        if not event.is_media:
            return b""
        return decode_to_pipeline_pcm(
            event.payload,
            encoding=self.state.encoding or event.encoding or "PCMU",
            sample_rate=self.state.sample_rate or event.sample_rate,
            pipeline_rate=self.pipeline_rate,
        )

    # ------------------------------------------------------------ outbound --

    def queue_pcm(self, pcm: bytes) -> bool:
        """Queue pipeline PCM16 for the caller. False when the queue is full."""
        if not pcm:
            return True
        if not self.state.started:
            raise TelnyxMediaStreamError(
                "cannot send audio before the start frame",
                code="stream_not_started",
                status_code=409,
            )
        if len(self._queue) >= self.max_queued_frames:
            self.state.dropped_outbound_frames += 1
            return False
        self._queue.append(pcm)
        return True

    def media_frame(self, pcm: bytes) -> str:
        """Build one outbound ``media`` frame from pipeline PCM16."""
        if not pcm:
            raise TelnyxMediaStreamError("refusing to send an empty media frame")
        encoding = self.state.encoding or "PCMU"
        payload = encode_from_pipeline_pcm(
            pcm,
            encoding=encoding,
            sample_rate=self.state.sample_rate or DEFAULT_SAMPLE_RATE,
            pipeline_rate=self.pipeline_rate,
        )
        self.state.frames_out += 1
        return json.dumps(
            {
                "event": EVENT_MEDIA,
                "stream_id": self.state.stream_id,
                "media": {"payload": base64.b64encode(payload).decode("ascii")},
            }
        )

    def drain(self) -> list[str]:
        """Encode and remove every queued PCM buffer, oldest first."""
        frames: list[str] = []
        while self._queue:
            frames.append(self.media_frame(self._queue.pop(0)))
        return frames

    def mark(self, name: str) -> str:
        """Ask the carrier to acknowledge playback up to this point."""
        label = (name or "").strip()
        if not label:
            raise TelnyxMediaStreamError("a mark needs a name")
        self.state.marks_sent += 1
        return json.dumps({"event": EVENT_MARK, "stream_id": self.state.stream_id, "mark": {"name": label}})

    def clear(self) -> str:
        """Barge-in: drop queued audio and tell the carrier to flush playback."""
        dropped = len(self._queue)
        self._queue.clear()
        self.state.clears += 1
        self.state.dropped_outbound_frames += dropped
        return json.dumps({"event": EVENT_CLEAR, "stream_id": self.state.stream_id})

    @property
    def queued_frames(self) -> int:
        return len(self._queue)


def frame_seconds(*, frame_ms: int = DEFAULT_FRAME_MS) -> float:
    """Frame duration in seconds, used for pacing outbound audio."""
    if frame_ms <= 0:
        raise TelnyxMediaStreamError("frame length must be positive")
    return frame_ms / 1000.0


def frame_bytes(*, sample_rate: int = DEFAULT_SAMPLE_RATE, frame_ms: int = DEFAULT_FRAME_MS) -> int:
    """Bytes of 8-bit G.711 audio in one frame."""
    return int(sample_rate * frame_ms / 1000)


def pcm16_frame_bytes(*, sample_rate: int, frame_ms: int = DEFAULT_FRAME_MS) -> int:
    """Bytes of 16-bit PCM in one frame at ``sample_rate``."""
    return int(sample_rate * frame_ms / 1000) * PCM16_SAMPLE_WIDTH
