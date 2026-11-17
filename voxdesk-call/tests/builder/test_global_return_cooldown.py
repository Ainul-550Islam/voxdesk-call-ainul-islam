"""PART 12 / P12-19 — global nodes: return path, return stack, cooldown."""

from __future__ import annotations

import asyncio

from app.builder.flow_runner import FlowRunner


def _flow(*, return_path: str = "", cooldown: int = 0) -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "ask", "type": "conversation", "params": {"prompt": "How can I help?"}},
            {
                "id": "reminder",
                "type": "conversation",
                "is_global": True,
                "params": {
                    "prompt": "Are you still there?",
                    "global_config": {
                        "enabled": True,
                        "condition_type": "prompt",
                        "prompt": "the caller has gone quiet",
                        "return_path": return_path,
                        "cooldown_steps": cooldown,
                    },
                },
            },
            {"id": "end", "type": "end"},
        ],
        "edges": [{"source": "start", "target": "ask"}],
    }


async def _judge(prompt: str, utterance: str) -> bool:
    return True


def test_global_trigger_judge_receives_configured_examples() -> None:
    seen: dict[str, object] = {}

    async def judge(prompt, utterance, variables, examples):
        seen["prompt"] = prompt
        seen["utterance"] = utterance
        seen["examples"] = examples
        return True

    flow = _flow()
    flow["nodes"][2]["params"]["global_config"]["examples"] = [
        "I need a human",
        "Can someone help?",
    ]
    runner = FlowRunner(flow, judge=judge)
    runner.current_node_id = "ask"
    runner.turn_index = 1

    result = asyncio.run(runner._check_global_preemption("please help"))

    assert result is not None and result.node_id == "reminder"
    assert seen["examples"] == ["I need a human", "Can someone help?"]


def test_a_global_node_remembers_where_it_came_from() -> None:
    runner = FlowRunner(_flow(return_path="ask"), judge=_judge)
    runner.current_node_id = "ask"
    node = asyncio.run(runner._check_global_preemption("hello"))
    assert node is not None and node.node_id == "reminder"
    assert runner.return_stack == ["ask"]
    assert runner.resume_after_global() == "ask"
    assert runner.current_node_id == "ask"


def test_step_automatically_resumes_the_return_stack_after_global_node() -> None:
    async def run():
        runner = FlowRunner(_flow(return_path="ask"), judge=_judge)
        await runner.start()
        interrupted = await runner.step("please help")
        assert interrupted.current_node_id == "reminder"
        assert runner.return_stack == ["ask"]
        resumed = await runner.step("thank you")
        return runner, resumed

    runner, result = asyncio.run(run())
    assert runner.current_node_id == "ask"
    assert result.transitions[0].reason == "return_from_global"
    assert runner.return_stack == []


def test_an_explicit_return_path_wins() -> None:
    runner = FlowRunner(_flow(return_path="end"), judge=_judge)
    runner.current_node_id = "ask"
    asyncio.run(runner._check_global_preemption("hello"))
    assert runner.return_stack == ["end"]
    assert runner.resume_after_global() == "end"


def test_the_stack_is_empty_when_nothing_preempted() -> None:
    runner = FlowRunner(_flow(return_path="ask"), judge=_judge)
    assert runner.resume_after_global() is None
    assert runner.return_stack == []


def test_a_cooldown_keeps_the_global_node_quiet() -> None:
    runner = FlowRunner(_flow(return_path="ask", cooldown=3), judge=_judge)
    runner.current_node_id = "ask"
    runner.turn_index = 5
    assert asyncio.run(runner._check_global_preemption("hello")) is not None
    runner.current_node_id = "ask"
    runner.turn_index = 6
    assert asyncio.run(runner._check_global_preemption("hello")) is None, "still cooling down"
    runner.current_node_id = "ask"
    runner.turn_index = 8
    assert asyncio.run(runner._check_global_preemption("hello")) is not None, "cooldown elapsed"


def test_zero_cooldown_fires_every_turn() -> None:
    runner = FlowRunner(_flow(return_path="ask", cooldown=0), judge=_judge)
    for turn in (1, 2, 3):
        runner.current_node_id = "ask"
        runner.turn_index = turn
        assert asyncio.run(runner._check_global_preemption("hello")) is not None


def test_a_return_target_that_does_not_exist_is_reported_by_validation() -> None:
    from app.builder.flow_validation import validate_flow

    result = validate_flow(_flow(return_path="ghost"))
    assert any(issue.code == "GLOBAL_RETURN_TARGET_MISSING" for issue in result.errors)


def test_cooldown_bounds_are_validated() -> None:
    from app.builder.node import GlobalNodeConfig

    assert GlobalNodeConfig(prompt="x", cooldown_steps=-1).validate("n")
    assert GlobalNodeConfig(prompt="x", cooldown_steps=51).validate("n")
    assert GlobalNodeConfig(prompt="x", cooldown_steps=5).validate("n") == []
