"""Deterministic PCM helpers shared by opt-in real-provider tests."""

from __future__ import annotations

import io
import struct
import wave


def wav_to_pcm16_mono_at_rate(wav_bytes: bytes, target_rate: int) -> bytes:
    """Decode a PCM16 WAV, mix channels to mono, and linearly resample to ``target_rate``.

    OpenAI Realtime expects PCM16 at 24 kHz while Gemini Live's PCM input is 16 kHz;
    merely stripping a WAV header and relabeling its sample rate corrupts the audio.
    """
    if target_rate <= 0:
        raise ValueError("target_rate must be positive")

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        source_rate = wf.getframerate()
        frame_count = wf.getnframes()
        raw = wf.readframes(frame_count)

    if channels < 1:
        raise ValueError("WAV must contain at least one channel")
    if sample_width != 2:
        raise ValueError(f"Expected PCM16 WAV (sample width 2), got {sample_width}")
    if source_rate <= 0:
        raise ValueError(f"Invalid WAV sample rate: {source_rate}")

    value_count = len(raw) // sample_width
    if value_count % channels:
        raise ValueError("PCM data ends with a partial audio frame")
    packed = struct.unpack(f"<{value_count}h", raw) if value_count else ()
    source_mono = [
        int(round(sum(packed[idx : idx + channels]) / channels))
        for idx in range(0, value_count, channels)
    ]
    if not source_mono:
        return b"\x00\x00" * max(1, target_rate // 5)

    target_count = max(1, int(round(len(source_mono) * target_rate / source_rate)))
    if source_rate == target_rate:
        resampled = source_mono
    else:
        resampled: list[int] = []
        scale = source_rate / target_rate
        last_idx = len(source_mono) - 1
        for out_idx in range(target_count):
            source_pos = min(out_idx * scale, float(last_idx))
            left = int(source_pos)
            right = min(left + 1, last_idx)
            fraction = source_pos - left
            value = int(round(source_mono[left] * (1.0 - fraction) + source_mono[right] * fraction))
            resampled.append(max(-32768, min(32767, value)))

    return struct.pack(f"<{len(resampled)}h", *resampled)
