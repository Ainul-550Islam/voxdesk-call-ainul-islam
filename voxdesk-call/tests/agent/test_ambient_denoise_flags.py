"""Unit tests for Ambient Audio Mixer (`SoundfileMixer`) & Caller Denoise (`NoisereduceFilter`) (Sub-Phase 2G)."""

from __future__ import annotations

import pytest
from pipecat.audio.filters.noisereduce_filter import NoisereduceFilter
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer

from app.agent.audio import (
    AMBIENT_SOUNDS,
    build_ambient_mixer,
    build_denoise_filter,
    normalize_ambient_volume,
)


@pytest.mark.asyncio
async def test_ambient_mixer_loads_all_five_presets_and_mixes_audio():
    assert set(AMBIENT_SOUNDS) == {
        "office",
        "call_center",
        "coffee_shop",
        "convention_hall",
        "summer_outdoor",
    }
    for name, wav_path in AMBIENT_SOUNDS.items():
        assert wav_path.is_file(), f"Missing ambient WAV asset: {wav_path}"
        mixer = build_ambient_mixer(name, ambient_volume=0.2)
        assert isinstance(mixer, SoundfileMixer)

    # Disabled ambient returns None
    assert build_ambient_mixer(None) is None
    assert build_ambient_mixer("off") is None
    assert build_ambient_mixer("none") is None

    # Unknown preset raises ValueError
    with pytest.raises(ValueError):
        build_ambient_mixer("spaceship_engine")

    # Volume normalization supports both linear (0..1) and decibel (-20 dB -> 0.1)
    assert normalize_ambient_volume(0.25) == 0.25
    assert normalize_ambient_volume(-20.0) == 0.1

    # Verify SoundfileMixer starts at 8000 Hz and mixes into a 16-bit PCM frame
    office_mixer = build_ambient_mixer("office", ambient_volume=0.25)
    assert office_mixer is not None
    await office_mixer.start(8000)
    silent_frame = b"\x00\x00" * 160
    mixed = await office_mixer.mix(silent_frame)
    assert isinstance(mixed, bytes)
    assert len(mixed) == len(silent_frame)
    assert mixed != silent_frame
    await office_mixer.stop()


@pytest.mark.asyncio
async def test_denoise_filter_toggle_and_filtering():
    assert build_denoise_filter(False) is None

    flt = build_denoise_filter(True)
    assert isinstance(flt, NoisereduceFilter)
    await flt.start(8000)
    raw_audio = b"\x10\x00\xf0\xff" * 160
    filtered = await flt.filter(raw_audio)
    assert isinstance(filtered, bytes)
    assert len(filtered) == len(raw_audio)
    await flt.stop()


def test_ambient_and_denoise_enable_frames_and_capability_gating():
    from pipecat.frames.frames import FilterEnableFrame, MixerEnableFrame

    from app.agent.audio.ambient import build_ambient_enable_frame
    from app.agent.audio.denoise import (
        advertised_denoise_modes,
        available_denoise_backends,
        build_denoise_enable_frame,
    )

    assert isinstance(build_ambient_enable_frame(True), MixerEnableFrame)
    assert isinstance(build_denoise_enable_frame(True), FilterEnableFrame)

    backends = available_denoise_backends()
    assert backends["noisereduce"]["available"] is True
    assert backends["krisp"]["available"] is False
    assert "noisereduce" in advertised_denoise_modes()
    assert "krisp" not in advertised_denoise_modes()
    with pytest.raises(ValueError, match="not licensed"):
        build_denoise_filter("krisp")

