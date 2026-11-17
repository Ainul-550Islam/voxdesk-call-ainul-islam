"""PART 12 / P12-18 — code-node sandbox: budgets, refusal, determinism, audit."""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from app.builder.flow_runner import FlowRunner
from app.builder.code_node_runtime import (
    CODE_NODE_CPU_LIMIT_S,
    CODE_NODE_MEMORY_LIMIT_MB,
    CodeNodeError,
    CodeNodePolicy,
    QjsRunner,
    audit_entry,
    build_sandbox_source,
    run_code_node,
    source_hash,
)


class FakeRunner:
    engine = "fake"

    def __init__(self, output: str = '{"ok": true}') -> None:
        self.output = output
        self.seen: list[tuple[str, dict]] = []

    def run(self, script, payload, *, timeout_s):
        self.seen.append((script, dict(payload)))
        return self.output


def test_policy_defaults_match_the_specification() -> None:
    policy = CodeNodePolicy()
    assert policy.cpu_limit_s == CODE_NODE_CPU_LIMIT_S == 2.0
    assert policy.memory_limit_mb == CODE_NODE_MEMORY_LIMIT_MB == 8


def test_network_and_filesystem_are_not_optional() -> None:
    with pytest.raises(ValueError, match="never get network"):
        CodeNodePolicy(allow_network=True)
    with pytest.raises(ValueError, match="never get network"):
        CodeNodePolicy(allow_filesystem=True)


def test_api_image_installs_a_bookworm_compatible_sandbox_toolchain() -> None:
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim-bookworm AS quickjs-build" in source
    assert "FROM python:3.12-slim-bookworm AS api" in source
    assert "https://bellard.org/quickjs/quickjs-2025-04-26.tar.xz" in source
    assert "2f20074c25166ef6f781f381c50d57b502cb85d470d639abccebbef7954c83bf" in source
    assert "bubblewrap" in source and "util-linux" in source
    assert "COPY --from=quickjs-build /out/qjs /usr/local/bin/qjs" in source
    assert "command -v bwrap" in source and "command -v prlimit" in source


def test_the_wrapper_seeds_time_and_randomness() -> None:
    script = build_sandbox_source("return {x: 1};", policy=CodeNodePolicy(seed=7))
    assert "__SEED__" not in script and "__FIXED_MS__" not in script
    assert "Math.random = function" in script
    assert script.lstrip().startswith("(function(){")
    assert "})();" in script
    assert "FixedDate.now" in script
    assert "__write(JSON.stringify(__entry" in script
    assert "1700000007000" in script, "the simulated clock is the epoch plus the seed offset"


def test_oversized_source_is_refused_before_it_runs() -> None:
    policy = CodeNodePolicy(max_source_bytes=50)
    with pytest.raises(CodeNodeError, match="over the 50 byte limit"):
        build_sandbox_source("x" * 100, policy=policy)


def test_a_successful_run_returns_the_object_and_an_audit_hash() -> None:
    result = run_code_node("return {greeting: input.name};", {"name": "Dana"}, runner=FakeRunner('{"greeting": "Dana"}'))
    assert result.ok is True
    assert result.output == {"greeting": "Dana"}
    assert result.source_hash == source_hash("return {greeting: input.name};")
    assert result.engine == "fake"


def test_the_payload_reaches_the_runner_as_data() -> None:
    runner = FakeRunner()
    run_code_node("return {};", {"a": 1}, runner=runner)
    assert runner.seen[0][1] == {"a": 1}


def test_flow_runner_executes_code_nodes_and_stores_the_result() -> None:
    fake = FakeRunner('{"greeting": "Dana"}')
    graph = {
        "nodes": [
            {"id": "start", "type": "start"},
            {
                "id": "calculate",
                "type": "code",
                "params": {
                    "code": "return {greeting: input.customer};",
                    "input": {"customer": "{{customer_name}}"},
                    "result_variable": "calculation",
                },
            },
            {"id": "done", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "calculate"},
            {"source": "calculate", "target": "done"},
        ],
    }
    runner = FlowRunner(
        graph,
        code_runner=fake,
        initial_variables={"customer_name": "Dana"},
    )

    result = asyncio.run(runner.start())

    assert result.ended is True
    assert result.variables["calculation"] == {"greeting": "Dana"}
    code_action = next(action for action in runner.executed_actions if action.action_type == "code")
    assert code_action.ok is True
    assert code_action.output["source_hash"] == source_hash("return {greeting: input.customer};")
    assert fake.seen[0][1]["customer"] == "Dana"
    assert fake.seen[0][1]["customer_name"] == "Dana"


def test_a_non_object_return_is_an_error_not_a_crash() -> None:
    result = run_code_node("return 1;", runner=FakeRunner("[1,2,3]"))
    assert result.ok is False
    assert "must return a JSON object" in result.error


