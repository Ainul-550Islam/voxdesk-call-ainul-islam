"""Deterministic, sequential pytest sharding for the large backend suite.

The collector may be invoked for the whole suite, but execution always happens
in child processes containing at most ``--max-tests`` node IDs. The parent runs
one child at a time, writes each full stdout/stderr log, and never interprets a
missing terminal summary as a pass. Use ``--target tests/e2e --max-tests 1`` for
an isolated API journey lane, or omit ``--target`` for a full-suite plan/run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_MAX_TESTS = 50
DEFAULT_TIMEOUT_SECONDS = 600
SUMMARY_COUNTERS = ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed")


@dataclass(frozen=True)
class PytestCounts:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0
    xpassed: int = 0

    @property
    def executed(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped + self.xfailed + self.xpassed


def parse_collected_nodeids(output: str) -> list[str]:
    """Extract pytest quiet-collection node IDs, excluding summary text."""
    nodeids: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if "::" not in line or line.startswith(("=", "<", "WARNING ")):
            continue
        if line.endswith(" tests collected") or line.endswith(" test collected"):
            continue
        nodeids.append(line)
    return nodeids


def chunk_nodeids(nodeids: Sequence[str], max_tests: int) -> list[list[str]]:
    """Partition in input order; reject duplicate IDs and invalid chunk sizes."""
    if max_tests < 1:
        raise ValueError("max_tests must be at least 1")
    if len(set(nodeids)) != len(nodeids):
        raise ValueError("pytest collection contains duplicate node IDs")
    return [list(nodeids[start : start + max_tests]) for start in range(0, len(nodeids), max_tests)]


def parse_pytest_counts(output: str) -> PytestCounts:
    """Read only a pytest-style terminal summary with an elapsed-time suffix."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}
    known_outcomes = set(SUMMARY_COUNTERS) | {"warning", "warnings"}
    for line in output.splitlines():
        summary = line.strip().strip("=").strip()
        elapsed = re.search(
            r"\bin\s+[0-9]+(?:\.[0-9]+)?\s*(?:s|sec(?:onds?)?)(?:\s+\(\d+:\d{2}:\d{2}\))?\s*$",
            summary,
        )
        if elapsed is None:
            continue
        outcome_text = summary[: elapsed.start()].strip()
        tokens = [token.strip() for token in outcome_text.split(",") if token.strip()]
        if not tokens:
            continue
        parsed_tokens: list[tuple[int, str]] = []
        for token in tokens:
            match = re.fullmatch(
                r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|warnings?)",
                token,
            )
            if match is None or match.group(2) not in known_outcomes:
                parsed_tokens = []
                break
            parsed_tokens.append((int(match.group(1)), match.group(2)))
        if not parsed_tokens:
            continue
        for value, word in parsed_tokens:
            if word in {"warning", "warnings"}:
                continue
            key = "errors" if word in {"error", "errors"} else word
            counts[key] = value
    return PytestCounts(**counts)


def classify_exit(returncode: int | None, *, timed_out: bool = False) -> str:
    """Keep assertion failures distinct from runtime/resource termination."""
    if timed_out:
        return "TIMEOUT"
    if returncode == 0:
        return "PASS"
    if returncode in {-9, 137, 128 + 9}:
        return "RESOURCE_LIMIT"
    if returncode == 1:
        return "FAIL"
    if returncode is None:
        return "ERROR"
    return "ERROR"


def _collect_command(targets: Sequence[str]) -> list[str]:
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    command.extend(targets)
    return command


