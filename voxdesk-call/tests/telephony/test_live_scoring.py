# File: tests/telephony/test_live_scoring.py — the score, its components and its explanation
"""Live scoring tests (PART 15, P15-02).

Three things are worth testing here and nothing else is:

1. **The scale.** A perfect call scores 100, a hopeless one scores near 0, and a
   band boundary is where the constant says it is.
2. **The explanation.** Every score carries the components and the weights that
   produced it, and changing the weights does not rewrite an old score.
3. **The insufficiency rule.** Two turns are not a score. ``score_turns`` returns
   ``None`` rather than a confident number built from noise.
"""

from __future__ import annotations

import asyncio

import pytest

from app.telephony import live_scoring as ls


def _turn(speaker: str, sentiment: float = 0.0, latency: float = 0.0, text: str = "") -> ls.Turn:
    return ls.Turn(speaker=speaker, sentiment=sentiment, latency_ms=latency, text=text)


def _good_call() -> list[ls.Turn]:
    return [
        _turn("caller", 0.6, 0.0, "hi i need to book an appointment"),
        _turn("agent", 0.4, 400.0, "of course, i can help with that today"),
        _turn("caller", 0.7, 0.0, "great, tomorrow morning please"),
        _turn("agent", 0.5, 500.0, "i have nine fifteen or ten thirty"),
    ]


def _bad_call() -> list[ls.Turn]:
    return [
        _turn("caller", -0.8, 0.0, "this is the third time i called"),
        _turn("agent", -0.6, 3000.0, "please hold please hold please hold"),
        _turn("caller", -0.9, 0.0, "i am still waiting"),
        _turn("agent", -0.7, 3200.0, "please hold please hold please hold"),
    ]


# --------------------------------------------------------------- the scale ---


def test_band_edges():
    assert ls.band_for(70) == "ok"
    assert ls.band_for(69) == "watch"
    assert ls.band_for(45) == "watch"
    assert ls.band_for(44) == "critical"


def test_not_enough_turns_is_not_a_score():
    assert ls.score_turns(_good_call()[: ls.MIN_SAMPLES - 1]) is None
    scored = ls.score_turns(_good_call())
    assert scored is not None
    assert scored.sample_count == 4


def test_a_good_call_scores_higher_than_a_bad_call():
    good = ls.score_turns(_good_call())
    bad = ls.score_turns(_bad_call())
    assert good is not None and bad is not None
    assert good.score > bad.score
    assert good.band in ("ok", "watch")
    assert bad.band == "critical"
    assert 0 <= bad.score <= 100


def test_score_is_bounded():
    # Twenty turns of pure hostility cannot push the score below zero.
    horror = [
        _turn("caller", -1.0, 0.0, "no " * 40),
        _turn("agent", -1.0, 12000.0, ""),
    ] * 10
    scored = ls.score_turns(horror)
    assert scored is not None
    # Not zero: a flat trend scores the neutral 50 on that component, so the
    # floor of the scale is the trend weight, not 0. That is deliberate -- the
    # caller is not getting worse, they started furious.
    assert scored.score <= 10
    assert scored.components["sentiment"] == 0.0
    assert scored.components["latency"] == 0.0


# ------------------------------------------------------------ explanation ---


def test_components_and_weights_are_kept():
    scored = ls.score_turns(_bad_call())
    assert scored is not None
    assert set(scored.components) == set(ls.COMPONENTS)
    assert abs(sum(scored.weights.values()) - 1.0) < 1e-9
    # A weight set is stored with the score so the score stays explainable.
    assert scored.as_dict()["weights"]["latency"] > 0


def test_reasons_name_the_problem():
    scored = ls.score_turns(_bad_call())
    assert scored is not None
    assert scored.reasons
    joined = " ".join(scored.reasons)
    assert "p95" in joined or "repeating" in joined or "sentiment" in joined


