"""P12-03 — the agent's pace follows the caller, inside a band, and never overrides a request."""

from __future__ import annotations

import pytest

from app.agent.adaptive_pace import (
    DEFAULT_MAX_SPEED,
    DEFAULT_MIN_SPEED,
    PaceAdapter,
    summarise_turns,
)


def test_a_fast_talker_speeds_the_agent_up_within_bounds() -> None:
    adapter = PaceAdapter()
    for _ in range(5):
        adapter.observe_turn(words=60, duration_ms=15000)  # 240 wpm
    decision = adapter.decide()
    assert decision.source == "adaptive"
    assert decision.speed > 1.0
    assert decision.speed <= DEFAULT_MAX_SPEED
    assert decision.eagerness > 0.7
    assert "talks fast" in adapter.to_context()


def test_a_slow_talker_slows_the_agent_down_within_bounds() -> None:
    adapter = PaceAdapter()
    for _ in range(5):
        adapter.observe_turn(words=15, duration_ms=15000)  # 60 wpm
    decision = adapter.decide()
    assert 0.0 < decision.speed < 1.0
    assert decision.speed >= DEFAULT_MIN_SPEED
    assert "talks slowly" in adapter.to_context()


def test_the_band_cannot_be_exceeded_by_an_absurd_rate() -> None:
    adapter = PaceAdapter(min_speed=0.8, max_speed=1.2)
    for _ in range(10):
        adapter.observe_turn(words=400, duration_ms=1000)  # 24000 wpm
    assert adapter.decide().speed == pytest.approx(1.2)
    slow = PaceAdapter(min_speed=0.8, max_speed=1.2)
    for _ in range(10):
        slow.observe_turn(words=1, duration_ms=20000)
    assert slow.decide().speed == pytest.approx(0.8)


def test_no_data_means_no_change() -> None:
    adapter = PaceAdapter()
    decision = adapter.decide()
    assert decision.speed == 1.0
    assert decision.source == "default"
    assert adapter.to_context() == ""
    assert summarise_turns([]) == {"user_wpm": 0.0, "speed": 1.0, "eagerness": 0.7}


def test_short_turns_are_ignored() -> None:
    adapter = PaceAdapter()
    adapter.observe_turn(words=2, duration_ms=200)
    assert adapter.user_wpm == 0.0
    assert adapter.decide().source == "default"


def test_explicit_slow_down_request_overrides_inference_and_expires() -> None:
    adapter = PaceAdapter()
    for _ in range(4):
        adapter.observe_turn(words=60, duration_ms=15000)  # fast talker → speed >  Cn 1.0
    adaptive = adapter.decide().speed

    assert adapter.handle_intent("can you slow down a bit please") == "slow_down"
    pinned = adapter.decide()
    assert pinned.source == "manual"
    assert pinned.speed < adaptive
    assert "asked you to change your speed" in adapter.to_context()

    adapter.handle_intent("slower please")
    assert adapter.decide().speed < pinned.speed

    for _ in range(6):
        adapter.tick()
    assert adapter.decide().source == "adaptive", "the pin must expire on its own"


def test_talk_faster_and_normal_speed_requests() -> None:
    adapter = PaceAdapter()
    assert adapter.handle_intent("could you talk faster") == "speed_up"
    assert adapter.decide().speed > 1.0
    assert adapter.decide().speed <= DEFAULT_MAX_SPEED
    assert adapter.handle_intent("okay that's better now") == "normal"
    assert adapter.decide().source == "adaptive" or adapter.decide().speed == 1.0


def test_unrelated_sentences_are_not_read_as_requests() -> None:
    adapter = PaceAdapter()
    for text in (
        "the slow down lane on the motorway was closed",
        "the music was too fast at the wedding",
        "I need to hurry up and finish work",
        "not so fast there, buddy",  # ambiguous, but not addressed to the agent
    ):
        assert adapter.handle_intent(text) is None, text
    assert adapter.decide().source == "default"


def test_auto_mode_off_pins_neutral_speed() -> None:
    adapter = PaceAdapter(auto_mode=False)
    for _ in range(5):
        adapter.observe_turn(words=60, duration_ms=15000)
    decision = adapter.decide()
    assert decision.speed == 1.0
    assert decision.source == "default"
    assert "disabled" in decision.reason


def test_window_keeps_only_recent_turns() -> None:
    adapter = PaceAdapter(window=2)
    adapter.observe_turn(words=60, duration_ms=15000)   # fast
    adapter.observe_turn(words=15, duration_ms=15000)   # slow
    adapter.observe_turn(words=15, duration_ms=15000)   # slow
    assert adapter.user_wpm == pytest.approx(60.0, abs=1.0)


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        PaceAdapter(min_speed=1.4, max_speed=1.2)
    with pytest.raises(ValueError):
        PaceAdapter(window=0)


def test_summarise_turns_reports_the_three_numbers() -> None:
    summary = summarise_turns([(40, 20000), (45, 20000)])
    assert summary["user_wpm"] == pytest.approx(127.5, abs=1.0)
    assert 0.8 <= summary["speed"] <= 1.2
    assert 0.0 <= summary["eagerness"] <= 1.0
