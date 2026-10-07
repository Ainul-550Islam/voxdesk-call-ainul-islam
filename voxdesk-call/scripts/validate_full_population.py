"""Deterministic full-population execution with durable phase-level evidence.

Run from the repository root. Files never share a child interpreter, avoiding
cross-file SDK/import retention; files above 50 nodes use ordered subchunks.
Every collected node is scheduled, including opt-in external provider checks.
Timeouts are recorded distinctly, never silently interpreted as assertion failures.
"""
from __future__ import annotations

import collections
import gc
import multiprocessing
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".prompt8b" / "backend"
ENV = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
           MKL_NUM_THREADS="1", VOXDESK_REAL_INTEGRATION="1")
COMMAND = [sys.executable, "-m", "pytest", "-p", "scripts.validation_evidence"]


def run(label, args, timeout):
    events = EVIDENCE / f"{label}.jsonl"
    stamp = time.time_ns()
    for previous in (events, EVIDENCE / f"{label}.log"):
        if previous.exists():
            history = EVIDENCE / "history"
            history.mkdir(exist_ok=True)
            previous.rename(history / f"{stamp}-{previous.name}")
    env = dict(ENV, VALIDATION_EVENTS=str(events))
    start = time.monotonic()
    if "--collect-only" in args:
        with (EVIDENCE / f"{label}.log").open("w") as log:
            try:
                result = subprocess.run(COMMAND + args, cwd=ROOT, env=env,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = "TIMEOUT"
    else:
        process = multiprocessing.get_context("fork").Process(
            target=child, args=(label, args, env))
        process.start()
        process.join(timeout)
        if process.is_alive():
            process.kill()
            process.join()
            code = "TIMEOUT"
        else:
            code = process.exitcode
    rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
    return {"label": label, "exit_code": code, "duration": time.monotonic() - start}, rows


def child(label, args, env):
    """A pristine fork runs pytest normally, including setup and teardown."""
    os.environ.update(env)
    os.chdir(ROOT)
    with (EVIDENCE / f"{label}.log").open("w", buffering=1) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        import pytest
        code = pytest.main(["-p", "scripts.validation_evidence", *args])
        sys.stdout.flush()
        sys.stderr.flush()
    raise SystemExit(int(code))


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    collections_seen = []
    for n in (1, 2):
        summary, rows = run(f"collection-{n}", ["--collect-only", "-q"], 300)
        if summary["exit_code"] != 0:
            raise RuntimeError(f"Collection failed: {summary}")
        nodes = next(row["nodeids"] for row in rows if row["kind"] == "collection")
        if len(nodes) != len(set(nodes)):
            raise RuntimeError("Duplicate node IDs in collection")
        collections_seen.append(nodes)
    if collections_seen[0] != collections_seen[1]:
        raise RuntimeError("Unstable collection; cannot safely account for population")
    nodes = collections_seen[0]
    (ROOT / "tests_manifest.txt").write_text("\n".join(nodes) + "\n")
    by_file = collections.defaultdict(list)
    for node in nodes:
        by_file[node.split("::")[0]].append(node)
    results = {node: {"nodeid": node, "status": "not_run", "duration": 0.0,
                      "chunk": None, "attempts": [], "failure": None,
                      "error_class": "NOT_EXECUTED"} for node in nodes}
    chunks = []
    for file_nodes in by_file.values():
        for offset in range(0, len(file_nodes), 50):
            chunks.append(file_nodes[offset:offset + 50])
    (EVIDENCE / "chunks.json").write_text(json.dumps(chunks, indent=2))
    # Application imports alone use ~1.27 GiB: preload once, before any DB
    # connection, event loop, test module, or pytest fixture is created. Forks
    # share these read-only pages but never return modified state to the parent.
    # Collection already imports this same application in ordinary full pytest.
    os.environ.update(ENV)
    sys.path.insert(0, str(ROOT))
    import app.main  # noqa: F401
    gc.collect()
    gc.freeze()
    summary_rows = []
    prior_path = ROOT / "FINAL_TEST_RESULTS.json"
    if prior_path.exists():
        prior = json.loads(prior_path.read_text())
        if [r["nodeid"] for r in prior["tests"]] != nodes:
            raise RuntimeError("Cannot resume a different collection")
        results = {r["nodeid"]: r for r in prior["tests"]}
        summary_rows = prior["chunks"]
    for index, chunk in enumerate(chunks, 1):
        label = f"chunk-{index:04d}"
        if any(c["label"] == label and c["exit_code"] == 0 for c in summary_rows) and all(results[node]["chunk"] == label and results[node]["status"] != "not_run" for node in chunk):
            continue
        summary, events = run(label, ["-q", "-ra", *chunk], 240)
        summary_rows.append(summary)
        phases = collections.defaultdict(list)
        for event in events:
            if event["kind"] == "phase":
                phases[event["nodeid"]].append(event)
        for node in chunk:
            rows = phases[node]
            failed = [r for r in rows if r["outcome"] == "failed"]
            skipped = [r for r in rows if r["outcome"] == "skipped"]
            complete = any(r["phase"] == "teardown" for r in rows)
            call = next((r for r in rows if r["phase"] == "call"), None)
            status = "passed" if call and complete else "not_run"
            error = None if status == "passed" else "INCOMPLETE_EXECUTION"
            failure = None
            if failed:
                status = "error" if any(r["phase"] != "call" for r in failed) else "failed"
                error = "FIXTURE_ERROR" if status == "error" else "ASSERTION_OR_TEST_ERROR"
                failure = "\n".join(r["failure"] or "" for r in failed)
            elif skipped:
                status, error = "skipped", "SKIP"
                failure = "\n".join(r["failure"] or "" for r in skipped)
            if any(r["wasxfail"] for r in rows):
                status = "xfailed" if skipped else "xpassed"
                error = "EXPECTED_FAILURE_MARKER"
            if not complete and summary["exit_code"] == "TIMEOUT":
                status, error = "not_run", "RESOURCE_TIMEOUT"
            if node.startswith("tests/test_real_providers.py") and status == "skipped":
                error = "EXTERNAL_CREDENTIALS_BLOCKED"
            attempt = {"chunk": label, "status": status, "duration": sum(r["duration"] for r in rows),
                       "exit_code": summary["exit_code"], "phases": rows}
            results[node].update(status=status, duration=attempt["duration"], chunk=label,
                                 attempts=results[node]["attempts"] + [attempt], failure=failure, error_class=error)
        payload = {"population": len(nodes), "collection_stable": True,
                   "counts": dict(collections.Counter(r["status"] for r in results.values())),
                   "chunks": summary_rows, "tests": list(results.values())}
        (ROOT / "FINAL_TEST_RESULTS.json").write_text(json.dumps(payload, indent=2))
        print(f"{index}/{len(chunks)} {label} {summary['exit_code']} {payload['counts']}", flush=True)
    return 0 if all(r["status"] == "passed" for r in results.values()) and all(c["exit_code"] == 0 for c in summary_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