def test_weights_change_the_score_but_not_the_past():
    turns = _bad_call()
    default = ls.score_turns(turns)
    latency_only = ls.score_turns(turns, weights={"latency": 1.0})
    assert default is not None and latency_only is not None
    assert latency_only.score != default.score
    # Re-scoring the same window with the default weights still gives the
    # original number: the inputs are not mutated by the second call.
    again = ls.score_turns(turns)
    assert again is not None and again.score == default.score


def test_weights_are_normalised():
    weights = ls.normalise_weights({"latency": 3.0, "sentiment": 1.0, "bogus": 99.0})
    assert "bogus" not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    # An empty or all-zero mapping falls back to the defaults rather than
    # dividing by zero.
    assert ls.normalise_weights({}) == ls.DEFAULT_WEIGHTS


def test_latency_component_is_monotonic():
    fast = ls._latency_component([_turn("agent", 0.0, 400.0)])
    slow = ls._latency_component([_turn("agent", 0.0, 2000.0)])
    dead = ls._latency_component([_turn("agent", 0.0, 5000.0)])
    assert fast == 100.0
    assert fast > slow > dead
    assert dead == 0.0


def test_talk_ratio_band():
    inside = [_turn("agent", text="a b c d e f"), _turn("caller", text="g h i j k l m n o p")]
    monologue = [_turn("agent", text="a b c d e f g h i j k l m n o p"), _turn("caller", text="x")]
    assert ls._talk_ratio_component(inside) == 100.0
    assert ls._talk_ratio_component(monologue) < 40.0


def test_engagement_penalises_repetition():
    repeated = [
        _turn("agent", text="let me check that for you"),
        _turn("agent", text="let me check that for you"),
        _turn("caller", text="ok"),
    ]
    varied = [
        _turn("agent", text="let me check that for you"),
        _turn("agent", text="i found two slots tomorrow"),
        _turn("caller", text="ok"),
    ]
    assert ls._engagement_component(repeated) < ls._engagement_component(varied)


# --------------------------------------------------------------- the window ---


def test_scorer_window_is_bounded_and_droppable():
    scorer = ls.LiveScorer(window=4)
    for index in range(10):
        scorer.add("call-1", speaker="caller", sentiment=0.1, text=f"turn {index}")
    assert scorer.sample_count("call-1") == 4
    assert scorer.active_calls() == ["call-1"]
    scorer.forget("call-1")
    assert scorer.sample_count("call-1") == 0
    assert scorer.current("call-1") is None


def test_scorer_returns_the_live_score_each_turn():
    scorer = ls.LiveScorer()
    results = [
        scorer.add("c", speaker="caller", sentiment=0.5, text="hello there"),
        scorer.add("c", speaker="agent", sentiment=0.2, latency_ms=300.0, text="hi how can i help"),
        scorer.add("c", speaker="caller", sentiment=0.4, text="i need a booking"),
    ]
    assert results[0] is None and results[1] is None
    assert results[2] is not None
    assert results[2].sample_count == 3


def test_window_smaller_than_minimum_is_refused():
    with pytest.raises(ValueError):
        ls.LiveScorer(window=1)


# ------------------------------------------------------------- fan-out path ---


def test_publish_score_reaches_a_subscriber():
    from app.telephony.monitor_bus import MonitorBus

    bus = MonitorBus()

    async def scenario():
        subscription = await bus.subscribe("call-9", supervisor_id="sup-1")
        try:
            scored = ls.score_turns(_bad_call())
            assert scored is not None
            published = await bus.publish_live_score("call-9", scored.as_dict())
            assert published == 1
            event = await subscription.get(timeout=1.0)
            payload = event.to_ws_json()
            assert payload["type"] == "score"
            assert payload["score"] == scored.score
            assert payload["band"] == scored.band
            assert payload["components"] == scored.as_dict()["components"]
        finally:
            await subscription.close()

    asyncio.run(scenario())


def test_publish_score_with_no_listeners_writes_nothing():
    from app.telephony.monitor_bus import MonitorBus

    bus = MonitorBus()

    async def scenario():
        return await bus.publish_live_score("nobody-listening", {"score": 10})

    assert asyncio.run(scenario()) == 0
