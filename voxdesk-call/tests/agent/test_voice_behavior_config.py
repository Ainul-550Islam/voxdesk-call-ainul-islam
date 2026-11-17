"""PART 12 (P12-01..P12-07) — the voice-behaviour block must persist, validate, and round-trip."""

from __future__ import annotations

import pytest

from app.domain.agent_models import AgentConfig, CallToolingConfig, VoiceBehaviorConfig


def test_defaults_do_not_change_an_existing_agent() -> None:
    defaults = VoiceBehaviorConfig()
    assert defaults.colloquial is False
    assert defaults.expressive is False
    assert defaults.denoise_mode == "off"
    assert defaults.stt_endpointing_mode == "default"
    assert defaults.begin_message_delay_ms == 0
    assert defaults.normalize_for_speech is True
    assert defaults.validate() == []

    config = AgentConfig(tenant_id="t1", name="Agent")
    assert config.validate() == []


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"denoise_mode": "loud"}, "denoise_mode"),
        ({"stt_endpointing_mode": "psychic"}, "stt_endpointing_mode"),
        ({"begin_message_delay_ms": 5000}, "begin_message_delay_ms"),
        ({"colloquial_budget_ms": 0}, "colloquial_budget_ms"),
        ({"pace_min_speed": 1.4, "pace_max_speed": 1.2}, "pace speeds"),
        ({"language_reply_policy": "guess"}, "language_reply_policy"),
    ],
)
def test_invalid_values_are_reported_with_the_field_name(kwargs, expected) -> None:
    problems = VoiceBehaviorConfig(**kwargs).validate()
    assert any(expected in problem for problem in problems), problems


def test_snapshot_round_trip_preserves_every_setting() -> None:
    original = VoiceBehaviorConfig(
        colloquial=True,
        colloquial_budget_ms=35,
        expressive=True,
        expressive_emotions=("warm", "calm"),
        expressive_auto=True,
        dynamic_pace=False,
        pace_min_speed=0.9,
        pace_max_speed=1.1,
        begin_message_delay_ms=750,
        stt_endpointing_mode="accuracy",
        denoise_mode="noise_and_speech",
        normalize_for_speech=False,
        read_numbers_slowly=True,
        format_transcript=False,
        languages=("en-US", "es-US"),
        language_reply_policy="primary_only",
        language_fallback_phrase="Please continue in English.",
    )
    config = AgentConfig(tenant_id="t1", name="Agent", voice_behavior=original)
    snapshot = config.to_snapshot_dict()
    assert snapshot["voice_behavior"]["denoise_mode"] == "noise_and_speech"

    restored = AgentConfig.from_snapshot_dict(snapshot, tenant_id="t1")
    assert restored.voice_behavior == original


def test_a_snapshot_from_before_part_12_still_loads() -> None:
    legacy = {"name": "Old Agent", "greeting": "Hi.", "system_prompt": "Help."}
    restored = AgentConfig.from_snapshot_dict(legacy, tenant_id="t1")
    assert restored.voice_behavior == VoiceBehaviorConfig()


def test_partial_and_stringly_typed_snapshots_are_tolerated() -> None:
    restored = AgentConfig.from_snapshot_dict(
        {
            "name": "A",
            "voice_behavior": {
                "denoise_mode": "noise",
                "languages": "en-US, es-US",
                "colloquial_budget_ms": "40",
                "pace_min_speed": None,
            },
        },
        tenant_id="t1",
    )
    assert restored.voice_behavior.denoise_mode == "noise"
    assert restored.voice_behavior.languages == ("en-US", "es-US")
    assert restored.voice_behavior.colloquial_budget_ms == 40
    assert restored.voice_behavior.pace_min_speed == 0.8


def test_the_serialised_block_is_json_safe() -> None:
    import json

    payload = VoiceBehaviorConfig(languages=("en-US",), expressive_emotions=("warm",)).as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert isinstance(payload["languages"], list)


def test_call_tooling_round_trip_includes_ivr_and_live_call_controls() -> None:
    original = CallToolingConfig(
        voicemail_detection_enabled=True,
        voicemail_action="static_message",
        voicemail_message="Please call us back.",
        user_dtmf_enabled=True,
        user_dtmf_mode="fixed_digits",
        user_dtmf_fixed_digits=6,
        user_dtmf_keep_last=1,
        call_screening_enabled=True,
        call_screening_message="This is Acme calling.",
        ivr_navigation_enabled=True,
        ivr_goal="billing",
        auto_hangup_on_ivr=True,
        ivr_max_menus=5,
        structured_output_schema={"type": "object", "properties": {"intent": {"type": "string"}}},
        structured_output_strict=False,
    )
    config = AgentConfig(tenant_id="t1", name="Agent", call_tooling=original)
    snapshot = config.to_snapshot_dict()
    restored = AgentConfig.from_snapshot_dict(snapshot, tenant_id="t1")
    assert restored.call_tooling == original
    assert snapshot["call_tooling"]["auto_hangup_on_ivr"] is True
    assert snapshot["call_tooling"]["ivr_max_menus"] == 5


def test_behavioral_settings_participate_in_publish_hash() -> None:
    base = AgentConfig(tenant_id="t1", name="Agent")
    changed_voice = AgentConfig(
        tenant_id="t1", name="Agent", voice_behavior=VoiceBehaviorConfig(colloquial=True)
    )
    changed_call = AgentConfig(
        tenant_id="t1", name="Agent", call_tooling=CallToolingConfig(auto_hangup_on_ivr=True)
    )
    assert len({base.canonical_hash(), changed_voice.canonical_hash(), changed_call.canonical_hash()}) == 3


def test_string_boolean_call_tooling_values_parse_as_booleans() -> None:
    restored = AgentConfig.from_snapshot_dict(
        {"name": "Agent", "call_tooling": {"auto_hangup_on_ivr": "false"}},
        tenant_id="t1",
    )
    assert restored.call_tooling.auto_hangup_on_ivr is False
