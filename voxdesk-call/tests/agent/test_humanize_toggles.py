"""P12-06 — normalise-for-speech and slow-number toggles, per agent."""

from __future__ import annotations

import pytest

from app.agent.humanize import HumanizeOptions, apply_humanize, normalize_for_speech, read_numbers_slowly


def test_defaults_preserve_todays_behaviour() -> None:
    text = "Your total is $40 and the **visit** is confirmed."
    assert apply_humanize(text) == normalize_for_speech(text)
    assert HumanizeOptions().as_dict() == {
        "normalize_for_speech": True,
        "read_numbers_slowly": False,
        "format_transcript": True,
    }


def test_normalisation_can_be_turned_off() -> None:
    raw = "Your total is **$40**."
    assert apply_humanize(
        raw, HumanizeOptions(normalize_for_speech=False, format_transcript=False)
    ) == raw


def test_transcript_formatting_removes_markup_without_rewriting_numbers() -> None:
    raw = "**Order 5550142** is ready 😀"
    formatted = apply_humanize(
        raw, HumanizeOptions(normalize_for_speech=False, format_transcript=True)
    )
    assert formatted == "Order 5550142 is ready"
    assert apply_humanize(
        raw, HumanizeOptions(normalize_for_speech=False, format_transcript=False)
    ) == raw


def test_slow_number_reading_spells_a_serial_number() -> None:
    spoken = read_numbers_slowly("Reference 5550142 please.")
    assert "five, five, five, zero, one, four, two" in spoken


def test_slow_number_reading_leaves_money_percent_and_clock_alone() -> None:
    text = "It costs $1,250 and is 25% done at 3:30."
    assert read_numbers_slowly(text) == text


def test_slow_number_reading_ignores_short_numbers() -> None:
    assert read_numbers_slowly("Table for 4 at 7") == "Table for 4 at 7"


def test_slow_numbers_flow_into_the_full_transform_in_the_right_order() -> None:
    spoken = apply_humanize("Order 1234567 is ready.", HumanizeOptions(read_numbers_slowly=True))
    assert "one, two, three, four, five, six, seven" in spoken
    # normalize_for_speech would otherwise have said "one million two hundred..." — asserting the
    # spelled form is present proves the order.
    assert "million" not in spoken


def test_any_flag_combination_is_accepted() -> None:
    for normalize in (True, False):
        for slow in (True, False):
            for transcript in (True, False):
                options = HumanizeOptions(
                    normalize_for_speech=normalize, read_numbers_slowly=slow, format_transcript=transcript
                )
                assert options.validate() == []
                assert apply_humanize("Call 5550142 now.", options)


def test_empty_text_is_returned_as_is() -> None:
    assert apply_humanize("") == ""
    assert apply_humanize("", HumanizeOptions(read_numbers_slowly=True)) == ""
