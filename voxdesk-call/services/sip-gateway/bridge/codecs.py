"""G.711 and resampling for the SIP media bridge (PART 13, P13-01).

The model side of the platform speaks 16 kHz linear PCM. A PSTN trunk speaks
G.711 at 8 kHz, 8 bits per sample, one of two companding laws chosen by the
carrier. This module is the only place that knows the difference.

Both laws are implemented as lookup tables built once at import. That is the
whole trick: mu-law and A-law are defined by a piecewise formula, and the
formula evaluated per sample is slow enough to matter in a bridge that runs for
the length of a call.

Resampling is a 2x decimating/interpolating filter, which is exactly what
8 kHz <-> 16 kHz needs. It is not a general sample-rate converter and does not
pretend to be: a call that arrives at a different rate is rejected by
`codec_for` rather than silently mangled.
"""

from __future__ import annotations

import struct
from typing import Iterable

CODECS = ("PCMU", "PCMA")
RTP_CLOCK_RATE = 8000
TARGET_RATE = 16000
#: Samples per packet at 8 kHz. 20 ms is what every carrier sends.
FRAME_SAMPLES = 160


def _build_mulaw_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        encoded = ~value & 0xFF
        sign = encoded & 0x80
        exponent = (encoded >> 4) & 0x07
        mantissa = encoded & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        table.append(-sample if sign else sample)
    return tuple(table)


def _build_alaw_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        encoded = value ^ 0x55
        sign = encoded & 0x80
        exponent = (encoded >> 4) & 0x07
        mantissa = encoded & 0x0F
        if exponent == 0:
            sample = (mantissa << 4) + 8
        else:
            sample = ((mantissa << 4) + 0x108) << (exponent - 1)
        table.append(sample if sign else -sample)
    return tuple(table)


MULAW_DECODE = _build_mulaw_table()
ALAW_DECODE = _build_alaw_table()


def decode_mulaw(data: bytes) -> list[int]:
    return [MULAW_DECODE[byte] for byte in data]


def decode_alaw(data: bytes) -> list[int]:
    return [ALAW_DECODE[byte] for byte in data]


def _linear_to_mulaw(sample: int) -> int:
    """The reference G.711 writer (ITU-T G.711 section 4.2)."""
    bias = 0x84
    clip = 32635
    sign = 0x80 if sample < 0 else 0x00
    magnitude = min(abs(int(sample)), clip) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not magnitude & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


#: Segment end points, from the reference G.711 encoder.
_ALAW_SEG_END = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)


def _linear_to_alaw(sample: int) -> int:
    """The reference G.711 A-law writer.

    The sign bit is *not* part of the odd-bit inversion, which is why the mask
    differs for positive and negative samples. Getting this wrong is the classic
    A-law bug: audio that sounds like a loud buzz rather than speech.
    """
    value = int(sample) >> 3
    if value >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        value = -value - 1
    segment = 0
    for index, limit in enumerate(_ALAW_SEG_END):
        if value <= limit:
            segment = index
            break
    else:
        return 0x7F ^ mask
    encoded = segment << 4
    encoded |= ((value >> 1) & 0x0F) if segment < 2 else ((value >> segment) & 0x0F)
    return encoded ^ mask


def encode_mulaw(samples: Iterable[int]) -> bytes:
    return bytes(_linear_to_mulaw(sample) for sample in samples)


def encode_alaw(samples: Iterable[int]) -> bytes:
    return bytes(_linear_to_alaw(sample) for sample in samples)


def codec_for(name: str) -> tuple[str, int]:
    """Return ``(codec, payload_type)`` for a negotiated codec name."""
    key = (name or "").strip().upper()
    if key in ("PCMU", "G711U"):
        return "PCMU", 0
    if key in ("PCMA", "G711A"):
        return "PCMA", 8
    raise ValueError(f"{name!r} is not bridged; only PCMU and PCMA are G.711")


def to_pcm16(data: bytes, codec: str = "PCMU") -> bytes:
    """G.711 payload -> little-endian 16-bit PCM at 16 kHz."""
    if codec not in CODECS:
        raise ValueError(f"unsupported codec {codec!r}")
    samples = decode_mulaw(data) if codec == "PCMU" else decode_alaw(data)
    upsampled = upsample_2x(samples)
    return struct.pack(f"<{len(upsampled)}h", *upsampled)


def from_pcm16(data: bytes, codec: str = "PCMU", *, input_rate: int = TARGET_RATE) -> bytes:
    """Little-endian 16-bit PCM -> G.711 payload at 8 kHz."""
    if codec not in CODECS:
        raise ValueError(f"unsupported codec {codec!r}")
    if input_rate not in (RTP_CLOCK_RATE, TARGET_RATE):
        raise ValueError(f"input_rate must be {RTP_CLOCK_RATE} or {TARGET_RATE}")
    count = len(data) // 2
    samples = list(struct.unpack(f"<{count}h", data[: count * 2]))
    if input_rate == TARGET_RATE:
        samples = downsample_2x(samples)
    return encode_mulaw(samples) if codec == "PCMU" else encode_alaw(samples)


def upsample_2x(samples: list[int]) -> list[int]:
    """8 kHz -> 16 kHz by linear interpolation between neighbouring samples."""
    if not samples:
        return []
    out = [0] * (len(samples) * 2)
    for index, sample in enumerate(samples):
        nxt = samples[index + 1] if index + 1 < len(samples) else sample
        out[index * 2] = sample
        out[index * 2 + 1] = (sample + nxt) // 2
    return out


def downsample_2x(samples: list[int]) -> list[int]:
    """16 kHz -> 8 kHz by averaging pairs (a cheap anti-alias)."""
    return [(samples[index] + samples[index + 1]) // 2 for index in range(0, len(samples) - 1, 2)]
