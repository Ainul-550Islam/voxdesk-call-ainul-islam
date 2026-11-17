"""PART 12 / P12-19 — flex navigation: candidates, cap, selector trust."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.flow_processor import build_flow_processor_for_runtime
from app.builder.flow_runner import FlowRunner
from app.builder.node import MAX_FLEX_NODES, FlowGraph


def _flex(nodes: int = 3) -> dict:
    edges = [{"source": "start", "target": "n0"}]
    edges.extend(
        {"source": "n0", "target": f"n{i}"}
        for i in range(1, nodes)
    )
    return {
        "mode": "flex",
        "nodes": [{"id": "start", "type": "start"}]
        + [{"id": f"n{i}", "type": "conversation", "params": {"prompt": "hi"}} for i in range(nodes)],
        "edges": edges,
    }


def test_a_rigid_flow_offers_no_flex_candidates() -> None:
    graph = {"nodes": [{"id": "s", "type": "start"}, {"id": "a", "type": "end"}], "edges": []}
    assert FlowRunner(graph).flex_candidates() == []


def test_flex_lists_reachable_then_conversational_nodes() -> None:
    runner = FlowRunner(_flex(3))
    candidates = runner.flex_candidates()
    assert candidates[0] == "n0", "declared edges come first"
    assert set(candidates) == {"n0", "n1", "n2"}


def test_flex_excludes_disconnected_conversation_nodes() -> None:
    graph = _flex(3)
    graph["nodes"].append({"id": "orphan", "type": "conversation"})
    runner = FlowRunner(graph)
    assert "orphan" not in runner.flex_candidates()


def test_step_uses_flex_selector_and_records_transition() -> None:
    async def selector(candidates, utterance):
        assert candidates == ["n1", "n2"]
        assert utterance == "next topic"
        return "n2"

    async def run():
        runner = FlowRunner(_flex(3), flex_selector=selector)
        await runner.start()
        result = await runner.step("next topic")
        return runner, result

    runner, result = asyncio.run(run())
    assert runner.current_node_id == "n2"
    assert result.transitions[0].reason == "flex_selector"
    assert result.transitions[0].edge_id is None


def test_selector_failure_falls_back_to_declared_edges() -> None:
    async def broken_selector(candidates, utterance):
        raise RuntimeError("temporary selector failure")

    async def run():
        runner = FlowRunner(_flex(3), flex_selector=broken_selector)
        await runner.start()
        result = await runner.step("next topic")
        return runner, result

    runner, result = asyncio.run(run())
    assert runner.current_node_id == "n1"
    assert result.transitions[0].reason == "always"


def test_the_selector_choice_is_honoured() -> None:
    async def selector(candidates, utterance):
        return "n2"

    runner = FlowRunner(_flex(3), flex_selector=selector)
    assert asyncio.run(runner.flex_next_node("hello")) == "n2"
    assert runner.current_node_id == "n2"


def test_an_unreachable_choice_is_ignored_not_trusted() -> None:
    async def selector(candidates, utterance):
        return "somewhere_else"

    runner = FlowRunner(_flex(3), flex_selector=selector)
    assert asyncio.run(runner.flex_next_node("hello")) is None
    assert runner.current_node_id == "start"


def test_a_flow_too_large_for_flex_is_refused() -> None:
    runner = FlowRunner(_flex(MAX_FLEX_NODES + 5), flex_selector=lambda c, u: c[0])
    with pytest.raises(ValueError, match="flex mode is limited to 20 nodes"):
        asyncio.run(runner.flex_next_node("hi"))


def test_no_selector_means_no_navigation() -> None:
    assert asyncio.run(FlowRunner(_flex(3)).flex_next_node("hi")) is None


def test_flow_processor_builder_wires_flex_selector() -> None:
    async def selector(candidates, utterance):
        return candidates[-1]

    runtime_config = SimpleNamespace(
        raw_config={"mode": "flow", "flow": _flex(2)},
        system_prompt="",
        dynamic_variables={"customer": "Sam"},
        tools=[],
    )
    processor = build_flow_processor_for_runtime(
        runtime_config=runtime_config,
        flex_selector=selector,
    )
    assert processor is not None

    async def run():
        await processor.runner.start()
        result = await processor.runner.step("continue")
        return result

    result = asyncio.run(run())
    assert result.current_node_id == "n1"
    assert result.variables["customer"] == "Sam"
    assert "untrusted data values" in result.system_prompt
    assert '"customer": "Sam"' in result.system_prompt


def test_the_mode_survives_serialisation() -> None:
    graph = FlowGraph.from_dict(_flex(2))
    assert graph.mode == "flex"
    assert graph.to_dict()["mode"] == "flex"
