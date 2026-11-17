"""PART 12 / P12-15 — keypad collection: three modes, masking, timeouts, safety stops."""

from __future__ import annotations

import pytest

from app.agent.tools.user_dtmf import (
    DTMF_MODES,
    DtmfPolicy,
    UserDtmfCollector,
    collect_keypad_input,
    mask_digits,
)


def test_end_key_mode_finishes_on_the_terminator() -> None:
    result = collect_keypad_input("1234#", policy=DtmfPolicy(mode="end_key"))
    assert result.digits == "1234"
    assert result.complete is True
    assert result.reason == "end_key"


def test_end_key_is_not_stored_as_a_digit() -> None:
    result = collect_keypad_input("12#", policy=DtmfPolicy(mode="end_key", end_key="#"))
    assert "#" not in result.digits


def test_fixed_digits_mode_stops_without_a_terminator() -> None:
    result = collect_keypad_input("98765", policy=DtmfPolicy(mode="fixed_digits", fixed_digits=4))
    assert result.digits == "9876"
    assert result.reason == "fixed_digits"
    assert result.complete is True


def test_fixed_digits_that_never_arrive_stay_incomplete() -> None:
    result = collect_keypad_input("98", policy=DtmfPolicy(mode="fixed_digits", fixed_digits=4))
    assert result.complete is False
    assert result.reason == "incomplete"


def test_timeout_mode_finishes_on_silence_not_on_a_key() -> None:
    policy = DtmfPolicy(mode="timeout", timeout_s=2.0)
    collector = UserDtmfCollector(policy)
    collector.feed("5", at=10.0)
    assert collector.tick(at=11.0) is None, "only 1s of silence so far"
    result = collector.tick(at=12.5)
    assert result is not None
    assert result.digits == "5"
    assert result.reason == "timeout"
    assert result.complete is True


def test_timeout_with_no_digits_never_completes() -> None:
    collector = UserDtmfCollector(DtmfPolicy(mode="timeout", timeout_s=1.0))
    assert collector.tick(at=100.0) is None
    assert collector.result().complete is False


def test_mask_keeps_only_the_tail() -> None:
    assert mask_digits("4111111111111111") == "*" * 14 + "11"
    assert mask_digits("1234", keep_last=0) == "****"
    assert mask_digits("7") == "7"
    assert mask_digits("") == ""


def test_the_event_carries_no_raw_digits() -> None:
    event = collect_keypad_input("5550134#").to_event()
    assert event["type"] == "dtmf_input"
    assert event["digits_masked"] == "*****34"
    assert event["digit_count"] == 7
    assert "5550134" not in str(event)


def test_a_stuck_key_cannot_fill_the_buffer_forever() -> None:
    result = collect_keypad_input("1" * 40, policy=DtmfPolicy(mode="end_key", max_digits=8))
    assert len(result.digits) == 8
    assert result.reason == "max_digits"
    assert result.complete is False, "hitting the cap is not a completed entry"


@pytest.mark.parametrize("mode", DTMF_MODES)
def test_every_mode_exists_and_validates(mode) -> None:
    assert DtmfPolicy(mode=mode).mode == mode


def test_invalid_configuration_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown dtmf mode"):
        DtmfPolicy(mode="press_hash")
    with pytest.raises(ValueError, match="end_key"):
        DtmfPolicy(mode="end_key", end_key="5")
    with pytest.raises(ValueError, match="timeout_s"):
        DtmfPolicy(mode="timeout", timeout_s=0)
    with pytest.raises(ValueError, match="fixed_digits"):
        DtmfPolicy(mode="fixed_digits", fixed_digits=99, max_digits=8)


@pytest.mark.parametrize("bad", ["", "12", "Z", "!"])
def test_only_single_valid_digits_are_accepted(bad) -> None:
    collector = UserDtmfCollector()
    with pytest.raises(ValueError):
        collector.feed(bad)


def test_feeding_after_completion_does_not_change_the_result() -> None:
    collector = UserDtmfCollector(DtmfPolicy(mode="fixed_digits", fixed_digits=2))
    collector.feed("1", at=0.0)
    done = collector.feed("2", at=0.1)
    assert done is not None and done.digits == "12"
    assert collector.feed("3", at=0.2).digits == "12"
    assert collector.result().digits == "12"
