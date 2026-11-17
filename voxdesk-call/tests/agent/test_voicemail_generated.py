"""PART 12 / P12-13 — voicemail actions, 40-word cap, dynamic vars, PII guard, detection window."""

from __future__ import annotations

import pytest

from app.agent.tools.voicemail import (
    MAX_GENERATED_WORDS,
    VOICEMAIL_ACTIONS,
    enforce_word_cap,
    plan_voicemail_action,
    substitute_dynamic_variables,
)


def test_hangup_needs_no_message() -> None:
    plan = plan_voicemail_action(action="hangup")
    assert plan.action == "hangup"
    assert plan.allowed is False
    assert plan.message == ""


def test_static_message_is_spoken_with_variables_substituted() -> None:
    plan = plan_voicemail_action(
        action="static_message",
        message="Hi, it's {{agent_name}} calling about your {{topic}}.",
        variables={"agent_name": "Ava", "topic": "appointment"},
    )
    assert plan.message == "Hi, it's Ava calling about your appointment."
    assert plan.allowed is True


def test_an_unknown_variable_stays_visible() -> None:
    text = substitute_dynamic_variables("Hi {{agent_name}} about {{missing}}", {"agent_name": "Ava"})
    assert "Ava" in text
    assert "{{missing}}" in text, "a blank would hide the configuration mistake"


def test_generated_message_is_capped_at_forty_words() -> None:
    long_text = " ".join(["word"] * 120)
    plan = plan_voicemail_action(action="generated_message", generated_text=long_text)
    assert len(plan.message.split()) == MAX_GENERATED_WORDS
    assert plan.trimmed is True
    assert plan.generated is True


def test_the_cap_cuts_on_a_sentence_boundary_when_it_can() -> None:
    text = "First sentence here. " + " ".join(["filler"] * 60)
    trimmed, was_trimmed = enforce_word_cap(text, limit=10)
    assert was_trimmed is True
    assert trimmed.endswith(".")
    assert len(trimmed.split()) <= 10


def test_a_short_message_is_untouched() -> None:
    assert enforce_word_cap("Thanks, I'll call back later.") == (
        "Thanks, I'll call back later.",
        False,
    )


@pytest.mark.parametrize(
    "leaky",
    [
        "call me on 5550134 tomorrow",
        "email dana@example.com",
        "card 4111 1111 1111 1111",
        "ssn 123-45-6789",
    ],
)
def test_a_message_that_leaks_an_identifier_is_not_spoken(leaky) -> None:
    plan = plan_voicemail_action(action="generated_message", generated_text=leaky)
    assert plan.action == "hangup"
    assert plan.allowed is False
    assert plan.blocked_reason
    assert "spoke" not in plan.message.lower()


def test_generated_action_without_text_falls_back_with_a_reason() -> None:
    plan = plan_voicemail_action(action="generated_message")
    assert plan.action == "hangup"
    assert plan.blocked_reason == "no message was generated"


def test_detection_window_is_bounded() -> None:
    assert plan_voicemail_action(action="hangup", detection_window_s=0).detection_window_s == 1.0
    assert plan_voicemail_action(action="hangup", detection_window_s=999).detection_window_s == 60.0
    assert plan_voicemail_action(action="hangup", detection_window_s=8).detection_window_s == 8.0


def test_leave_message_alias_still_works() -> None:
    plan = plan_voicemail_action(action="leave_message", message="Please call back.")
    assert plan.action == "static_message"


def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown voicemail action"):
        plan_voicemail_action(action="whisper")


@pytest.mark.parametrize("action", VOICEMAIL_ACTIONS)
def test_every_declared_action_produces_a_plan(action) -> None:
    plan = plan_voicemail_action(action=action, message="Hello.", generated_text="Hello there.")
    assert plan.action in VOICEMAIL_ACTIONS
    assert 1.0 <= plan.detection_window_s <= 60.0
