"""PART 12 / P12-17 — an `always` edge may or may not wait for the caller."""

from __future__ import annotations

import asyncio

from app.builder.flow_runner import FlowRunner
from app.builder.node import EDGE_TIMINGS, EdgeCondition, FlowGraph


def _flow(timing: str) -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "ask", "type": "conversation", "params": {"prompt": "How can I help?"}},
            {"id": "after", "type": "end"},
        ],
        "edges": [
            {
                "source": "start",
                "target": "ask",
                "condition": {"kind": "always", "timing": timing},
            },
            {
                "source": "ask",
                "target": "after",
                "condition": {"kind": "always", "timing": timing},
            },
        ],
    }


async def _pick(runner: FlowRunner, utterance: str) -> str | None:
    edge = await runner._select_outgoing_edge(runner.current_node, utterance)
    return edge[0].target_id if edge[0] else None


def test_the_default_timing_is_after_user_response() -> None:
    assert EdgeCondition(kind="always").timing == "after_user_response"
    assert set(EDGE_TIMINGS) == {"after_user_response", "skip_user_response"}


def test_an_interactive_node_waits_for_the_caller() -> None:
    runner = FlowRunner(_flow("after_user_response"))
    runner.current_node_id = "ask"
    assert asyncio.run(_pick(runner, "")) is None, "nothing said yet, so do not move on"
    assert asyncio.run(_pick(runner, "I need a refill")) == "after"


def test_a_skip_edge_leaves_without_waiting() -> None:
    runner = FlowRunner(_flow("skip_user_response"))
    runner.current_node_id = "ask"
    assert asyncio.run(_pick(runner, "")) == "after"


def test_the_start_node_advances_without_speech() -> None:
    runner = FlowRunner(_flow("after_user_response"))
    assert runner.current_node_id == "start"
    assert asyncio.run(_pick(runner, "")) == "ask", "start is not interactive"


def test_the_timing_round_trips_through_json() -> None:
    graph = FlowGraph.from_dict(_flow("skip_user_response"))
    assert graph.edges[0].condition is not None
    assert graph.edges[0].condition.timing == "skip_user_response"
    assert graph.to_dict()["edges"][0]["condition"]["timing"] == "skip_user_response"


def test_an_invalid_timing_falls_back_instead_of_exploding() -> None:
    assert EdgeCondition(kind="always", timing="whenever").timing == "after_user_response"
