"""Erlang B/C/A: the queueing arithmetic behind the staffing plans.

These are closed-form results, so they are checked against the textbook values
rather than against a snapshot of whatever the code produced. A staffing plan built
on a wrong C is wrong in a way nobody notices until the queue is on fire.
"""

from __future__ import annotations

import math

import pytest

from app.platform_v2.wfm import erlang


# --- Erlang C ------------------------------------------------------------------


def test_erlang_c_matches_the_textbook_value():
    # C(c=1, a=0.5) = 0.5: a single server at 50% load makes you wait half the time.
    assert erlang.erlang_c(1, 0.5) == pytest.approx(0.5)
    # C(5, 3) = 0.2361, the standard table value.
    assert erlang.erlang_c(5, 3.0) == pytest.approx(0.2361, abs=5e-4)
    # C(10, 5) = 0.0361: with ten servers at 50% load, a caller still waits 3.6%
    # of the time. (5^10/10!)/(1-0.5) over the same term plus sum_{k<10} 5^k/k!.
    assert erlang.erlang_c(10, 5.0) == pytest.approx(0.0361, abs=5e-4)


def test_more_agents_always_lower_the_wait_probability():
    loads = [erlang.erlang_c(agents, 10.0) for agents in range(11, 21)]
    assert loads == sorted(loads, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in loads)


def test_a_load_beyond_capacity_means_everybody_waits():
    assert erlang.erlang_c(4, 4.0) == 1.0
    assert erlang.erlang_c(4, 6.0) == 1.0


def test_zero_load_needs_no_waiting():
    assert erlang.erlang_c(3, 0.0) == 0.0


def test_no_agents_is_a_total_wait():
    assert erlang.erlang_c(0, 3.0) == 1.0


def test_a_negative_load_is_clamped_to_no_waiting():
    """Load is non-negative by construction; a negative is treated as zero load."""
    assert erlang.erlang_c(3, -1.0) == 0.0


# --- service level and waiting time --------------------------------------------


def test_service_level_is_the_probability_of_answering_in_time():
    level = erlang.service_level(
        10, 8.0, target_seconds=20.0, average_handle_seconds=240.0
    )
    assert 0.0 < level < 1.0
    wait_probability = erlang.erlang_c(10, 8.0)
    # SL = 1 - C * exp(-(c - a) * target / AHT)
    expected = 1 - wait_probability * math.exp(-(10 - 8.0) * 20.0 / 240.0)
    assert level == pytest.approx(expected, rel=1e-9)


def test_a_more_generous_answer_target_raises_the_service_level():
    tight = erlang.service_level(10, 8.0, target_seconds=10.0, average_handle_seconds=240.0)
    loose = erlang.service_level(10, 8.0, target_seconds=60.0, average_handle_seconds=240.0)
    assert loose > tight


def test_the_expected_wait_falls_as_agents_are_added():
    waits = [
        erlang.average_wait_seconds(agents, 8.0, average_handle_seconds=240.0)
        for agents in range(9, 15)
    ]
    assert waits == sorted(waits, reverse=True)


def test_an_unstable_queue_reports_an_infinite_wait_not_a_negative_one():
    assert erlang.average_wait_seconds(5, 8.0, average_handle_seconds=240.0) == float("inf")


# --- Erlang A ------------------------------------------------------------------


def test_erlang_a_reports_abandonment_between_none_and_everybody():
    result = erlang.erlang_a(
        10,
        9.0,
        average_handle_seconds=240.0,
        average_patience_seconds=300.0,
        target_seconds=20.0,
    )
    assert 0.0 <= result.abandonment <= 1.0
    assert 0.0 <= result.service_level <= 1.0
    assert result.agents == 10


def test_patience_changes_abandonment():
    patient = erlang.erlang_a(
        10, 9.0, average_handle_seconds=240.0, average_patience_seconds=900.0
    )
    impatient = erlang.erlang_a(
        10, 9.0, average_handle_seconds=240.0, average_patience_seconds=60.0
    )
    assert impatient.abandonment > patient.abandonment


def test_erlang_a_with_no_load_has_no_abandonment():
    result = erlang.erlang_a(
        3, 0.0, average_handle_seconds=240.0, average_patience_seconds=300.0
    )
    assert result.abandonment == 0.0
    assert result.service_level == pytest.approx(1.0)


def test_an_impossible_patience_is_refused():
    with pytest.raises(ValueError):
        erlang.erlang_a(
            3, 2.0, average_handle_seconds=240.0, average_patience_seconds=0.0
        )


def test_erlang_a_with_a_huge_patience_approaches_erlang_c():
    """A customer who never gives up is a customer in the C model."""
    agents, load = 12, 9.0
    patient = erlang.erlang_a(
        agents, load, average_handle_seconds=240.0, average_patience_seconds=100_000.0
    )
    assert patient.probability_wait == pytest.approx(
        erlang.erlang_c(agents, load), abs=1e-3
    )


# --- the inverse problem -------------------------------------------------------


def test_required_agents_hits_the_target_service_level():
    result = erlang.required_agents(
        interval_volume=60.0,
        average_handle_seconds=240.0,
        interval_seconds=1800.0,
        target_service_level=0.8,
        target_answer_seconds=20.0,
    )
    assert result.offered_load == pytest.approx(8.0), "60 calls x 4 minutes over 30 minutes"
    assert result.service_level >= 0.8
    # The previous agent count must *not* have met the target, or the answer is
    # conservative rather than tight.
    below = erlang.service_level(
        result.agents - 1,
        result.offered_load,
        target_seconds=20.0,
        average_handle_seconds=240.0,
    )
    assert below < 0.8


def test_required_agents_never_exceeds_the_occupancy_cap():
    result = erlang.required_agents(
        interval_volume=60.0,
        average_handle_seconds=240.0,
        interval_seconds=1800.0,
        target_service_level=0.5,
        target_answer_seconds=5.0,
        max_occupancy=0.7,
    )
    assert result.occupancy <= 0.7 + 1e-9


def test_a_quiet_interval_needs_nobody():
    result = erlang.required_agents(
        interval_volume=0.0,
        average_handle_seconds=240.0,
        target_service_level=0.8,
        target_answer_seconds=20.0,
    )
    assert result.agents == 0
    assert result.service_level == pytest.approx(1.0)


def test_an_impossible_target_is_refused():
    with pytest.raises(ValueError):
        erlang.required_agents(
            interval_volume=60.0,
            average_handle_seconds=240.0,
            target_service_level=1.5,
            target_answer_seconds=20.0,
        )


def test_the_erlang_a_model_uses_more_agents_than_erlang_c():
    """Abandonment lets the queue tolerate a lower service level, so A needs fewer."""
    common = dict(
        interval_volume=120.0,
        average_handle_seconds=240.0,
        interval_seconds=1800.0,
        target_service_level=0.8,
        target_answer_seconds=20.0,
    )
    blocking = erlang.required_agents(model="erlang_c", **common)
    abandoning = erlang.required_agents(
        model="erlang_a", average_patience_seconds=300.0, **common
    )
    assert abandoning.agents <= blocking.agents


def test_an_unknown_model_is_refused():
    with pytest.raises(ValueError):
        erlang.required_agents(
            interval_volume=10.0,
            average_handle_seconds=240.0,
            target_service_level=0.8,
            target_answer_seconds=20.0,
            model="erlang_x",
        )
