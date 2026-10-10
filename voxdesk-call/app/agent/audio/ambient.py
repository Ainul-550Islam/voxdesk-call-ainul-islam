"""Ambient Background Audio Mixer wrapping Pipecat `SoundfileMixer` (Sub-Phase 2G).

Supports continuous loop mixing of `assets/ambient/*.wav` (`office`, `call_center`,
`coffee_shop`, `convention_hall`, `summer_outdoor`) into outbound agent audio at a
configurable volume (`ambient_volume` linear `0.0..1.0` or dB `-40..0`).
"""

from __future__ import annotations

import math
from pathlib import Path

from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer
from pipecat.frames.frames import MixerEnableFrame

ASSETS_AMBIENT_DIR = Path(__file__).resolve().parents[3] / "assets" / "ambient"

AMBIENT_SOUNDS: dict[str, Path] = {
    "office": ASSETS_AMBIENT_DIR / "office.wav",
    "call_center": ASSETS_AMBIENT_DIR / "call_center.wav",
    "coffee_shop": ASSETS_AMBIENT_DIR / "coffee_shop.wav",
    "convention_hall": ASSETS_AMBIENT_DIR / "convention_hall.wav",
    "summer_outdoor": ASSETS_AMBIENT_DIR / "summer_outdoor.wav",
}


def normalize_ambient_volume(raw_volume: float | None = 0.15) -> float:
    """Normalize linear (`0.0..1.0`) or decibel (`-60.0..0.0 dB`) ambient volume into `[0.0, 1.0]`."""
    if raw_volume is None:
        return 0.15
    val = float(raw_volume)
    if val < 0.0:
        # Interpret negative values as dB attenuation (e.g. -20 dB -> 0.10)
        linear = math.pow(10.0, val / 20.0)
        return round(max(0.0, min(1.0, linear)), 4)
    return round(max(0.0, min(1.0, val)), 4)


def build_ambient_mixer(
    ambient_sound: str | None,
    *,
    ambient_volume: float | None = 0.15,
    loop: bool = True,
) -> SoundfileMixer | None:
    """Build a Pipecat `SoundfileMixer` for `ambient_sound`, or `None` when disabled."""
    preset = (ambient_sound or "").strip().lower()
    if not preset or preset in {"off", "none", "disabled", "false"}:
        return None
    wav_path = AMBIENT_SOUNDS.get(preset)
    if wav_path is None or not wav_path.is_file():
        raise ValueError(
            f"Unknown ambient_sound preset {ambient_sound!r}; supported presets: {sorted(AMBIENT_SOUNDS)}"
        )
    volume = normalize_ambient_volume(ambient_volume)
    return SoundfileMixer(
        sound_files={preset: str(wav_path)},
        default_sound=preset,
        volume=volume,
        loop=loop,
    )


def build_ambient_enable_frame(enabled: bool) -> MixerEnableFrame:
    """Build a Pipecat `MixerEnableFrame` to dynamically toggle the ambient audio mixer."""
    return MixerEnableFrame(enable=bool(enabled))

