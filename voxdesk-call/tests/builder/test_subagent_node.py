"""PART 12 / P12-18 — subagent delegation happens inside one turn."""

from __future__ import annotations

import asyncio

from app.builder.flow_runner import FlowRunner


def _flow(tools=("book_appointment",), terminal=False) -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {
                "id": "delegate",
                "type": "subagent",
                "params": {
                    "agent_id": "agent_booking",
                    "version": 3,
                    "prompt": "Book a slot for {{caller_name}}.",
                    "tools": list(tools),
                    "terminal": terminal,
                },
            },
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "delegate"},
            {"source": "delegate", "target": "end"},
        ],
    }


def test_the_delegation_carries_prompt_tools_and_utterance() -> None:
    seen: dict = {}

    async def handler(agent_id, payload):
        seen["agent_id"] = agent_id
        seen["payload"] = payload
        return {"ok": True, "reply": "booked"}

    runner = FlowRunner(_flow(), subagent_handler=handler, initial_variables={"caller_name": "Dana"})
    runner.current_node_id = "delegate"
    action, speak_text = asyncio.run(
        runner._execute_node_action(runner.current_node, "book me in")
    )

    assert action is not None and action.ok is True
    assert speak_text == "booked"
    assert seen["agent_id"] == "agent_booking"
    assert seen["payload"]["prompt"] == "Book a slot for Dana."
    assert seen["payload"]["tools"] == ["book_appointment"]
    assert seen["payload"]["version"] == 3


def test_context_is_passed_only_when_asked() -> None:
    captured: dict = {}

    async def handler(agent_id, payload):
        captured.update(payload)
        return {}

    graph = _flow()
    graph["nodes"][1]["params"]["pass_context"] = False
    runner = FlowRunner(graph, subagent_handler=handler, initial_variables={"secret": "x"})
    runner.current_node_id = "delegate"
    asyncio.run(runner._execute_node_action(runner.current_node, "hi"))
    assert captured["variables"] == {}


def test_a_non_terminal_subagent_returns_control_to_the_flow() -> None:
    runner = FlowRunner(_flow(terminal=False))
    runner.current_node_id = "delegate"
    asyncio.run(runner._execute_node_action(runner.current_node, "hi"))
    assert runner.ended is False


def test_a_terminal_subagent_does_not_claim_success_when_no_adapter_exists() -> None:
    graph = _flow(terminal=True)
    graph["edges"] = [{"source": "start", "target": "delegate"}]
    runner = FlowRunner(graph)
    runner.current_node_id = "delegate"
    action, _ = asyncio.run(runner._execute_node_action(runner.current_node, "hi"))
    assert action is not None and action.ok is False
    assert action.output["error"] == "subagent_runtime_unavailable"
    assert runner.ended is False
    assert runner.variables["subagent_ok"] is False


def test_validation_requires_a_target_agent() -> None:
    from app.builder.flow_validation import validate_flow

    graph = _flow()
    graph["nodes"][1]["params"].pop("agent_id")
    result = validate_flow(graph)
    assert any(issue.code == "SUBAGENT_TARGET_MISSING" for issue in result.errors)


def test_validation_limits_the_delegated_functions() -> None:
    from app.builder.flow_validation import SUBAGENT_ALLOWED_FUNCTIONS, validate_flow

    good = validate_flow(_flow(tools=tuple(SUBAGENT_ALLOWED_FUNCTIONS)))
    assert not any(issue.code == "SUBAGENT_FUNCTION_NOT_ALLOWED" for issue in good.errors)

    bad = validate_flow(_flow(tools=("delete_everything",)))
    assert any(issue.code == "SUBAGENT_FUNCTION_NOT_ALLOWED" for issue in bad.errors)
