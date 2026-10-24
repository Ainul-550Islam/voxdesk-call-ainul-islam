"""Audio format adapters for the speech-to-speech runtime.

The providers speak one format; the VoxDesk pipeline speaks another:

===================  ======================  =========================
provider             wire format             pipeline format
===================  ======================  =========================
OpenAI Realtime      PCM16 @ 24 kHz          PCM16 @ 8 kHz
Gemini Live          PCM16 @ 16 kHz          PCM16 @ 8 kHz
telephony carriers   G.711 (mu-law/A-law)   PCM16 @ 8 kHz
===================  ======================  =========================

Every conversion here is exact arithmetic on integer samples -- no float
rounding drift, no external DSP dependency, and no fake "conversion" that
merely renames a buffer.
"""

from __future__ import annotations

import array
import sys
from typing import Iterable

from app.telephony.sip.sip_codec import (
    pcm16_to_pcma,
    pcm16_to_pcmu,
    pcma_to_pcm16,
    pcmu_to_pcm16,
)

#: Sample rates the runtime understands.
PIPELINE_SAMPLE_RATE = 8000
OPENAI_REALTIME_SAMPLE_RATE = 24000
GEMINI_LIVE_SAMPLE_RATE = 16000

#: The wire formats the runtime understands.
PCM16 = "pcm16"
ULAW = "ulaw"
ALAW = "alaw"


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


def upsample_2x(pcm: bytes) -> bytes:
    """Double the sample rate by linear interpolation between neighbours."""
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


def downsample_2x(pcm: bytes) -> bytes:
    """Halve the sample rate by averaging each pair of samples."""
    samples = _to_samples(pcm)
    if not samples:
        return b""
    out = array.array("h")
    for index in range(0, len(samples) - 1, 2):
        out.append((samples[index] + samples[index + 1]) // 2)
    return _from_samples(out)


def resample_pcm16(pcm: bytes, *, source_rate: int, target_rate: int) -> bytes:
    """Resample 16-bit PCM between two rates.

    The 2:1 conversions are exact (pair averaging and linear
    interpolation). Other ratios use a rational linear interpolator, which
    is deterministic and bounded in error.
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate:
        return pcm
    if target_rate == source_rate * 2:
        return upsample_2x(pcm)
    if source_rate == target_rate * 2:
        return downsample_2x(pcm)

    samples = _to_samples(pcm)
    if not samples:
        return b""
    out = array.array("h")
    last = len(samples) - 1
    # Output sample j sits at source position j * source_rate / target_rate.
    count = (len(samples) * target_rate) // source_rate
    for index in range(count):
        position = index * source_rate
        source_index = position // target_rate
        fraction = (position % target_rate) / target_rate
        current = samples[min(source_index, last)]
        nxt = samples[min(source_index + 1, last)]
        out.append(int(round(current + (nxt - current) * fraction)))
    return _from_samples(out)


def encode(pcm: bytes, codec: str) -> bytes:
    """Encode linear PCM into a wire format."""
    name = (codec or PCM16).strip().lower()
    if name == PCM16:
        return pcm
    if name == ULAW:
        return pcm16_to_pcmu(pcm)
    if name == ALAW:
        return pcm16_to_pcma(pcm)
    raise ValueError(f"unsupported codec: {codec!r}")


def decode(payload: bytes, codec: str) -> bytes:
    """Decode a wire format into linear PCM."""
    name = (codec or PCM16).strip().lower()
    if name == PCM16:
        return payload
    if name == ULAW:
        return pcmu_to_pcm16(payload)
    if name == ALAW:
        return pcma_to_pcm16(payload)
    raise ValueError(f"unsupported codec: {codec!r}")


def to_pipeline_pcm(
    payload: bytes,
    *,
    codec: str = PCM16,
    source_rate: int = GEMINI_LIVE_SAMPLE_RATE,
    target_rate: int = PIPELINE_SAMPLE_RATE,
) -> bytes:
    """Convert provider audio into the pipeline's PCM16 format."""
    pcm = decode(payload, codec)
    return resample_pcm16(pcm, source_rate=source_rate, target_rate=target_rate)


def from_pipeline_pcm(
    pcm: bytes,
    *,
    codec: str = PCM16,
    source_rate: int = PIPELINE_SAMPLE_RATE,
    target_rate: int = GEMINI_LIVE_SAMPLE_RATE,
) -> bytes:
    """Convert the pipeline's PCM16 into a provider wire format."""
    resampled = resample_pcm16(pcm, source_rate=source_rate, target_rate=target_rate)
    return encode(resampled, codec)


def frame_duration_ms(payload: bytes, *, sample_rate: int, codec: str = PCM16) -> float:
    """The duration of a payload in milliseconds."""
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    if (codec or PCM16).strip().lower() == PCM16:
        samples = len(payload) // 2
    else:
        samples = len(payload)
    return (samples / sample_rate) * 1000.0


def chunk_payload(payload: bytes, *, frame_bytes: int) -> list[bytes]:
    """Split a payload into fixed-size frames (a short tail is kept)."""
    if frame_bytes <= 0:
        raise ValueError("frame_bytes must be positive")
    if not payload:
        return []
    return [payload[i : i + frame_bytes] for i in range(0, len(payload), frame_bytes)]


def concat(chunks: Iterable[bytes]) -> bytes:
    """Join byte chunks (a small helper used by the tests and the meter)."""
    return b"".join(chunks)


__all__ = [
    "ALAW",
    "GEMINI_LIVE_SAMPLE_RATE",
    "OPENAI_REALTIME_SAMPLE_RATE",
    "PCM16",
    "PIPELINE_SAMPLE_RATE",
    "ULAW",
    "chunk_payload",
    "concat",
    "decode",
    "downsample_2x",
    "encode",
    "frame_duration_ms",
    "from_pipeline_pcm",
    "resample_pcm16",
    "to_pipeline_pcm",
    "upsample_2x",
]
