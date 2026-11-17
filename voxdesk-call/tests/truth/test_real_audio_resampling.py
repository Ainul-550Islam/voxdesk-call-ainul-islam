"""PCM resampling contract for the opt-in OpenAI Realtime and Gemini Live tests."""

from __future__ import annotations

import math
from pathlib import Path
import struct
import wave

from tests.real.audio_utils import wav_to_pcm16_mono_at_rate


def test_s2s_sample_audio_is_resampled_to_each_provider_rate(tmp_path: Path) -> None:
    source_rate = 8_000
    source_frames = source_rate // 10  # 100 ms of recorded-style PCM speech input
    samples = [
        int(12_000 * math.sin(2.0 * math.pi * 440.0 * idx / source_rate))
        for idx in range(source_frames)
    ]
    wav_path = tmp_path / "recorded.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(source_rate)
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    recorded_wav = wav_path.read_bytes()
    gemini_pcm = wav_to_pcm16_mono_at_rate(recorded_wav, 16_000)
    openai_pcm = wav_to_pcm16_mono_at_rate(recorded_wav, 24_000)

    assert len(gemini_pcm) == 1_600 * 2
    assert len(openai_pcm) == 2_400 * 2
    assert any(gemini_pcm) and any(openai_pcm)