def test_invalid_json_is_reported_with_the_reason() -> None:
    result = run_code_node("return 1;", runner=FakeRunner("not json"))
    assert result.ok is False
    assert "did not return JSON" in result.error


def test_a_timeout_is_a_result_not_an_exception() -> None:
    class Slow:
        engine = "fake"

        def run(self, *args, **kwargs):
            raise subprocess.TimeoutExpired("qjs", 2.0)

    result = run_code_node("while(true){}", runner=Slow())
    assert result.ok is False
    assert "was killed" in result.error


def test_a_missing_engine_says_so_plainly() -> None:
    result = run_code_node("return {};", runner=QjsRunner(binary="/nonexistent/qjs"))
    assert result.ok is False
    assert "no QuickJS engine" in result.error


def test_qjs_runner_injects_json_and_enforces_sandbox_budgets(monkeypatch) -> None:
    from app.builder import code_node_runtime

    runner = QjsRunner(
        binary="/usr/bin/qjs",
        sandbox_binary="/usr/bin/bwrap",
        limit_binary="/usr/bin/prlimit",
    )
    monkeypatch.setattr(
        code_node_runtime.shutil,
        "which",
        lambda path: path if path in {"/usr/bin/qjs", "/usr/bin/bwrap", "/usr/bin/prlimit"} else None,
    )
    captured: dict[str, object] = {}

    class _FakeProcess:
        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            stdout_read, stdout_write = os.pipe()
            os.write(stdout_write, b"{}")
            os.close(stdout_write)
            stderr_read, stderr_write = os.pipe()
            os.close(stderr_write)
            self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
            self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(code_node_runtime.subprocess, "Popen", _FakeProcess)
    output = runner.run(
        "JSON.stringify(__entry(JSON.parse(globalThis.__input)));",
        {"name": "Dana"},
        timeout_s=2.0,
        memory_limit_mb=8,
    )

    command = captured["command"]
    assert output == "{}"
    assert captured["stdin"] == subprocess.DEVNULL
    assert "--unshare-net" in command
    assert "--unshare-pid" in command
    assert "--unshare-ipc" in command and "--unshare-uts" in command
    assert "--memory-limit" in command and "8M" in command
    assert "--cpu=2:2" in command
    assert "globalThis.__input" in command[-1]


def test_engine_errors_become_results() -> None:
    class Broken:
        engine = "fake"

        def run(self, *args, **kwargs):
            raise CodeNodeError("SyntaxError: unexpected token")

    result = run_code_node("bad(", runner=Broken())
    assert result.ok is False and "SyntaxError" in result.error


def test_output_is_size_capped() -> None:
    result = run_code_node(
        "return {};",
        runner=FakeRunner('{"blob": "' + "x" * 100 + '"}'),
        policy=CodeNodePolicy(max_output_bytes=40),
    )
    assert result.ok is False
    assert "output exceeded" in result.error


def test_the_audit_entry_names_the_source_that_ran() -> None:
    result = run_code_node("return {};", runner=FakeRunner())
    entry = audit_entry(result, node_id="n_code", call_id="call-9")
    assert entry["kind"] == "code_node_execution"
    assert entry["node_id"] == "n_code"
    assert entry["call_id"] == "call-9"
    assert len(entry["source_hash"]) == 32
    assert entry["ok"] is True


def test_the_same_source_always_hashes_the_same_way() -> None:
    assert source_hash("return 1;") == source_hash("return 1;")
    assert source_hash("return 1;") != source_hash("return 2;")


@pytest.mark.skipif(not QjsRunner().available(), reason="qjs, bubblewrap, and prlimit are required")
def test_real_quickjs_bubblewrap_run_and_stream_output_cap() -> None:
    source = "return {answer: input.value * 2, now: Date.now(), random: Math.random()};"
    policy = CodeNodePolicy(seed=7)
    first = run_code_node(source, {"value": 21}, policy=policy)
    second = run_code_node(source, {"value": 21}, policy=policy)

    assert first.ok and second.ok
    assert first.output == second.output
    assert first.output["answer"] == 42
    assert first.output["now"] == 1700000007000
    assert isinstance(first.output["random"], float)

    noisy = run_code_node(
        "while (true) print('x'.repeat(1024));",
        policy=CodeNodePolicy(max_output_bytes=1024),
    )
    assert noisy.ok is False
    assert "output exceeded 1024 bytes" in noisy.error

    timed = run_code_node(
        "while (true) {}",
        policy=CodeNodePolicy(cpu_limit_s=0.15),
    )
    assert timed.ok is False
    assert "exceeded 0.15s and was killed" in timed.error
