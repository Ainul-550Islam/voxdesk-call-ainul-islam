"""Voice-settings validation and per-provider parameter mapper (Sub-Phase 2C & 2G).

ElevenLabs' streaming TTS accepts a speech-speed multiplier through the
`voice_settings.speed` field, with a supported range of **0.7–1.2** (the
ElevenLabs Agents Platform restricts speed to that range; the REST API is wider
at 0.25–4.0, but the voice pipeline streams). Values outside the supported
range are rejected or clamped by the provider, so VoxDesk enforces the range
itself:

* the API rejects out-of-range writes with a 422, and
* the pipeline normalises out-of-range values that were persisted by older
  clients (clamp, never rewrite) so a provider never receives an unsupported
  value.

This module is deliberately dependency-free — no pipecat import — so the REST
layer (`app/api/routes.py`) and the provider layer (`app/agent/tts.py`) share
one source of truth without loading the voice stack.
"""
from __future__ import annotations

from typing import Any

SPEECH_SPEED_DEFAULT = 1.0
SPEECH_SPEED_MIN = 0.7
SPEECH_SPEED_MAX = 1.2


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def normalize_speech_speed(value: float) -> tuple[float, bool]:
    """Clamp a stored speech speed into the provider-supported range.

    Returns ``(applied, adjusted)`` where ``adjusted`` is True when the stored
    value lay outside ``[SPEECH_SPEED_MIN, SPEECH_SPEED_MAX]``. The stored value
    is never rewritten — only the value sent to the provider is normalised, so
    an existing tenant's configuration is preserved exactly.
    """
    stored = float(value)
    applied = min(max(stored, SPEECH_SPEED_MIN), SPEECH_SPEED_MAX)
    return applied, applied != stored


def map_voice_settings(
    provider: str,
    raw_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize and map unified `voice_settings` onto a specific TTS provider's parameters (2G).

    Supported canonical input keys:
    - `stability` (0.0..1.0, default 0.5)
    - `similarity_boost` / `similarity` (0.0..1.0, default 0.75)
    - `style` (0.0..1.0, default 0.0)
    - `speed` / `speech_speed` (0.5..2.0, default 1.0)
    - `pitch_semitones` (-12.0..12.0, default 0.0)
    - `volume_gain_db` (-12.0..12.0, default 0.0)
    - `use_speaker_boost` (bool, default True)
    """
    s = dict(raw_settings or {})
    prov = (provider or "elevenlabs").strip().lower()

    stability = _clamp(float(s.get("stability", 0.5)), 0.0, 1.0)
    similarity = _clamp(
        float(s.get("similarity_boost", s.get("similarity", 0.75))), 0.0, 1.0
    )
    style = _clamp(float(s.get("style", 0.0)), 0.0, 1.0)
    raw_speed = float(s.get("speed", s.get("speech_speed", SPEECH_SPEED_DEFAULT)))
    pitch_semitones = _clamp(float(s.get("pitch_semitones", s.get("pitch", 0.0))), -12.0, 12.0)
    volume_gain_db = _clamp(float(s.get("volume_gain_db", s.get("volume", 0.0))), -12.0, 12.0)
    use_speaker_boost = bool(s.get("use_speaker_boost", True))

    if prov == "elevenlabs":
        speed, _ = normalize_speech_speed(raw_speed)
        return {
            "stability": round(stability, 3),
            "similarity_boost": round(similarity, 3),
            "style": round(style, 3),
            "speed": round(speed, 3),
            "use_speaker_boost": use_speaker_boost,
        }

    if prov == "openai":
        speed = _clamp(raw_speed, 0.25, 4.0)
        return {
            "speed": round(speed, 3),
        }

    if prov == "cartesia":
        speed = _clamp(raw_speed, 0.5, 2.0)
        emotion: list[str] = []
        if style >= 0.6:
            emotion.append("positivity:high")
        elif style >= 0.25:
            emotion.append("positivity:low")
        return {
            "speed": round(speed, 3),
            "emotion": emotion,
            "volume_gain_db": round(volume_gain_db, 2),
        }

    if prov == "playht":
        speed = _clamp(raw_speed, 0.5, 2.0)
        temperature = round(_clamp(1.0 - stability * 0.5, 0.1, 1.5), 3)
        return {
            "speed": round(speed, 3),
            "temperature": temperature,
            "style_guidance": round(1.0 + style * 9.0, 2),
        }

    if prov == "azure":
        rate_pct = int(round((_clamp(raw_speed, 0.5, 2.0) - 1.0) * 100))
        pitch_st = int(round(pitch_semitones))
        return {
            "rate": f"{rate_pct:+d}%",
            "pitch": f"{pitch_st:+d}st",
            "volume_gain_db": round(volume_gain_db, 2),
        }

    if prov == "google":
        return {
            "speaking_rate": round(_clamp(raw_speed, 0.25, 4.0), 3),
            "pitch": round(pitch_semitones, 2),
            "volume_gain_db": round(volume_gain_db, 2),
        }

    # deepgram or default
    return {
        "speed": round(_clamp(raw_speed, 0.5, 2.0), 3),
    }
