"""Unit tests for unified `voice_settings` mapping & Multilingual `language="multi"` (Sub-Phase 2G)."""

from __future__ import annotations


from app.agent.language import resolve_language_config, supported_languages_catalog
from app.agent.voice_settings import map_voice_settings, normalize_speech_speed
from app.telephony.transcription import resolve_transcription_options


def test_map_voice_settings_across_all_tts_providers():
    raw = {
        "stability": 0.65,
        "similarity_boost": 0.85,
        "style": 0.7,
        "speed": 1.15,
        "pitch_semitones": 3.0,
        "volume_gain_db": -2.5,
        "use_speaker_boost": True,
    }

    el = map_voice_settings("elevenlabs", raw)
    assert el["stability"] == 0.65
    assert el["similarity_boost"] == 0.85
    assert el["style"] == 0.7
    assert el["speed"] == 1.15
    assert el["use_speaker_boost"] is True

    # ElevenLabs speed clamping (0.7..1.2)
    el_fast = map_voice_settings("elevenlabs", {"speed": 2.5})
    assert el_fast["speed"] == 1.2
    applied, adjusted = normalize_speech_speed(0.4)
    assert applied == 0.7
    assert adjusted is True

    oai = map_voice_settings("openai", raw)
    assert oai["speed"] == 1.15

    cart = map_voice_settings("cartesia", raw)
    assert cart["speed"] == 1.15
    assert "positivity:high" in cart["emotion"]
    assert cart["volume_gain_db"] == -2.5

    pht = map_voice_settings("playht", raw)
    assert pht["speed"] == 1.15
    assert 0.1 <= pht["temperature"] <= 1.5
    assert pht["style_guidance"] > 1.0

    az = map_voice_settings("azure", raw)
    assert az["rate"] == "+15%"
    assert az["pitch"] == "+3st"
    assert az["volume_gain_db"] == -2.5

    goog = map_voice_settings("google", raw)
    assert goog["speaking_rate"] == 1.15
    assert goog["pitch"] == 3.0
    assert goog["volume_gain_db"] == -2.5


def test_multilingual_auto_detect_and_boosted_keywords():
    multi_prof = resolve_language_config("multi")
    assert multi_prof.is_multilingual is True
    assert multi_prof.stt_language == "multi"
    assert multi_prof.stt_model == "nova-3"
    assert multi_prof.tts_model == "eleven_multilingual_v2"
    assert "Multilingual auto-detection" in multi_prof.llm_instruction

    es_prof = resolve_language_config("es-ES")
    assert es_prof.is_multilingual is False
    assert es_prof.stt_language == "es"
    assert es_prof.tts_model == "eleven_flash_v2_5"

    bn_prof = resolve_language_config("bn-BD")
    assert bn_prof.stt_language == "bn"
    assert bn_prof.stt_model == "nova-2"

    opts = resolve_transcription_options(
        language="multi",
        boosted_keywords=[("VoxDesk", 4.0), ("HIPAA", 2.5)],
    )
    assert opts["language"] == "multi"
    assert opts["multilingual"] is True
    assert opts["keywords"] == ["VoxDesk:4", "HIPAA:2.5"]

    catalog = supported_languages_catalog()
    assert len(catalog) >= 14
    assert any(item["code"] == "multi" and item["multilingual"] is True for item in catalog)
