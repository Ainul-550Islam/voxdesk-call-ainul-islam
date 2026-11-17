"""PART 12 / P12-12 — screening vs live pickup vs voicemail, EN + ES fixtures."""

from __future__ import annotations

import pytest

from app.telephony.call_screening import (
    DEFAULT_SCREENING_MESSAGE,
    SCREENING_STATES,
    ScreeningDetector,
    ScreeningScript,
    handle_screening,
)

IOS = "The person you are calling is using call screening. Please say your name and why you are calling."
GOOGLE = "Hi, I'm a Google Assistant screening this call. Tell me what this is about."
SPANISH = "La persona a la que llamas está filtrando llamadas. Di tu nombre y el motivo de tu llamada."


@pytest.mark.parametrize(
    "phrase, platform",
    [(IOS, "ios"), (GOOGLE, "google"), (SPANISH, "es")],
)
def test_screening_prompts_are_recognised_per_platform(phrase, platform) -> None:
    result = ScreeningDetector().classify(phrase)
    assert result.state == "screening"
    assert result.platform == platform
    assert result.phrase, "the triggering phrase is kept for the transcript"


def test_a_live_human_is_not_mistaken_for_a_screener() -> None:
    result = ScreeningDetector().classify("Hello? Who is this?")
    assert result.state == "live_pickup"


def test_voicemail_wins_over_a_leaked_screening_phrase() -> None:
    detector = ScreeningDetector()
    result = detector.classify(
        "this call is being screened ... please leave a message after the beep"
    )
    assert result.state == "voicemail"


def test_silence_is_unknown_and_keeps_us_listening() -> None:
    result = ScreeningDetector().classify("")
    assert result.state == "unknown"
    assert result.should_keep_listening is True


def test_a_screener_gets_our_message_then_a_wait() -> None:
    plan = handle_screening(IOS)
    assert plan["action"] == "speak"
    assert plan["message"] == DEFAULT_SCREENING_MESSAGE
    assert plan["then"] == "wait_for_pickup"
    assert plan["wait_s"] == 20.0


def test_answering_twice_is_limited() -> None:
    plan = handle_screening(IOS, repeats_done=1)
    assert plan["action"] == "wait"
    assert plan["reason"] == "already answered the screener"


def test_after_screening_a_live_pickup_resumes_the_conversation() -> None:
    plan = handle_screening("Hello, this is Dana speaking")
    assert plan["action"] == "resume_conversation"


def test_voicemail_hands_over_to_the_voicemail_action() -> None:
    plan = handle_screening("Please leave a message after the tone", voicemail_action="static_message")
    assert plan["action"] == "voicemail"
    assert plan["voicemail_action"] == "static_message"
    assert plan["classification"]["state"] == "voicemail"


def test_custom_phrases_are_supported() -> None:
    detector = ScreeningDetector(extra_phrases=["this is the doorman speaking"])
    assert detector.classify("this is the doorman speaking").state == "screening"


def test_script_validation_refuses_impossible_settings() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        ScreeningScript(message="   ")
    with pytest.raises(ValueError, match="wait_for_pickup_s"):
        ScreeningScript(wait_for_pickup_s=0.5)
    with pytest.raises(ValueError, match="repeat_limit"):
        ScreeningScript(repeat_limit=-1)


def test_every_state_is_in_the_declared_set() -> None:
    detector = ScreeningDetector()
    observed = {
        detector.classify(IOS).state,
        detector.classify("Hello?").state,
        detector.classify("leave a message").state,
        detector.classify("").state,
    }
    assert observed <= set(SCREENING_STATES)