def _run_command(nodeids: Sequence[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", *nodeids]


def _summarize_run(
    *,
    chunk_index: int,
    nodeids: Sequence[str],
    returncode: int | None,
    timed_out: bool,
    stdout: str,
    stderr: str,
    elapsed_seconds: float,
    log_path: Path,
) -> dict[str, Any]:
    counts = parse_pytest_counts(stdout + "\n" + stderr)
    status = classify_exit(returncode, timed_out=timed_out)
    summary_executed = counts.executed if counts.executed else None
    if status == "PASS" and counts.executed < len(nodeids):
        status = "UNCONFIRMED"
    unresolved_count = max(0, len(nodeids) - counts.executed)
    return {
        "chunk": chunk_index,
        "collected": len(nodeids),
        "submitted_nodeids": list(nodeids),
        "executed_from_explicit_summary": summary_executed,
        "unconfirmed_count": unresolved_count,
        "counts": asdict(counts),
        "exit_code": returncode,
        "status": status,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "log": str(log_path),
        "terminal_summary_present": summary_executed is not None,
    }


def run_chunked_validation(
    *,
    root: Path,
    targets: Sequence[str],
    max_tests: int,
    timeout_seconds: int,
    output_dir: Path,
    limit_tests: int | None = None,
    plan_only: bool = False,
) -> dict[str, Any]:
    """Collect deterministically, then optionally execute sequential chunks."""
    root = root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    collection_command = _collect_command(targets)
    collect_started = time.monotonic()
    try:
        collection = subprocess.run(
            collection_command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=os.environ.copy(),
        )
        collection_timed_out = False
        collect_returncode: int | None = collection.returncode
        collect_stdout = collection.stdout
        collect_stderr = collection.stderr
    except subprocess.TimeoutExpired as exc:
        collection_timed_out = True
        collect_returncode = None
        collect_stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        collect_stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    collect_elapsed = time.monotonic() - collect_started
    collect_log = output_dir / "collection.log"
    collect_log.write_text(
        "$ " + " ".join(collection_command) + "\n" + collect_stdout + "\nSTDERR:\n" + collect_stderr,
        encoding="utf-8",
    )
    nodeids = parse_collected_nodeids(collect_stdout)
    if collection_timed_out or collect_returncode != 0:
        manifest = {
            "collection_status": "TIMEOUT" if collection_timed_out else "ERROR",
            "collection_exit_code": collect_returncode,
            "collection_elapsed_seconds": round(collect_elapsed, 3),
            "collection_log": str(collect_log),
            "collected": len(nodeids),
            "selected": 0,
            "executed": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "not_run": len(nodeids),
            "unconfirmed": 0,
            "resource_unconfirmed": 0,
            "unconfirmed_nodeids": [],
            "resource_termination": False,
            "full_suite_complete": False,
            "chunks": [],
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    collected = len(nodeids)
    if limit_tests is not None:
        if limit_tests < 0:
            raise ValueError("limit_tests must not be negative")
        selected_nodeids = nodeids[:limit_tests]
    else:
        selected_nodeids = nodeids
    chunks = chunk_nodeids(selected_nodeids, max_tests)

    manifest: dict[str, Any] = {
        "collection_status": "PASS",
        "collection_exit_code": collect_returncode,
        "collection_elapsed_seconds": round(collect_elapsed, 3),
        "collection_log": str(collect_log),
        "collected": collected,
        "selected": len(selected_nodeids),
        "chunk_size_max": max_tests,
        "chunk_count": len(chunks),
        "execution_mode": "plan_only" if plan_only else "sequential_child_processes",
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "not_run": collected if plan_only else collected - len(selected_nodeids),
        "unconfirmed": 0,
        "resource_unconfirmed": 0,
        "unconfirmed_nodeids": [],
        "resource_termination": False,
        "full_suite_complete": False,
        "chunks": [],
    }

    if not plan_only:
        for chunk_index, nodeid_chunk in enumerate(chunks, start=1):
            command = _run_command(nodeid_chunk)
            started = time.monotonic()
            timed_out = False
            try:
                result = subprocess.run(
                    command,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    env=os.environ.copy(),
                )
                returncode: int | None = result.returncode
                stdout = result.stdout
                stderr = result.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                returncode = None
                stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            elapsed = time.monotonic() - started
            log_path = output_dir / f"chunk-{chunk_index:04d}.log"
            log_path.write_text(
                "$ " + " ".join(command) + "\n" + stdout + "\nSTDERR:\n" + stderr,
                encoding="utf-8",
            )
            chunk = _summarize_run(
                chunk_index=chunk_index,
                nodeids=nodeid_chunk,
                returncode=returncode,
                timed_out=timed_out,
                stdout=stdout,
                stderr=stderr,
                elapsed_seconds=elapsed,
                log_path=log_path,
            )
            manifest["chunks"].append(chunk)
            counts = chunk["counts"]
            if chunk["unconfirmed_count"]:
                manifest["unconfirmed"] += chunk["unconfirmed_count"]
                # When pytest terminates without a complete summary it is not
                # possible to identify exactly which submitted node IDs ran;
                # keep the full chunk as an explicitly unconfirmed set.
                manifest["unconfirmed_nodeids"].extend(nodeid_chunk)
                if chunk["status"] == "RESOURCE_LIMIT":
                    manifest["resource_unconfirmed"] += chunk["unconfirmed_count"]
            manifest["passed"] += counts["passed"]
            manifest["failed"] += counts["failed"]
            manifest["errors"] += counts["errors"]
            manifest["skipped"] += counts["skipped"] + counts["xfailed"] + counts["xpassed"]
            if chunk["executed_from_explicit_summary"] is not None:
                manifest["executed"] += chunk["executed_from_explicit_summary"]
            if chunk["status"] == "RESOURCE_LIMIT":
                manifest["resource_termination"] = True
                # Continue to the next isolated chunk; do not convert the
                # terminated chunk into a pass or skip.
            if chunk["status"] in {"TIMEOUT", "ERROR"}:
                # Preserve the later tests as NOT_RUN rather than silently
                # continuing after a broken interpreter/collection environment.
                remaining_chunks = chunks[chunk_index:]
                manifest["not_run"] += sum(len(part) for part in remaining_chunks)
                break

    all_selected_executed_successfully = (
        not plan_only
        and len(manifest["chunks"]) == len(chunks)
        and all(chunk["status"] == "PASS" for chunk in manifest["chunks"])
        and manifest["executed"] == len(selected_nodeids)
        and manifest["failed"] == 0
        and manifest["errors"] == 0
    )
    manifest["full_suite_complete"] = bool(
        all_selected_executed_successfully
        and len(selected_nodeids) == collected
    )
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", default=[], help="Pytest path/node expression; repeat to add targets.")
    parser.add_argument("--max-tests", type=int, default=DEFAULT_MAX_TESTS, help="Maximum node IDs per sequential child process.")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-collection or per-chunk timeout.")
    parser.add_argument("--limit-tests", type=int, help="Run only the first N collected node IDs; the rest remain NOT_RUN.")
    parser.add_argument("--output-dir", default=".prompt8-validation", help="Directory for logs and truthful manifest.")
    parser.add_argument("--plan-only", action="store_true", help="Collect and write a chunk plan without executing tests.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    try:
        manifest = run_chunked_validation(
            root=root,
            targets=args.target,
            max_tests=args.max_tests,
            timeout_seconds=args.timeout_seconds,
            output_dir=output_dir,
            limit_tests=args.limit_tests,
            plan_only=args.plan_only,
        )
    except ValueError as exc:
        print(f"Invalid validation plan: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2))
    if manifest["collection_status"] != "PASS":
        return 2
    if args.plan_only:
        return 0
    return 0 if manifest["full_suite_complete"] else 1


def test_chunk_partition_is_ordered_bounded_and_lossless() -> None:
    nodeids = [f"tests/test_{index}.py::test_case" for index in range(7)]
    chunks = chunk_nodeids(nodeids, 3)
    assert [len(chunk) for chunk in chunks] == [3, 3, 1]
    assert [nodeid for chunk in chunks for nodeid in chunk] == nodeids


def test_collection_parser_does_not_treat_summary_as_a_nodeid() -> None:
    output = """tests/test_example.py::test_one
================= 1 test collected in 0.01s =================
"""
    assert parse_collected_nodeids(output) == ["tests/test_example.py::test_one"]


def test_result_classifier_distinguishes_assertion_from_resource_termination() -> None:
    assert classify_exit(0) == "PASS"
    assert classify_exit(1) == "FAIL"
    assert classify_exit(137) == "RESOURCE_LIMIT"
    assert classify_exit(None, timed_out=True) == "TIMEOUT"


def test_pytest_counter_parser_uses_only_terminal_summary_lines() -> None:
    output = """A test message mentions 88 passed but is not a pytest summary.
================== 2 passed, 1 failed, 3 skipped, 1 error, 2 xfailed, 1 xpassed in 0.4s ==================
"""
    assert parse_pytest_counts(output) == PytestCounts(
        passed=2,
        failed=1,
        errors=1,
        skipped=3,
        xfailed=2,
        xpassed=1,
    )


def test_pytest_counter_parser_accepts_the_clock_suffix_from_long_runs() -> None:
    output = """============================== 1 passed, 43 warnings in 67.16s (0:01:07) ==============================\n"""
    assert parse_pytest_counts(output) == PytestCounts(passed=1)


def test_plan_only_marks_every_collected_test_not_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def fake_run(command: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert "--collect-only" in command
        return subprocess.CompletedProcess(
            command,
            0,
            "tests/test_one.py::test_one\ntests/test_two.py::test_two\n2 tests collected in 0.01s\n",
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    manifest = run_chunked_validation(
        root=tmp_path,
        targets=["tests"],
        max_tests=1,
        timeout_seconds=5,
        output_dir=tmp_path / "plan",
        plan_only=True,
    )
    assert manifest["collection_status"] == "PASS"
    assert manifest["collected"] == 2
    assert manifest["selected"] == 2
    assert manifest["executed"] == 0
    assert manifest["not_run"] == 2
    assert manifest["full_suite_complete"] is False


def test_zero_exit_without_a_terminal_summary_remains_unconfirmed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def fake_run(command: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "tests/test_one.py::test_one\n1 test collected in 0.01s\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manifest = run_chunked_validation(
        root=tmp_path,
        targets=["tests"],
        max_tests=1,
        timeout_seconds=5,
        output_dir=tmp_path / "missing-summary",
    )

    assert manifest["collection_status"] == "PASS"
    assert manifest["executed"] == 0
    assert manifest["passed"] == 0
    assert manifest["unconfirmed"] == 1
    assert manifest["not_run"] == 0
    assert manifest["full_suite_complete"] is False
    assert manifest["chunks"][0]["status"] == "UNCONFIRMED"
    assert manifest["chunks"][0]["terminal_summary_present"] is False


def test_resource_terminated_chunk_is_unconfirmed_not_passed_or_not_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def fake_run(command: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--collect-only" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "tests/test_one.py::test_one\ntests/test_two.py::test_two\n2 tests collected in 0.01s\n",
                "",
            )
        nodeid = command[-1]
        if nodeid.endswith("test_one"):
            return subprocess.CompletedProcess(command, 137, ".", "")
        return subprocess.CompletedProcess(
            command,
            0,
            "============================== 1 passed in 0.01s ==============================\n",
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    manifest = run_chunked_validation(
        root=tmp_path,
        targets=["tests"],
        max_tests=1,
        timeout_seconds=5,
        output_dir=tmp_path / "resource",
    )
    assert manifest["collection_status"] == "PASS"
    assert manifest["resource_termination"] is True
    assert manifest["resource_unconfirmed"] == 1
    assert manifest["unconfirmed"] == 1
    assert manifest["unconfirmed_nodeids"] == ["tests/test_one.py::test_one"]
    assert manifest["executed"] == 1
    assert manifest["passed"] == 1
    assert manifest["failed"] == 0
    assert manifest["not_run"] == 0
    assert manifest["full_suite_complete"] is False


if __name__ == "__main__":
    raise SystemExit(main())
