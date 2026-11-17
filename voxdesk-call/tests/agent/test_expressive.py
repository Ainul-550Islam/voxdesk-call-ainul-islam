"""P12-02 — emotion tags, per-provider mapping, and caller-delivery context."""

from __future__ import annotations

import pytest

from app.agent.expressive import (
    AutoEmotionPlanner,
    EmotionMapper,
    EmotionTagParser,
    ListeningFeatures,
    supports_emotion,
)


def test_parser_removes_tags_from_spoken_text_and_canonicalises_aliases() -> None:
    parsed = EmotionTagParser().parse("Sorry about that [sigh]. Give me a moment [Long Pause] please.")
    assert parsed.text == "Sorry about that. Give me a moment please."
    assert [span.canonical for span in parsed.spans] == ["sigh", "long pause"]
    assert parsed.emotions == ["calm", "neutral"]
    assert parsed.has_tags


def test_unknown_and_disallowed_tags_are_dropped_not_spoken() -> None:
    parsed = EmotionTagParser(allowed_tags=("sigh",)).parse("Sure [shout] thing [chuckle] there.")
    assert parsed.text == "Sure thing there."
    assert [(span.canonical) for span in parsed.spans] == ["unknown", "unknown"]
    with pytest.raises(ValueError):
        EmotionTagParser(allowed_tags=("sigh", "screaming"))


def test_star_style_markers_are_handled_too() -> None:
    parsed = EmotionTagParser().parse("Well *sigh* that did not work.")
    assert parsed.text == "Well that did not work."
    assert parsed.spans[0].canonical == "sigh"


def test_mapper_gives_each_provider_its_own_parameter_shape() -> None:
    eleven = EmotionMapper("elevenlabs").map("excited")
    assert eleven["voice_settings"]["style"] == 0.7
    assert eleven["voice_settings"]["stability"] < 0.6

    assert EmotionMapper("cartesia").map("warm") == {"emotion": "warm"}
    assert EmotionMapper("minimax").map("sad") == {"emotion": "sad"}
    assert EmotionMapper("fish_audio").map("happy") == {"emotion": "happy"}


def test_a_provider_that_cannot_express_emotion_says_so_instead_of_guessing() -> None:
    assert EmotionMapper("openai").map("excited") == {}
    assert EmotionMapper("openai").capabilities["supports_style"] is False
    assert supports_emotion("openai", "excited") is False
    assert supports_emotion("elevenlabs", "excited") is True
    with pytest.raises(ValueError):
        EmotionMapper("not-a-provider")


def test_render_strips_tags_only_for_providers_without_tag_support() -> None:
    with_tags = "[sigh] Sorry about that."
    eleven = EmotionMapper("elevenlabs").render(with_tags)
    assert eleven["text"] == "[sigh] Sorry about that."
    assert eleven["emotion"] == "calm"

    cartesia = EmotionMapper("cartesia").render(with_tags)
    assert cartesia["text"] == "Sorry about that."
    assert cartesia["params"] == {"emotion": "calm"}


def test_auto_planner_places_one_tag_for_an_apology_and_stays_bounded() -> None:
    planner = AutoEmotionPlanner(max_tags=2)
    planned = planner.plan("Sorry, that slot is taken. Would you like Tuesday at three?")
    assert planned.startswith("[sigh] ")
    assert planned.count("[") == 1


def test_auto_planner_never_stacks_on_manual_tags() -> None:
    planner = AutoEmotionPlanner()
    manual = "[chuckle] Sorry about that."
    assert planner.plan(manual) == manual


def test_auto_planner_respects_the_allowed_sets_and_zero_budget() -> None:
    no_sigh = AutoEmotionPlanner(allowed_tags=("chuckle",)).plan("Sorry about that.")
    assert "[sigh]" not in no_sigh
    assert AutoEmotionPlanner(max_tags=0).plan("Sorry about that.") == "Sorry about that."


def test_auto_planner_adds_a_pause_in_a_long_answer() -> None:
    text = (
        "Your appointment is confirmed for Tuesday at three in the afternoon with Doctor Rahman. "
        "Please arrive fifteen minutes early so we can update your details at the front desk. "
        "If you need to change it, call the clinic before noon tomorrow and we will move it."
    )
    planned = AutoEmotionPlanner(max_tags=2).plan(text)
    assert "[long pause]" in planned
    # The pause is inserted after a sentence boundary, never mid-clause.
    assert ". [long pause]" in planned or "? [long pause]" in planned


def test_listening_features_describe_a_hurried_caller() -> None:
    features = ListeningFeatures.from_segments(
        [(True, 4000), (False, 120), (True, 3500), (False, 90)], words=90, pitch_samples_hz=[120, 210]
    )
    assert features.pace_label == "fast"
    assert features.speech_ratio > 0.9
    assert features.pitch_spread_hz == 90
    context = features.to_context()
    assert "quickly" in context
    assert "interruptions" in context


def test_listening_features_describe_a_hesitant_caller() -> None:
    features = ListeningFeatures.from_segments(
        [(True, 2000), (False, 2400), (True, 1500), (False, 1800)], words=6
    )
    assert features.longest_pause_ms == 2400
    context = features.to_context()
    assert "pausing for a long time" in context
    assert features.as_dict()["pace_label"] == "slow"


def test_listening_features_are_empty_without_data() -> None:
    assert ListeningFeatures().to_context() == ""
    assert ListeningFeatures.from_segments([], words=0).as_dict()["pace_label"] == "unknown"
