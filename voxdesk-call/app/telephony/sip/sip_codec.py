"""SIP media codecs: G.711, sample-rate conversion and RFC 2833 DTMF.

Everything in this module is a pure function of its arguments -- no clock,
no socket, no global state -- so the tables can be checked against the
ITU-T G.711 reference vectors byte for byte.

* :func:`pcmu_to_pcm16` / :func:`pcma_to_pcm16` -- 8 kHz G.711 to 16-bit
  linear PCM.
* :func:`pcm16_to_pcmu` / :func:`pcm16_to_pcma` -- and back.
* :func:`resample_8k_to_16k` / :func:`resample_16k_to_8k` -- exact 2:1
  conversion for the 8 kHz <-> 16 kHz hop.
* :func:`parse_telephone_event` -- RFC 2833 in-band DTMF payload parsing.
"""

from __future__ import annotations

import array
import struct
import sys
from dataclasses import dataclass

#: G.711 constants (ITU-T Recommendation G.711).
_BIAS = 0x84
_CLIP = 8159
_SEG_SHIFT = 4
_SEG_MASK = 0x70
_QUANT_MASK = 0x0F
_SIGN_BIT = 0x80

#: Segment end points for the mu-law encoder.
_SEG_UEND = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)
#: Segment end points for the A-law encoder.
_SEG_AEND = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)


def _segment(value: int, table: tuple[int, ...]) -> int:
    for index, limit in enumerate(table):
        if value <= limit:
            return index
    return len(table)


def ulaw_to_linear(value: int) -> int:
    """Decode one 8-bit mu-law sample to a 16-bit linear sample."""
    inverted = (~value) & 0xFF
    magnitude = ((inverted & _QUANT_MASK) << 3) + _BIAS
    magnitude <<= (inverted & _SEG_MASK) >> _SEG_SHIFT
    return (_BIAS - magnitude) if (inverted & _SIGN_BIT) else (magnitude - _BIAS)


def linear_to_ulaw(pcm: int) -> int:
    """Encode one 16-bit linear sample to an 8-bit mu-law byte."""
    value = pcm >> 2
    if value < 0:
        value = -value
        mask = 0x7F
    else:
        mask = 0xFF
    if value > _CLIP:
        value = _CLIP
    value += _BIAS >> 2
    segment = _segment(value, _SEG_UEND)
    if segment >= 8:
        return 0x7F ^ mask
    encoded = (segment << 4) | ((value >> (segment + 1)) & 0x0F)
    return encoded ^ mask


def alaw_to_linear(value: int) -> int:
    """Decode one 8-bit A-law sample to a 16-bit linear sample."""
    inverted = value ^ 0x55
    magnitude = (inverted & _QUANT_MASK) << 4
    segment = (inverted & _SEG_MASK) >> _SEG_SHIFT
    if segment == 0:
        magnitude += 8
    elif segment == 1:
        magnitude += 0x108
    else:
        magnitude += 0x108
        magnitude <<= segment - 1
    # In A-law the sign bit is set for a positive sample: the encoding is
    # stored inverted (mask 0x55), so the decoder reads the sign *after* the
    # inversion.
    return magnitude if (inverted & _SIGN_BIT) else -magnitude


def linear_to_alaw(pcm: int) -> int:
    """Encode one 16-bit linear sample to an 8-bit A-law byte."""
    value = pcm >> 3
    if value >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        value = -value
    if value > _CLIP:
        value = _CLIP
    segment = _segment(value, _SEG_AEND)
    if segment >= 8:
        return 0x7F ^ mask
    encoded = segment << _SEG_SHIFT
    if segment < 2:
        encoded |= (value >> 1) & 0x0F
    else:
        encoded |= (value >> segment) & 0x0F
    return encoded ^ mask


def _to_samples(pcm: bytes) -> array.array:
    usable = len(pcm) - (len(pcm) % 2)
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":  # pragma: no cover - little-endian in practice
        samples.byteswap()
    return samples


def _from_samples(samples: array.array) -> bytes:
    if sys.byteorder == "big":  # pragma: no cover - little-endian in practice
        swapped = array.array("h", samples)
        swapped.byteswap()
        return swapped.tobytes()
    return samples.tobytes()


def pcmu_to_pcm16(payload: bytes) -> bytes:
    """Decode 8 kHz mu-law bytes to 16-bit linear PCM."""
    return _from_samples(array.array("h", (ulaw_to_linear(b) for b in payload)))


def pcma_to_pcm16(payload: bytes) -> bytes:
    """Decode 8 kHz A-law bytes to 16-bit linear PCM."""
    return _from_samples(array.array("h", (alaw_to_linear(b) for b in payload)))


