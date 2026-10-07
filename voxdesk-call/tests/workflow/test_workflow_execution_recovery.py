from __future__ import annotations

import pytest

from app.builder.node import FlowEdge, FlowGraph, FlowNode
from app.builder.workflow_executor import WorkflowExecutor


@pytest.mark.asyncio
async def test_executor_execute_and_resume() -> None:
    ex = WorkflowExecutor()
    r = await ex.execute("ex-1", "t-1")
    assert r["status"] == "running"
    assert r["node"] == "start"
    assert r["recovered"] is False

    resumed = await ex.resume("ex-1")
    assert resumed["resumed"] is True
    assert resumed["status"] == "recovering"


@pytest.mark.asyncio
async def test_executor_checkpoint_and_recovery_progress() -> None:
    ex = WorkflowExecutor()
    await ex.execute("ex-2", "t-1", initial_node="greet")
    ok_first = await ex.checkpoint("ex-2", "verify_identity", {"verified": True})
    ok_second = await ex.checkpoint("ex-2", "route_billing", {"department": "billing"})
    assert ok_first is True
    assert ok_second is True

    checkpoints = ex.get_checkpoints("ex-2")
    assert len(checkpoints) == 2
    assert checkpoints[0]["node_id"] == "verify_identity"
    assert checkpoints[1]["node_id"] == "route_billing"
    assert checkpoints[1]["data"]["department"] == "billing"


@pytest.mark.asyncio
async def test_executor_rejects_empty_checkpoint_identifiers() -> None:
    ex = WorkflowExecutor()
    assert await ex.checkpoint("", "node-1", {"k": "v"}) is False
    assert await ex.checkpoint("ex-3", "", {"k": "v"}) is False


def test_flow_graph_and_node_validation() -> None:
    start = FlowNode("start", {"label": "Entry"}, node_id="n_start")
    tool = FlowNode("function_call", {"tool_name": "lookup_order"}, node_id="n_lookup")
    bad_tool = FlowNode("function_call", {}, node_id="n_broken")
    end = FlowNode("end", {}, node_id="n_end")

    assert start.is_valid_type() is True
    assert tool.validate() == []
    assert len(bad_tool.validate()) == 1

    valid_graph = FlowGraph(
        nodes=[start, tool, end],
        edges=[
            FlowEdge(source_id="n_start", target_id="n_lookup"),
            FlowEdge(source_id="n_lookup", target_id="n_end"),
        ],
    )
    assert valid_graph.validate_graph() == []
    assert len(valid_graph.outgoing_edges("n_start")) == 1
