"""Ambient background audio mixer and caller noise reduction filters (Sub-Phase 2G)."""

from app.agent.audio.ambient import (
    AMBIENT_SOUNDS,
    build_ambient_mixer,
    normalize_ambient_volume,
)
from app.agent.audio.denoise import (
    BackgroundVoiceGate,
    BackgroundVoiceGateProcessor,
    build_denoise_filter,
    build_denoise_stack,
)

__all__ = [
    "AMBIENT_SOUNDS",
    "BackgroundVoiceGate",
    "BackgroundVoiceGateProcessor",
    "build_ambient_mixer",
    "build_denoise_filter",
    "build_denoise_stack",
    "normalize_ambient_volume",
]