def pcm16_to_pcmu(pcm: bytes) -> bytes:
    """Encode 16-bit linear PCM to 8 kHz mu-law bytes."""
    return bytes(linear_to_ulaw(sample) for sample in _to_samples(pcm))


def pcm16_to_pcma(pcm: bytes) -> bytes:
    """Encode 16-bit linear PCM to 8 kHz A-law bytes."""
    return bytes(linear_to_alaw(sample) for sample in _to_samples(pcm))


def g711_to_pcm16(payload: bytes, codec: str) -> bytes:
    """Decode G.711 payload bytes (``PCMU`` or ``PCMA``) to 16-bit PCM."""
    name = (codec or "").strip().upper()
    if name == "PCMU":
        return pcmu_to_pcm16(payload)
    if name == "PCMA":
        return pcma_to_pcm16(payload)
    raise ValueError(f"unsupported G.711 codec: {codec!r}")


def pcm16_to_g711(pcm: bytes, codec: str) -> bytes:
    """Encode 16-bit PCM to G.711 payload bytes (``PCMU`` or ``PCMA``)."""
    name = (codec or "").strip().upper()
    if name == "PCMU":
        return pcm16_to_pcmu(pcm)
    if name == "PCMA":
        return pcm16_to_pcma(pcm)
    raise ValueError(f"unsupported G.711 codec: {codec!r}")


def resample_8k_to_16k(pcm: bytes) -> bytes:
    """Upsample 8 kHz PCM16 to 16 kHz PCM16 by linear interpolation."""
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


def resample_16k_to_8k(pcm: bytes) -> bytes:
    """Downsample 16 kHz PCM16 to 8 kHz PCM16 by averaging each sample pair."""
    samples = _to_samples(pcm)
    if not samples:
        return b""
    out = array.array("h")
    for index in range(0, len(samples) - 1, 2):
        out.append((samples[index] + samples[index + 1]) // 2)
    return _from_samples(out)


def resample_pcm16(pcm: bytes, *, source_rate: int, target_rate: int) -> bytes:
    """Convert PCM16 between the telephony rates (8 kHz and 16 kHz)."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate:
        return pcm
    if source_rate == 8000 and target_rate == 16000:
        return resample_8k_to_16k(pcm)
    if source_rate == 16000 and target_rate == 8000:
        return resample_16k_to_8k(pcm)
    raise ValueError(f"unsupported PCM16 resample ratio {source_rate} -> {target_rate}")


@dataclass(frozen=True)
class TelephoneEvent:
    """One RFC 2833 in-band DTMF event."""

    event: int
    end: bool
    volume: int
    duration_ms: int


def parse_telephone_event(payload: bytes) -> TelephoneEvent | None:
    """Parse an RFC 2833 telephone-event payload.

    The payload is four bytes: event type, ``E|R|volume``, and a 16-bit
    big-endian duration in milliseconds. A payload that is too short or that
    carries an out-of-range event number is rejected (``None``), never
    guessed at.
    """
    if not payload or len(payload) < 4:
        return None
    event = payload[0] & 0x7F
    if event > 15:
        return None
    second = payload[1]
    end = bool(second & 0x80)
    volume = second & 0x3F
    duration_ms = struct.unpack(">H", payload[2:4])[0]
    return TelephoneEvent(
        event=event, end=end, volume=volume, duration_ms=duration_ms
    )


def encode_telephone_event(
    event: int, *, end: bool = False, volume: int = 10, duration_ms: int = 100
) -> bytes:
    """Encode one RFC 2833 telephone-event payload."""
    if not 0 <= int(event) <= 15:
        raise ValueError("telephone-event code must be between 0 and 15")
    if not 0 <= int(volume) <= 63:
        raise ValueError("telephone-event volume must be between 0 and 63")
    second = (0x80 if end else 0x00) | (int(volume) & 0x3F)
    return bytes(
        [int(event) & 0x7F, second]
    ) + struct.pack(">H", max(0, min(0xFFFF, int(duration_ms))))


__all__ = [
    "TelephoneEvent",
    "alaw_to_linear",
    "encode_telephone_event",
    "g711_to_pcm16",
    "linear_to_alaw",
    "linear_to_ulaw",
    "pcm16_to_g711",
    "pcm16_to_pcma",
    "pcm16_to_pcmu",
    "pcma_to_pcm16",
    "pcmu_to_pcm16",
    "parse_telephone_event",
    "resample_16k_to_8k",
    "resample_8k_to_16k",
    "resample_pcm16",
    "ulaw_to_linear",
]
