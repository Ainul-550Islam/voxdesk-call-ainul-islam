"""PART 12 / P12-23 — weighted number routing: determinism, aliases, validation."""

from __future__ import annotations

import pytest

from app.telephony.number_routing import (
    ROUTING_CHANNELS,
    AgentWeight,
    NumberRouting,
    RoutingError,
    build_routing,
    distribution,
)


def _weighted() -> NumberRouting:
    return build_routing(
        {"inbound": [{"agent_id": "stable", "weight": 90}, {"agent_id": "canary", "weight": 10}]}
    )


def test_the_same_caller_always_reaches_the_same_agent() -> None:
    routing = _weighted()
    first = routing.select("inbound", caller="+15550100")
    for _ in range(20):
        assert routing.select("inbound", caller="+15550100") == first


def test_the_split_matches_the_weights_within_tolerance() -> None:
    routing = _weighted()
    counts = distribution(routing, "inbound", [f"+1555{i:05d}" for i in range(4000)])
    share = counts.get("canary", 0) / 4000
    assert 0.07 <= share <= 0.13, counts


def test_a_single_agent_channel_needs_no_hashing() -> None:
    routing = build_routing({"inbound": [{"agent_id": "only", "weight": 1}]})
    assert routing.select("inbound", caller="anything") == "only"
    assert routing.is_weighted is False


def test_the_legacy_single_agent_column_still_works() -> None:
    routing = build_routing(None, legacy_inbound_agent_id="legacy_agent")
    assert routing.entries_for("inbound") == (AgentWeight("legacy_agent"),)
    assert routing.select("inbound", caller="x") == "legacy_agent"


def test_weights_are_normalised_so_9_1_equals_90_10() -> None:
    a = build_routing({"inbound": [{"agent_id": "a", "weight": 90}, {"agent_id": "b", "weight": 10}]})
    b = build_routing({"inbound": [{"agent_id": "a", "weight": 9}, {"agent_id": "b", "weight": 1}]})
    callers = [f"+1999{i:04d}" for i in range(500)]
    assert distribution(a, "inbound", callers) == distribution(b, "inbound", callers)


def test_a_dictionary_form_is_accepted() -> None:
    routing = build_routing({"sms": {"a": 3, "b": 1}})
    assert {entry.agent_id for entry in routing.entries_for("sms")} == {"a", "b"}


def test_duplicate_agents_are_refused() -> None:
    with pytest.raises(RoutingError, match="duplicate agent"):
        NumberRouting(inbound=(AgentWeight("a", 1), AgentWeight("a", 2)))


def test_zero_total_weight_is_refused() -> None:
    with pytest.raises(RoutingError, match="no weight to distribute"):
        NumberRouting(inbound=(AgentWeight("a", 0), AgentWeight("b", 0)))


def test_negative_weight_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        AgentWeight("a", -1)


def test_each_channel_is_independent() -> None:
    routing = build_routing(
        {
            "inbound": [{"agent_id": "in_a", "weight": 1}],
            "outbound": [{"agent_id": "out_a", "weight": 1}],
            "sms": [{"agent_id": "sms_a", "weight": 1}],
        }
    )
    assert routing.select("inbound", caller="c") == "in_a"
    assert routing.select("outbound", caller="c") == "out_a"
    assert routing.select("sms", caller="c") == "sms_a"
    assert set(ROUTING_CHANNELS) == {"inbound", "outbound", "sms"}


def test_a_channel_with_nothing_configured_is_refused_clearly() -> None:
    with pytest.raises(RoutingError, match="no agents configured"):
        build_routing({"inbound": [{"agent_id": "a"}]}).select("sms")


def test_an_unknown_channel_is_refused() -> None:
    with pytest.raises(RoutingError, match="unknown channel"):
        _weighted().entries_for("fax")


def test_the_salt_changes_the_split_but_not_the_stability() -> None:
    routing = _weighted()
    plain = routing.select("inbound", caller="+1555")
    salted = routing.select("inbound", caller="+1555", salt="campaign-2")
    assert isinstance(plain, str) and isinstance(salted, str)
    assert salted == routing.select("inbound", caller="+1555", salt="campaign-2")


def test_bucket_reports_which_entry_was_chosen() -> None:
    routing = _weighted()
    index = routing.bucket("inbound", caller="+15550000")
    assert routing.entries_for("inbound")[index].agent_id == routing.select("inbound", caller="+15550000")
