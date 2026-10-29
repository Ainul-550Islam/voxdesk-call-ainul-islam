"""Reconcile a chunked pytest population against the evidence recorded for it.

Two modes, both honest about what they can prove:

``--results`` (the CI path)
    Reads the machine-generated results JSON written by
    ``scripts/pytest_chunks.py --results-out`` and refuses to sign off when the
    collected population and the recorded population disagree. A test that was
    collected but never produced an outcome counts as ``not_run``; that is a
    failure, because an unrun test is not a passing test. On success it writes
    the small committed summary ``reports/TEST_EVIDENCE_SUMMARY.json`` carrying
    the counts, the SHA-256 of the two large artifacts, the commit SHA and a UTC
    timestamp — never the artifacts themselves, which are far too big to commit
    and are uploaded as workflow artifacts instead.

no arguments (the historical path)
    Merges the complete backend run with newer regression evidence by exact node
    ID, exactly as it always has. Kept because older runs and the
    ``.prompt8b`` evidence tree still depend on it.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".prompt8b"

#: Statuses pytest can report for a node that was collected and executed. Every
#: other value means the node has no trustworthy outcome.
EXECUTED_STATUSES = frozenset({"passed", "failed", "error", "skipped", "xfailed", "xpassed"})


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, streamed so multi-megabyte artifacts stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def commit_sha() -> str:
    """The checked-out commit, or ``unknown`` outside a git work tree.

    An unresolvable commit is reported as a value, never as a fabricated hash:
    a summary that claims a commit it cannot name is worthless to a buyer.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def iso_now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_results(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"results file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"results file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict) or "nodes" not in data:
        raise SystemExit(
            f"{path} is not a pytest_chunks results document (no 'nodes' key)"
        )
    return data


def reconcile(results: dict[str, Any]) -> dict[str, Any]:
    """Compare the collected population with the recorded outcomes.

    Returns a findings dict. ``problems`` is empty only when every collected
    node has exactly one recorded outcome.
    """
    nodes = list(results.get("nodes") or [])
    population = int(results.get("population") or len(nodes))
    statuses: dict[str, int] = collections.Counter()
    not_run: list[str] = []
    unknown_status: list[str] = []
    duplicates: list[str] = []

    seen: dict[str, int] = collections.Counter()
    for row in nodes:
        nodeid = str(row.get("nodeid") or "").strip()
        status = str(row.get("status") or "").strip()
        if not nodeid:
            continue
        seen[nodeid] += 1
        statuses[status] += 1
        if status == "not_run":
            not_run.append(nodeid)
        elif status not in EXECUTED_STATUSES:
            unknown_status.append(f"{nodeid}={status}")

    duplicates = sorted(node for node, count in seen.items() if count > 1)

    problems: list[str] = []
    if population != len(nodes):
        problems.append(
            f"collected {population} node(s) but the results file lists {len(nodes)}"
        )
    if duplicates:
        problems.append(
            f"{len(duplicates)} node(s) recorded more than once "
            f"(first: {duplicates[:3]})"
        )
    if unknown_status:
        problems.append(
            f"{len(unknown_status)} node(s) carry an unrecognised status "
            f"(first: {unknown_status[:3]})"
        )
    if not_run:
        problems.append(
            f"{len(not_run)} collected node(s) have no recorded outcome "
            f"(first: {not_run[:5]})"
        )
    recorded = len(nodes) - len(not_run) - len(unknown_status)
    if population != recorded:
        problems.append(
            f"collected ({population}) != recorded ({recorded}); "
            "the population does not balance"
        )

    return {
        "population": population,
        "recorded": recorded,
        "counts": {key: int(value) for key, value in sorted(statuses.items())},
        "not_run_count": len(not_run),
        "not_run_sample": not_run[:20],
        "duplicate_count": len(duplicates),
        "unknown_status_count": len(unknown_status),
        "problems": problems,
    }


def write_summary(
    *,
    summary_path: Path,
    results_path: Path,
    junit_path: Path | None,
    findings: dict[str, Any],
    results: dict[str, Any],
) -> dict[str, Any]:
    """Write the small committed evidence summary.

    Only digests and counts are committed. The artifacts they describe are
    workflow artifacts: an 8 MB results file has no business in git history.
    """
    artifacts: list[dict[str, Any]] = [
        {
            "path": str(_relative(results_path)),
            "bytes": results_path.stat().st_size,
            "sha256": sha256_of(results_path),
        }
    ]
    if junit_path is not None and junit_path.is_file():
        artifacts.append(
            {
                "path": str(_relative(junit_path)),
                "bytes": junit_path.stat().st_size,
                "sha256": sha256_of(junit_path),
            }
        )

    counts = dict(findings["counts"])
    summary: dict[str, Any] = {
        "schema": "voxdesk.test_evidence_summary/v1",
        "generated_at": iso_now(),
        "commit_sha": commit_sha(),
        "marker": results.get("marker", ""),
        "population": findings["population"],
        "recorded": findings["recorded"],
        "counts": {
            "passed": int(counts.get("passed", 0)),
            "failed": int(counts.get("failed", 0)),
            "error": int(counts.get("error", 0)),
            "skipped": int(counts.get("skipped", 0)),
            "xfailed": int(counts.get("xfailed", 0)),
            "xpassed": int(counts.get("xpassed", 0)),
            "not_run": int(counts.get("not_run", 0)),
        },
        "duration_seconds": results.get("duration_seconds"),
        "reconciled": not findings["problems"],
        "problems": findings["problems"],
        "artifacts": artifacts,
        "note": (
            "Counts come from scripts/pytest_chunks.py --results-out. The "
            "artifacts listed here are uploaded as workflow artifacts, not "
            "committed; verify a copy against the sha256 recorded above."
        ),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _relative(path: Path) -> Path:
    try:
        return path.resolve().relative_to(ROOT)
    except ValueError:
        return path


def legacy_merge() -> None:
    """Merge the complete backend run with newer regression evidence by node ID."""
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconcile_validation_results.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=None,
        help="results JSON produced by `pytest_chunks.py --results-out`",
    )
    parser.add_argument(
        "--junit",
        type=Path,
        default=None,
        help="merged JUnit XML produced by `pytest_chunks.py --junit-out`",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "reports" / "TEST_EVIDENCE_SUMMARY.json",
        help="small committed summary to write (default: reports/TEST_EVIDENCE_SUMMARY.json)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail when any node failed or errored (not only when unaccounted)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.results is None:
        legacy_merge()
        return 0

    results_path = args.results.resolve()
    junit_path = args.junit.resolve() if args.junit else None
    results = load_results(results_path)
    findings = reconcile(results)

    summary = write_summary(
        summary_path=args.summary.resolve(),
        results_path=results_path,
        junit_path=junit_path,
        findings=findings,
        results=results,
    )
    print(
        json.dumps(
            {
                "population": findings["population"],
                "recorded": findings["recorded"],
                "counts": summary["counts"],
                "reconciled": summary["reconciled"],
                "summary": str(_relative(args.summary.resolve())),
            },
            indent=2,
        )
    )

    if findings["problems"]:
        for problem in findings["problems"]:
            print(f"RECONCILE FAILED: {problem}", flush=True)
        return 1
    if args.strict and (summary["counts"]["failed"] or summary["counts"]["error"]):
        print("RECONCILE FAILED: --strict and the run reported failures or errors")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
