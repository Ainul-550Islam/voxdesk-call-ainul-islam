"""Merge the complete backend run with newer regression evidence by exact node ID."""
from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".prompt8b"


def main():
    final_path = ROOT / "FINAL_TEST_RESULTS.json"
    baseline_path = EVIDENCE / "BASE_FULL_RESULTS.json"
    if not baseline_path.exists():
        baseline_path.write_bytes(final_path.read_bytes())
    baseline = json.loads(baseline_path.read_text())
    nodes = (ROOT / "tests_manifest.txt").read_text().splitlines()
    assert len(nodes) == len(set(nodes))
    results = {r["nodeid"]: r for r in baseline["tests"]}
    for node in nodes:
        results.setdefault(node, {"nodeid": node, "status": "not_run", "duration": 0,
                                  "chunk": None, "attempts": [], "failure": None,
                                  "error_class": "NOT_EXECUTED"})
    regression_chunks = json.loads((EVIDENCE / "regression" / "summary.json").read_text())
    summaries = {row["label"]: row for row in regression_chunks}
    for path in sorted((EVIDENCE / "regression").glob("regression-*.jsonl")):
        events = [json.loads(line) for line in path.read_text().splitlines()]
        collection = next(row["nodeids"] for row in events if row["kind"] == "collection")
        sessions = [e for e in events if e["kind"] == "session"]
        by_node = collections.defaultdict(list)
        for event in events:
            if event["kind"] == "phase":
                by_node[event["nodeid"]].append(event)
        for node in collection:
            assert node in results, f"Uncollected regression node: {node}"
            phases = by_node[node]
            failures = [p for p in phases if p["outcome"] == "failed"]
            skips = [p for p in phases if p["outcome"] == "skipped"]
            completed = any(p["phase"] == "teardown" for p in phases)
            called = any(p["phase"] == "call" for p in phases)
            status = "passed" if completed and called else "not_run"
            failure = None
            error = None if status == "passed" else "INCOMPLETE_EXECUTION"
            if failures:
                status = "error" if any(p["phase"] != "call" for p in failures) else "failed"
                failure = "\n".join(p["failure"] or "" for p in failures)
                error = "FIXTURE_ERROR" if status == "error" else "ASSERTION_OR_TEST_ERROR"
            elif skips:
                status, error = "skipped", "SKIP"
                failure = "\n".join(p["failure"] or "" for p in skips)
            if any(p["wasxfail"] for p in phases):
                status = "xfailed" if skips else "xpassed"
                error = "EXPECTED_FAILURE_MARKER"
            attempt = {"chunk": path.stem, "status": status,
                       "duration": sum(p["duration"] for p in phases),
                       "exit_code": summaries[path.stem]["exit_code"],
                       "session_finished": bool(sessions), "phases": phases}
            result = results[node]
            result["attempts"].append(attempt)
            result.update(status=status, duration=attempt["duration"], chunk=path.stem,
                          failure=failure, error_class=error)
    current = [results[node] for node in nodes]
    for result in current:
        result["attempt_count"] = len(result["attempts"])
    counts = {key: 0 for key in ("passed", "failed", "error", "skipped", "xfailed", "xpassed", "not_run")}
    counts.update(collections.Counter(r["status"] for r in current))
    output = {"population": len(nodes), "collection_stable": True,
              "counts": counts, "unique_accounted": len(current),
              "scope": "Current exact-node collection; full-population run plus changed-contract regression. Earlier interrupted exploratory runs are retained separately, not counted as passes.",
              "chunks": baseline["chunks"] + regression_chunks,
              "tests": current}
    final_path.write_text(json.dumps(output, indent=2))
    groups = collections.defaultdict(collections.Counter)
    for result in current:
        groups[result["nodeid"].split("::")[0]][result["status"]] += 1
    (EVIDENCE / "test-file-accounting.json").write_text(json.dumps(groups, indent=2))
    print(json.dumps({"population": len(nodes), "counts": counts}, indent=2))
    assert counts["not_run"] == 0, "Unrun nodes cannot be hidden"


if __name__ == "__main__":
    main()
