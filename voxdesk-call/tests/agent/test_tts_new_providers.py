"""PART 12 / P12-09 — MiniMax, Fish Audio, ElevenLabs v3 and Cartesia Sonic-3 request shapes."""

from __future__ import annotations

import pytest

from app.agent.providers.tts_providers import (
    ELEVENLABS_V3_TAGS,
    MINIMAX_EMOTIONS,
    build_tts_request,
    capabilities_for,
)


def test_minimax_shape_includes_voice_and_audio_settings() -> None:
    req = build_tts_request(
        "minimax",
        text="Your refill is ready.",
        voice_id="male-qn-qingse",
        api_key="mm-key",
        group_id="g-42",
        sample_rate=8000,
    )
    assert req.url == "https://api.minimax.chat/v1/t2a_v2"
    assert req.headers["Authorization"] == "Bearer mm-key"
    assert req.query == {"GroupId": "g-42"}
    assert req.payload["model"] == "speech-02-hd"
    assert req.payload["stream"] is True
    assert req.payload["voice_setting"]["voice_id"] == "male-qn-qingse"
    assert req.payload["audio_setting"]["sample_rate"] == 8000
    assert req.payload["audio_setting"]["format"] == "pcm"


def test_minimax_emotion_is_an_enum_not_free_text() -> None:
    ok = build_tts_request("minimax", text="hi", voice_id="v", emotion="happy")
    assert ok.payload["voice_setting"]["emotion"] == "happy"

    dropped = build_tts_request("minimax", text="hi", voice_id="v", emotion="elated")
    assert "emotion" not in dropped.payload["voice_setting"]
    assert "elated" not in MINIMAX_EMOTIONS


def test_minimax_speed_is_clamped_to_the_documented_window() -> None:
    fast = build_tts_request("minimax", text="hi", voice_id="v", speed=9.0)
    slow = build_tts_request("minimax", text="hi", voice_id="v", speed=0.01)
    assert fast.payload["voice_setting"]["speed"] == 2.0
    assert slow.payload["voice_setting"]["speed"] == 0.5


def test_fish_audio_uses_a_reference_id_and_never_an_emotion_field() -> None:
    req = build_tts_request(
        "fish_audio", text="Hello.", voice_id="ref-123", api_key="fk", emotion="happy"
    )
    assert req.url == "https://api.fish.audio/v1/tts"
    assert req.headers["Authorization"] == "Bearer fk"
    assert req.payload["reference_id"] == "ref-123"
    assert "emotion" not in req.payload
    assert "prosody" not in req.payload, "speed is only sent when it differs from 1.0"

    faster = build_tts_request("fish_audio", text="Hello.", voice_id="r", speed=1.2)
    assert faster.payload["prosody"] == {"speed": 1.2}


def test_elevenlabs_v2_maps_emotion_to_the_style_dial() -> None:
    req = build_tts_request(
        "elevenlabs", text="Sure.", voice_id="voice-1", model="eleven_turbo_v2_5", emotion="calm"
    )
    assert req.headers["xi-api-key"] == "", "no key was passed, so none is sent"
    assert req.payload["voice_settings"]["style"] == 0.6
    assert "enable_audio_tags" not in req.payload


def test_elevenlabs_v3_opts_into_audio_tags_and_reports_which_ones() -> None:
    req = build_tts_request(
        "elevenlabs",
        text="I'm sorry, [sigh] let me check that again.",
        voice_id="voice-1",
        model="eleven_v3",
    )
    assert req.payload["model_id"] == "eleven_v3"
    assert req.payload["enable_audio_tags"] is True
    assert req.payload["audio_tags"] == ["sigh"]
    assert "sigh" in ELEVENLABS_V3_TAGS


def test_cartesia_sonic3_accepts_emotion_and_sonic2_drops_it() -> None:
    sonic3 = build_tts_request(
        "cartesia", text="Hi", voice_id="v", model="sonic-3", emotion="excited", language="en-US"
    )
    assert sonic3.payload["generation_config"]["emotion"] == "excited"
    assert sonic3.payload["language"] == "en"
    assert sonic3.headers["Cartesia-Version"]

    sonic2 = build_tts_request(
        "cartesia", text="Hi", voice_id="v", model="sonic-2", emotion="excited"
    )
    assert "emotion" not in sonic2.payload["generation_config"]


def test_capabilities_distinguish_the_providers_honestly() -> None:
    assert capabilities_for("minimax").supports_emotion is True
    assert capabilities_for("fish_audio").supports_emotion is False
    assert capabilities_for("cartesia").supports_cloning is True
    unknown = capabilities_for("brand_new_tts")
    assert unknown.supports_emotion is False and unknown.supports_speed is False


def test_an_unknown_provider_raises_instead_of_returning_an_empty_payload() -> None:
    with pytest.raises(ValueError, match="no TTS request builder"):
        build_tts_request("nope", text="x", voice_id="v")


def test_redacted_preview_hides_credentials() -> None:
    req = build_tts_request("minimax", text="hi", voice_id="v", api_key="secret")
    preview = req.as_dict()
    assert preview["headers"]["Authorization"] == "***"
    assert "secret" not in str(preview["headers"])


@pytest.mark.parametrize("provider", ["minimax", "fish_audio", "elevenlabs", "cartesia"])
def test_every_builder_returns_a_postable_request(provider) -> None:
    req = build_tts_request(provider, text="hello", voice_id="v", api_key="k")
    assert req.url.startswith("https://")
    assert req.payload
    assert req.headers.get("Content-Type") == "application/json"
