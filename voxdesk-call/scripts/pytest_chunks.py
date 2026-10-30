#!/usr/bin/env python3
"""Deterministic chunked pytest runner with per-chunk isolation, bisection, and JUnit XML roll-up.

Usage:
    python scripts/pytest_chunks.py \
      --chunk-size 25 \
      --per-chunk-timeout 180 \
      --marker "not real_provider and not live" \
      --junit-dir reports/check/chunks \
      --summary reports/check/pytest_summary.json

Full-population accounting (what CI uses to prove no test is silently lost):

    python scripts/pytest_chunks.py \
      --chunk-size 40 \
      --junit-out reports/check/junit/full.xml \
      --results-out reports/check/full_results.json

Every collected node id is enumerated up front with ``pytest --collect-only`` and
chunked by node id (not by file), so a chunk's contents never depend on the
filesystem walk order. After the run, each collected node must appear in exactly
one JUnit ``<testcase>``. A node that neither ran, nor failed, nor was skipped
is *unaccounted for* and the command exits non-zero: an unrun test is never
allowed to disappear behind a green summary.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
import queue
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.junit_xml import iter_suites  # noqa: E402


def _merge_junit(src_paths: list[Path], dst_path: Path) -> None:
    """Combine multiple pytest JUnit XML files into a single ``<testsuites>`` file."""
    body: list[str] = ['<?xml version="1.0" encoding="utf-8"?>', "<testsuites>"]
    for src in src_paths:
        if src.is_file():
            for suite in iter_suites(src):
                body.append(ET.tostring(suite, encoding="unicode"))
    body.append("</testsuites>\n")
    dst_path.write_text("\n".join(body), encoding="utf-8")

#: Node-id shapes emitted by ``pytest --collect-only -q``. A node id always
#: carries a ``.py::`` separator (and may contain spaces inside ``[...]``
#: parameter labels); plain ``-q`` summary lines never do.
_NODE_LINE = re.compile(r"^(?P<node>\S+\.py::\S.*?)\s*$")

#: Test files that spawn nested ``sys.executable`` interpreters; isolating them
#: into dedicated sub-batches prevents parent + child RSS from exceeding 2 GiB.
_SUBPROCESS_HEAVY_FILES = frozenset({
    "tests/compliance_v2/test_evidence_run_fail_closed.py",
    "tests/live_proof/test_pstn_probe_refuses_without_keys.py",
    "tests/test_alembic_models_in_sync.py",
    "tests/test_backend_runtime_closure.py",
    "tests/test_boot_and_mount.py",
    "tests/test_memory_safe_final_validation.py",
    "tests/test_stt_lazy_import.py",
    "tests/truth/test_part9_packaging_sales_proof.py",
})

#: Exit code used when the run itself went fine but the population does not
#: balance. Distinct from 1 (test failures) so CI can tell them apart.
EXIT_UNACCOUNTED = 3


def discover_test_files(tests_dir: Path) -> list[str]:
    resolved_dir = tests_dir.resolve()
    files: set[Path] = set()
    for pattern in ("test_*.py", "*_test.py"):
        for path in resolved_dir.rglob(pattern):
            if "__pycache__" in path.parts:
                continue
            if path.is_file():
                files.add(path.resolve())
    return sorted(str(p.relative_to(ROOT)) for p in files)


def has_pytest_timeout() -> bool:
    return importlib.util.find_spec("pytest_timeout") is not None


def _parse_deselected(stdout: str) -> int:
    match = re.search(r"(\d+)\s+deselected", stdout)
    return int(match.group(1)) if match else 0


def _base_env() -> dict[str, str]:
    """The child interpreter environment, with the CI database wired when open."""
    env = dict(os.environ)
    env["TZ"] = "UTC"
    if "DATABASE_URL" not in env:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", 5432)) == 0:
                env["DATABASE_URL"] = (
                    "postgresql+asyncpg://voxdesk:voxdesk-ci@localhost:5432/voxdesk_ci"
                )
    return env


def collect_nodes(
    *,
    tests_dir: Path,
    marker: str,
    env: dict[str, str],
    timeout: int = 900,
) -> list[str]:
    """Enumerate every node pytest would run, by id, in a stable order.

    Collection is run twice and compared. An unstable collection (a test whose
    id depends on dict ordering or the clock) cannot be reconciled against a
    results file, so it is refused here rather than silently mis-counted later.
    """
    seen: list[list[str]] = []
    for _ in range(2):
        cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", str(tests_dir)]
        if marker.strip():
            cmd.extend(["-m", marker])
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        nodes: list[str] = []
        for line in (proc.stdout or "").splitlines():
            match = _NODE_LINE.match(line.strip())
            if match:
                nodes.append(match.group("node"))
        if not nodes:
            raise RuntimeError(
                "pytest collected zero nodes; refusing to record an empty population "
                f"(exit={proc.returncode})\n{(proc.stdout or proc.stderr)[-800:]}"
            )
        seen.append(nodes)
    if seen[0] != seen[1]:
        first = set(seen[0])
        second = set(seen[1])
        raise RuntimeError(
            "unstable collection: node ids differ between two collection passes "
            f"(only-in-first={sorted(first - second)[:5]} "
            f"only-in-second={sorted(second - first)[:5]})"
        )
    return seen[0]


def chunk_nodes(nodes: list[str], chunk_size: int) -> list[list[str]]:
    """Split node ids into deterministic, contiguous chunks."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    ordered = sorted(nodes)
    return [ordered[i : i + chunk_size] for i in range(0, len(ordered), chunk_size)]


def _junit_key_for_node(node: str) -> tuple[str, str]:
    """Return the ``(classname, name)`` pair pytest writes into JUnit XML for ``node``."""
    file_part, _, rest = node.partition("::")
    parts = rest.split("::")
    mod_dotted = file_part.removesuffix(".py").replace("/", ".")
    return (".".join([mod_dotted, *parts[:-1]]), parts[-1])


def _reconstruct_nodeid(classname: str, file_attr: str, name: str) -> tuple[str, str]:
    """Reconstruct ``tests/.../test_mod.py[::Class]::test_fn`` from JUnit attributes."""
    if classname:
        parts = classname.split(".")
        for idx in range(len(parts), 0, -1):
            candidate = Path(*parts[:idx]).with_suffix(".py")
            if (ROOT / candidate).is_file():
                rel_file = candidate.as_posix()
                cls_parts = parts[idx:]
                suffix = "::".join([*cls_parts, name]) if cls_parts else name
                return f"{rel_file}::{suffix}", rel_file
    if file_attr:
        return f"{file_attr}::{name}", file_attr
    fallback_file = classname.replace(".", "/") + ".py" if classname else ""
    return f"{classname}::{name}", fallback_file


def parse_junit_xml(xml_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tests": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "time_seconds": 0.0,
        "testcases": [],
        "failures": [],
    }
    if not xml_path.is_file():
        return result

    suites = iter_suites(xml_path)
    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            file_attr = case.attrib.get("file", "")
            time_val = float(case.attrib.get("time", "0") or 0.0)
            nodeid, resolved_file = _reconstruct_nodeid(classname, file_attr, name)

            result["tests"] += 1
            result["time_seconds"] += time_val

            failure_el = case.find("failure")
            error_el = case.find("error")
            skipped_el = case.find("skipped")

            status = "passed"
            if failure_el is not None:
                msg = (failure_el.attrib.get("message") or "").strip()
                text = (failure_el.text or "").strip()
                if "XPASS" in msg or "XPASS" in text:
                    status = "xpassed"
                    result["xpassed"] += 1
                else:
                    status = "failed"
                    result["failed"] += 1
                    exc_type = failure_el.attrib.get("type") or "AssertionError"
                    tb_lines = [line for line in (text or msg).splitlines() if line.strip()][:10]
                    result["failures"].append(
                        {
                            "nodeid": nodeid,
                            "classname": classname,
                            "name": name,
                            "kind": "failure",
                            "exception_type": exc_type,
                            "message": msg[:400],
                            "traceback_head": tb_lines,
                        }
                    )
            elif error_el is not None:
                msg = (error_el.attrib.get("message") or "").strip()
                text = (error_el.text or "").strip()
                status = "error"
                result["errors"] += 1
                exc_type = error_el.attrib.get("type") or "RuntimeError"
                tb_lines = [line for line in (text or msg).splitlines() if line.strip()][:10]
                result["failures"].append(
                    {
                        "nodeid": nodeid,
                        "classname": classname,
                        "name": name,
                        "kind": "error",
                        "exception_type": exc_type,
                        "message": msg[:400],
                        "traceback_head": tb_lines,
                    }
                )
            elif skipped_el is not None:
                skip_type = (skipped_el.attrib.get("type") or "").lower()
                skip_msg = (skipped_el.attrib.get("message") or "").lower()
                if "xfail" in skip_type or "xfail" in skip_msg:
                    status = "xfailed"
                    result["xfailed"] += 1
                else:
                    status = "skipped"
                    result["skipped"] += 1
            else:
                result["passed"] += 1

            result["testcases"].append(
                {
                    "nodeid": nodeid,
                    "file": resolved_file,
                    "classname": classname,
                    "name": name,
                    "status": status,
                    "time_seconds": round(time_val, 4),
                }
            )

    result["time_seconds"] = round(result["time_seconds"], 3)
    return result


def _build_pytest_cmd(
    files: list[str],
    *,
    marker: str,
    xml_path: Path,
    per_test_timeout: int,
    use_timeout_plugin: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        f"--junitxml={xml_path}",
        "-q",
        "--tb=short",
    ]
    if marker.strip():
        cmd.extend(["-m", marker])
    if use_timeout_plugin and per_test_timeout > 0:
        cmd.append(f"--timeout={per_test_timeout}")
    return cmd


def bisect_chunk(
    files: list[str],
    *,
    chunk_idx: int,
    marker: str,
    junit_dir: Path,
    per_file_timeout: int,
    per_test_timeout: int,
    use_timeout_plugin: bool,
    env: dict[str, str],
) -> tuple[list[dict[str, Any]], list[Path]]:
    offenders: list[dict[str, Any]] = []
    sub_xmls: list[Path] = []
    for sub_idx, file_path in enumerate(files, start=1):
        sub_xml = junit_dir / f"chunk_{chunk_idx:02d}_sub_{sub_idx:02d}.xml"
        cmd = _build_pytest_cmd(
            [file_path],
            marker=marker,
            xml_path=sub_xml,
            per_test_timeout=per_test_timeout,
            use_timeout_plugin=use_timeout_plugin,
        )
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=per_file_timeout,
            )
            elapsed = round(time.perf_counter() - t0, 2)
            if proc.returncode < 0 or (proc.returncode not in (0, 1, 5) and not sub_xml.is_file()):
                offenders.append(
                    {
                        "file": file_path,
                        "status": "CRASH",
                        "exit_code": proc.returncode,
                        "duration_seconds": elapsed,
                        "stderr_tail": (proc.stderr or proc.stdout or "")[-500:],
                    }
                )
            elif sub_xml.is_file():
                sub_xmls.append(sub_xml)
        except subprocess.TimeoutExpired as exc:
            elapsed = round(time.perf_counter() - t0, 2)
            offenders.append(
                {
                    "file": file_path,
                    "status": "TIMEOUT",
                    "exit_code": None,
                    "duration_seconds": elapsed,
                    "stderr_tail": str(exc.stderr or exc.stdout or "")[-500:],
                }
            )
    return offenders, sub_xmls


def run_chunks(
    *,
    tests_dir: Path,
    chunk_size: int,
    per_chunk_timeout: int,
    per_test_timeout: int,
    marker: str,
    junit_dir: Path,
    summary_path: Path,
    resume: bool = False,
    junit_out: Path | None = None,
    results_out: Path | None = None,
    workers: int = 1,
    worker_db_urls: list[str] | None = None,
    stop_after_seconds: int = 0,
) -> dict[str, Any]:
    """Run the suite file-by-file in chunks and write the roll-up summary.

    When ``junit_out`` or ``results_out`` is given, the runner switches to
    node-level chunking: the population is collected first, every node is
    scheduled, and the run fails when any node has no recorded outcome.
    """
    if junit_out is not None or results_out is not None:
        return _run_node_chunks(
            tests_dir=tests_dir,
            chunk_size=chunk_size,
            per_chunk_timeout=per_chunk_timeout,
            per_test_timeout=per_test_timeout,
            marker=marker,
            junit_dir=junit_dir,
            summary_path=summary_path,
            resume=resume,
            junit_out=junit_out,
            results_out=results_out,
            workers=workers,
            worker_db_urls=worker_db_urls,
            stop_after_seconds=stop_after_seconds,
        )

    all_files = discover_test_files(tests_dir)
    chunks = [
        all_files[i : i + chunk_size] for i in range(0, len(all_files), chunk_size)
    ]
    junit_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    use_timeout_plugin = has_pytest_timeout()
    env = _base_env()

    chunk_results: list[dict[str, Any]] = []
    all_testcases: list[dict[str, Any]] = []
    all_failures: list[dict[str, Any]] = []
    timeouts: list[dict[str, Any]] = []
    crashes: list[dict[str, Any]] = []

    total_passed = 0
    total_failed = 0
    total_errors = 0
    total_skipped = 0
    total_xfailed = 0
    total_xpassed = 0
    total_deselected = 0
    suite_start = time.perf_counter()

    prior_chunks_by_idx: dict[int, dict[str, Any]] = {}
    if resume and summary_path.is_file():
        try:
            prior_data = json.loads(summary_path.read_text(encoding="utf-8"))
            for pc in prior_data.get("chunks") or []:
                if isinstance(pc, dict) and "chunk_index" in pc:
                    prior_chunks_by_idx[int(pc["chunk_index"])] = pc
        except Exception:
            prior_chunks_by_idx = {}

    for idx, chunk_files in enumerate(chunks, start=1):
        xml_path = junit_dir / f"chunk_{idx:02d}.xml"
        if resume and xml_path.is_file():
            parsed = parse_junit_xml(xml_path)
            prior_chunk = prior_chunks_by_idx.get(idx, {})
            chunk_deselected = int(prior_chunk.get("deselected", 0) or 0)
            chunk_dur = float(
                prior_chunk.get("duration_seconds") or parsed["time_seconds"]
            )
            total_passed += parsed["passed"]
            total_failed += parsed["failed"]
            total_errors += parsed["errors"]
            total_skipped += parsed["skipped"]
            total_xfailed += parsed["xfailed"]
            total_xpassed += parsed["xpassed"]
            total_deselected += chunk_deselected
            all_testcases.extend(parsed["testcases"])
            all_failures.extend(parsed["failures"])
            chunk_results.append(
                {
                    "chunk_index": idx,
                    "xml_path": str(xml_path.relative_to(ROOT)),
                    "file_count": len(chunk_files),
                    "files": chunk_files,
                    "exit_code": 0 if (parsed["failed"] == 0 and parsed["errors"] == 0) else 1,
                    "status": "OK" if (parsed["failed"] == 0 and parsed["errors"] == 0) else "FAILED",
                    "duration_seconds": round(chunk_dur, 2),
                    "passed": parsed["passed"],
                    "failed": parsed["failed"],
                    "errors": parsed["errors"],
                    "skipped": parsed["skipped"],
                    "deselected": chunk_deselected,
                }
            )
            print(
                f"[chunk {idx:02d}/{len(chunks):02d}] RESUMED "
                f"passed={parsed['passed']} failed={parsed['failed']} "
                f"errors={parsed['errors']} deselected={chunk_deselected}",
                flush=True,
            )
            continue

        if xml_path.exists():
            xml_path.unlink()

        cmd = _build_pytest_cmd(
            chunk_files,
            marker=marker,
            xml_path=xml_path,
            per_test_timeout=per_test_timeout,
            use_timeout_plugin=use_timeout_plugin,
        )
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=per_chunk_timeout,
            )
            elapsed = round(time.perf_counter() - t0, 2)
            deselected = _parse_deselected(proc.stdout or "")
            total_deselected += deselected

            # pytest exit code 5 means "no tests were collected" (all deselected by marker)
            if proc.returncode < 0 or (proc.returncode not in (0, 1, 5) and not xml_path.is_file()):
                print(
                    f"[chunk {idx:02d}/{len(chunks):02d}] CRASH (rc={proc.returncode}), bisecting...",
                    flush=True,
                )
                offenders, sub_xmls = bisect_chunk(
                    chunk_files,
                    chunk_idx=idx,
                    marker=marker,
                    junit_dir=junit_dir,
                    per_file_timeout=min(90, per_chunk_timeout),
                    per_test_timeout=per_test_timeout,
                    use_timeout_plugin=use_timeout_plugin,
                    env=env,
                )
                for off in offenders:
                    if off["status"] == "TIMEOUT":
                        timeouts.append(off)
                    else:
                        crashes.append(off)
                c_pass = c_fail = c_err = c_skip = 0
                for sub_xml in sub_xmls:
                    parsed = parse_junit_xml(sub_xml)
                    c_pass += parsed["passed"]
                    c_fail += parsed["failed"]
                    c_err += parsed["errors"]
                    c_skip += parsed["skipped"]
                    total_passed += parsed["passed"]
                    total_failed += parsed["failed"]
                    total_errors += parsed["errors"]
                    total_skipped += parsed["skipped"]
                    total_xfailed += parsed["xfailed"]
                    total_xpassed += parsed["xpassed"]
                    all_testcases.extend(parsed["testcases"])
                    all_failures.extend(parsed["failures"])
                chunk_results.append(
                    {
                        "chunk_index": idx,
                        "xml_path": str(xml_path.relative_to(ROOT)),
                        "file_count": len(chunk_files),
                        "files": chunk_files,
                        "exit_code": proc.returncode,
                        "status": "CRASH_BISECTED",
                        "duration_seconds": elapsed,
                        "passed": c_pass,
                        "failed": c_fail,
                        "errors": c_err,
                        "skipped": c_skip,
                        "deselected": deselected,
                        "offenders": offenders,
                    }
                )
            else:
                parsed = parse_junit_xml(xml_path)
                total_passed += parsed["passed"]
                total_failed += parsed["failed"]
                total_errors += parsed["errors"]
                total_skipped += parsed["skipped"]
                total_xfailed += parsed["xfailed"]
                total_xpassed += parsed["xpassed"]
                all_testcases.extend(parsed["testcases"])
                all_failures.extend(parsed["failures"])
                status = (
                    "OK"
                    if parsed["failed"] == 0 and parsed["errors"] == 0
                    else "FAILED"
                )
                chunk_results.append(
                    {
                        "chunk_index": idx,
                        "xml_path": str(xml_path.relative_to(ROOT)),
                        "file_count": len(chunk_files),
                        "files": chunk_files,
                        "exit_code": proc.returncode,
                        "status": status,
                        "duration_seconds": elapsed,
                        "passed": parsed["passed"],
                        "failed": parsed["failed"],
                        "errors": parsed["errors"],
                        "skipped": parsed["skipped"],
                        "deselected": deselected,
                    }
                )
                print(
                    f"[chunk {idx:02d}/{len(chunks):02d}] {status} in {elapsed:.1f}s "
                    f"(passed={parsed['passed']} failed={parsed['failed']} "
                    f"errors={parsed['errors']} skipped={parsed['skipped']} "
                    f"deselected={deselected})",
                    flush=True,
                )
        except subprocess.TimeoutExpired:
            elapsed = round(time.perf_counter() - t0, 2)
            print(
                f"[chunk {idx:02d}/{len(chunks):02d}] TIMEOUT after {elapsed:.1f}s, bisecting...",
                flush=True,
            )
            offenders, sub_xmls = bisect_chunk(
                chunk_files,
                chunk_idx=idx,
                marker=marker,
                junit_dir=junit_dir,
                per_file_timeout=min(90, per_chunk_timeout),
                per_test_timeout=per_test_timeout,
                use_timeout_plugin=use_timeout_plugin,
                env=env,
            )
            for off in offenders:
                if off["status"] == "TIMEOUT":
                    timeouts.append(off)
                else:
                    crashes.append(off)
            c_pass = c_fail = c_err = c_skip = 0
            for sub_xml in sub_xmls:
                parsed = parse_junit_xml(sub_xml)
                c_pass += parsed["passed"]
                c_fail += parsed["failed"]
                c_err += parsed["errors"]
                c_skip += parsed["skipped"]
                total_passed += parsed["passed"]
                total_failed += parsed["failed"]
                total_errors += parsed["errors"]
                total_skipped += parsed["skipped"]
                total_xfailed += parsed["xfailed"]
                total_xpassed += parsed["xpassed"]
                all_testcases.extend(parsed["testcases"])
                all_failures.extend(parsed["failures"])
            chunk_results.append(
                {
                    "chunk_index": idx,
                    "xml_path": str(xml_path.relative_to(ROOT)),
                    "file_count": len(chunk_files),
                    "files": chunk_files,
                    "exit_code": None,
                    "status": "TIMEOUT_BISECTED",
                    "duration_seconds": elapsed,
                    "passed": c_pass,
                    "failed": c_fail,
                    "errors": c_err,
                    "skipped": c_skip,
                    "deselected": 0,
                    "offenders": offenders,
                }
            )

    suite_duration = round(
        max(
            time.perf_counter() - suite_start,
            sum(float(c.get("duration_seconds") or 0.0) for c in chunk_results),
        ),
        2,
    )
    slowest_20 = sorted(
        all_testcases, key=lambda tc: tc["time_seconds"], reverse=True
    )[:20]

    per_file_map: dict[str, dict[str, Any]] = {}
    for tc in all_testcases:
        f_key = tc.get("file") or tc.get("classname") or "unknown"
        entry = per_file_map.setdefault(
            f_key,
            {
                "file": f_key,
                "tests": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "duration_seconds": 0.0,
            },
        )
        entry["tests"] += 1
        st = tc.get("status", "passed")
        if st == "passed":
            entry["passed"] += 1
        elif st == "failed":
            entry["failed"] += 1
        elif st == "error":
            entry["errors"] += 1
        else:
            entry["skipped"] += 1
        entry["duration_seconds"] = round(
            entry["duration_seconds"] + float(tc.get("time_seconds") or 0.0), 4
        )
    per_file_list = sorted(per_file_map.values(), key=lambda x: x["file"])

    mirror_junit_dir = ROOT / "reports" / "check" / "junit"
    if mirror_junit_dir.resolve() != junit_dir.resolve():
        import shutil

        mirror_junit_dir.mkdir(parents=True, exist_ok=True)
        for xml_file in junit_dir.glob("*.xml"):
            shutil.copy2(xml_file, mirror_junit_dir / xml_file.name)

    summary: dict[str, Any] = {
        "marker": marker,
        "chunk_size": chunk_size,
        "per_chunk_timeout": per_chunk_timeout,
        "per_test_timeout": per_test_timeout,
        "pytest_timeout_plugin_active": use_timeout_plugin,
        "total_test_files": len(all_files),
        "total_chunks": len(chunks),
        "duration_seconds": suite_duration,
        "counts": {
            "total_executed": (
                total_passed
                + total_failed
                + total_errors
                + total_skipped
                + total_xfailed
                + total_xpassed
            ),
            "passed": total_passed,
            "failed": total_failed,
            "errors": total_errors,
            "skipped": total_skipped,
            "xfailed": total_xfailed,
            "xpassed": total_xpassed,
            "deselected": total_deselected,
            "timeouts": len(timeouts),
            "crashes": len(crashes),
        },
        "slowest_tests": slowest_20,
        "per_file": per_file_list,
        "failures": all_failures,
        "timeouts": timeouts,
        "crashes": crashes,
        "chunks": chunk_results,
    }

    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _run_node_chunks(
    *,
    tests_dir: Path,
    chunk_size: int,
    per_chunk_timeout: int,
    per_test_timeout: int,
    marker: str,
    junit_dir: Path,
    summary_path: Path,
    resume: bool = False,
    junit_out: Path | None,
    results_out: Path | None,
    workers: int = 1,
    worker_db_urls: list[str] | None = None,
    stop_after_seconds: int = 0,
) -> dict[str, Any]:
    """Collect every node id, run them in node-id chunks, account for each one."""
    junit_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    env = _base_env()
    use_timeout_plugin = has_pytest_timeout()

    nodes = collect_nodes(tests_dir=tests_dir, marker=marker, env=env)
    node_by_junit_key = {_junit_key_for_node(node): node for node in nodes}
    chunks = chunk_nodes(nodes, chunk_size)
    print(
        f"collected {len(nodes)} node(s); {len(chunks)} chunk(s) of {chunk_size}",
        flush=True,
    )

    worker_count = max(1, int(workers or 1))
    env_pool: queue.Queue[dict[str, str]] = queue.Queue()
    for slot in range(worker_count):
        slot_env = dict(env)
        if worker_db_urls and slot < len(worker_db_urls) and worker_db_urls[slot]:
            slot_env["DATABASE_URL"] = worker_db_urls[slot]
        env_pool.put(slot_env)

    def _run_one_chunk(idx_and_chunk: tuple[int, list[str]]) -> dict[str, Any]:
        idx, chunk = idx_and_chunk
        slot_env = env_pool.get()
        try:
            xml_path = junit_dir / f"nodes_{idx:04d}.xml"
            if resume and xml_path.is_file():
                cached = parse_junit_xml(xml_path)
                if cached["tests"] == len(chunk) and cached["failed"] == 0 and cached["errors"] == 0:
                    print(
                        f"[chunk {idx:04d}/{len(chunks):04d}] RESUMED "
                        f"passed={cached['passed']} skipped={cached['skipped']}",
                        flush=True,
                    )
                    return {
                        "idx": idx,
                        "testcases": cached["testcases"],
                        "failures": cached["failures"],
                        "crash": None,
                        "timeout": None,
                        "chunk_result": {
                            "chunk_index": idx,
                            "xml_path": str(xml_path.relative_to(ROOT)),
                            "node_count": len(chunk),
                            "exit_code": 0,
                            "status": "OK",
                            "duration_seconds": round(cached["time_seconds"], 2),
                            "passed": cached["passed"],
                            "failed": 0,
                            "errors": 0,
                            "skipped": cached["skipped"],
                        },
                    }
            if xml_path.exists():
                xml_path.unlink()
            heavy_nodes = [
                n for n in chunk if n.partition("::")[0] in _SUBPROCESS_HEAVY_FILES
            ]
            regular_nodes = [
                n for n in chunk if n.partition("::")[0] not in _SUBPROCESS_HEAVY_FILES
            ]
            if heavy_nodes and regular_nodes:
                queue_batches_h: list[tuple[str, list[str]]] = [("p0", regular_nodes)]
                by_heavy_file: dict[str, list[str]] = {}
                for hn in heavy_nodes:
                    by_heavy_file.setdefault(hn.partition("::")[0], []).append(hn)
                for h_idx, h_nodes in enumerate(by_heavy_file.values(), start=1):
                    queue_batches_h.append((f"p{h_idx}", h_nodes))
                t0 = time.perf_counter()
                part_xmls: list[Path] = []
                last_rc = 0
                while queue_batches_h:
                    p_tag, p_nodes = queue_batches_h.pop(0)
                    if not p_nodes:
                        continue
                    p_xml = junit_dir / f"nodes_{idx:04d}_{p_tag}.xml"
                    p_xml.unlink(missing_ok=True)
                    p_cmd = _build_pytest_cmd(
                        p_nodes,
                        marker=marker,
                        xml_path=p_xml,
                        per_test_timeout=per_test_timeout,
                        use_timeout_plugin=use_timeout_plugin,
                    )
                    p_proc = subprocess.run(
                        p_cmd, cwd=ROOT, env=slot_env, capture_output=True, text=True,
                        timeout=per_chunk_timeout,
                    )
                    if p_proc.returncode < 0 and len(p_nodes) > 1:
                        mid = len(p_nodes) // 2
                        queue_batches_h.insert(0, (f"{p_tag}b", p_nodes[mid:]))
                        queue_batches_h.insert(0, (f"{p_tag}a", p_nodes[:mid]))
                        continue
                    if p_proc.returncode not in (0, 5):
                        last_rc = p_proc.returncode
                    if p_xml.is_file():
                        part_xmls.append(p_xml)
                elapsed = round(time.perf_counter() - t0, 2)
                if part_xmls:
                    _merge_junit(part_xmls, xml_path)
                    for p_xml in part_xmls:
                        p_xml.unlink(missing_ok=True)
                parsed = parse_junit_xml(xml_path)
                print(
                    f"[chunk {idx:04d}/{len(chunks):04d}] "
                    f"passed={parsed['passed']} failed={parsed['failed']} "
                    f"errors={parsed['errors']} skipped={parsed['skipped']} in {elapsed:.1f}s",
                    flush=True,
                )
                return {
                    "idx": idx,
                    "testcases": parsed["testcases"],
                    "failures": parsed["failures"],
                    "crash": None,
                    "timeout": None,
                    "chunk_result": {
                        "chunk_index": idx,
                        "xml_path": str(xml_path.relative_to(ROOT)),
                        "node_count": len(chunk),
                        "exit_code": last_rc,
                        "status": "OK" if parsed["failed"] == 0 and parsed["errors"] == 0 else "FAILED",
                        "duration_seconds": elapsed,
                        "passed": parsed["passed"],
                        "failed": parsed["failed"],
                        "errors": parsed["errors"],
                        "skipped": parsed["skipped"],
                    },
                }
            cmd = _build_pytest_cmd(
                list(chunk),
                marker=marker,
                xml_path=xml_path,
                per_test_timeout=per_test_timeout,
                use_timeout_plugin=use_timeout_plugin,
            )
            t0 = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd, cwd=ROOT, env=slot_env, capture_output=True, text=True,
                    timeout=per_chunk_timeout,
                )
                elapsed = round(time.perf_counter() - t0, 2)
                if proc.returncode < 0 or (
                    proc.returncode not in (0, 1, 5) and not xml_path.is_file()
                ):
                    print(
                        f"[chunk {idx:04d}/{len(chunks):04d}] CRASH rc={proc.returncode}; splitting {len(chunk)} nodes into halves",
                        flush=True,
                    )
                    offenders: list[dict[str, Any]] = []
                    sub_xmls: list[Path] = []
                    queue_batches: list[tuple[str, list[str]]] = [
                        ("a", list(chunk[: max(1, len(chunk) // 2)])),
                        ("b", list(chunk[max(1, len(chunk) // 2) :])),
                    ]
                    while queue_batches:
                        tag, sub_nodes = queue_batches.pop(0)
                        if not sub_nodes:
                            continue
                        sub_xml = junit_dir / f"nodes_{idx:04d}_{tag}.xml"
                        if sub_xml.exists():
                            sub_xml.unlink()
                        sub_cmd = _build_pytest_cmd(
                            sub_nodes,
                            marker=marker,
                            xml_path=sub_xml,
                            per_test_timeout=per_test_timeout,
                            use_timeout_plugin=use_timeout_plugin,
                        )
                        st0 = time.perf_counter()
                        sproc = subprocess.run(
                            sub_cmd, cwd=ROOT, env=slot_env, capture_output=True, text=True,
                            timeout=per_chunk_timeout,
                        )
                        selapsed = round(time.perf_counter() - st0, 2)
                        if (
                            sproc.returncode < 0
                            or (sproc.returncode not in (0, 1, 5) and not sub_xml.is_file())
                        ):
                            if len(sub_nodes) > 1:
                                mid = len(sub_nodes) // 2
                                queue_batches.insert(0, (f"{tag}2", sub_nodes[mid:]))
                                queue_batches.insert(0, (f"{tag}1", sub_nodes[:mid]))
                            else:
                                offenders.append(
                                    {
                                        "file": sub_nodes[0],
                                        "status": "CRASH",
                                        "exit_code": sproc.returncode,
                                        "duration_seconds": selapsed,
                                        "stderr_tail": (sproc.stderr or sproc.stdout or "")[-500:],
                                    }
                                )
                        elif sub_xml.is_file():
                            sub_xmls.append(sub_xml)
                    if not offenders and sub_xmls:
                        _merge_junit(sub_xmls, xml_path)
                        for sub_xml in sub_xmls:
                            sub_xml.unlink(missing_ok=True)
                    sub_cases: list[dict[str, Any]] = []
                    sub_failures: list[dict[str, Any]] = []
                    xml_sources = [xml_path] if xml_path.is_file() else sub_xmls
                    for sub_xml in xml_sources:
                        sub_parsed = parse_junit_xml(sub_xml)
                        sub_cases.extend(sub_parsed["testcases"])
                        sub_failures.extend(sub_parsed["failures"])
                    crash_entry = None
                    if offenders:
                        crash_entry = {
                            "chunk_index": idx,
                            "status": "CRASH",
                            "exit_code": proc.returncode,
                            "duration_seconds": elapsed,
                            "offenders": offenders,
                            "stderr_tail": (proc.stderr or proc.stdout or "")[-500:],
                        }
                    return {
                        "idx": idx,
                        "testcases": sub_cases,
                        "failures": sub_failures,
                        "crash": crash_entry,
                        "timeout": None,
                        "chunk_result": {
                            "chunk_index": idx,
                            "xml_path": str(xml_path.relative_to(ROOT)),
                            "node_count": len(chunk),
                            "exit_code": proc.returncode,
                            "status": "CRASH" if offenders else "OK",
                            "duration_seconds": elapsed,
                        },
                    }
                parsed = parse_junit_xml(xml_path)
                print(
                    f"[chunk {idx:04d}/{len(chunks):04d}] "
                    f"passed={parsed['passed']} failed={parsed['failed']} "
                    f"errors={parsed['errors']} skipped={parsed['skipped']} in {elapsed:.1f}s",
                    flush=True,
                )
                return {
                    "idx": idx,
                    "testcases": parsed["testcases"],
                    "failures": parsed["failures"],
                    "crash": None,
                    "timeout": None,
                    "chunk_result": {
                        "chunk_index": idx,
                        "xml_path": str(xml_path.relative_to(ROOT)),
                        "node_count": len(chunk),
                        "exit_code": proc.returncode,
                        "status": "OK" if parsed["failed"] == 0 and parsed["errors"] == 0 else "FAILED",
                        "duration_seconds": elapsed,
                        "passed": parsed["passed"],
                        "failed": parsed["failed"],
                        "errors": parsed["errors"],
                        "skipped": parsed["skipped"],
                    },
                }
            except subprocess.TimeoutExpired:
                elapsed = round(time.perf_counter() - t0, 2)
                print(f"[chunk {idx:04d}/{len(chunks):04d}] TIMEOUT after {elapsed:.1f}s", flush=True)
                return {
                    "idx": idx,
                    "testcases": [],
                    "failures": [],
                    "crash": None,
                    "timeout": {
                        "chunk_index": idx,
                        "status": "TIMEOUT",
                        "exit_code": None,
                        "duration_seconds": elapsed,
                    },
                    "chunk_result": {
                        "chunk_index": idx,
                        "xml_path": str(xml_path.relative_to(ROOT)),
                        "node_count": len(chunk),
                        "exit_code": None,
                        "status": "TIMEOUT",
                        "duration_seconds": elapsed,
                    },
                }
        finally:
            env_pool.put(slot_env)

    chunk_results: list[dict[str, Any]] = []
    per_node: dict[str, dict[str, Any]] = {}
    all_failures: list[dict[str, Any]] = []
    timeouts: list[dict[str, Any]] = []
    crashes: list[dict[str, Any]] = []
    suite_start = time.perf_counter()

    indexed_chunks = list(enumerate(chunks, start=1))
    paused_early = False
    if worker_count == 1:
        outcomes = []
        for item in indexed_chunks:
            outcomes.append(_run_one_chunk(item))
            if stop_after_seconds > 0 and (time.perf_counter() - suite_start) >= stop_after_seconds:
                if item[0] < len(indexed_chunks):
                    paused_early = True
                    print(
                        f"PAUSED after chunk {item[0]:04d}/{len(chunks):04d} "
                        f"at {time.perf_counter() - suite_start:.1f}s (resume to continue)",
                        flush=True,
                    )
                    break
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            outcomes = list(pool.map(_run_one_chunk, indexed_chunks))

    for out in sorted(outcomes, key=lambda item: item["idx"]):
        for case in out["testcases"]:
            canonical = case["nodeid"]
            if canonical not in node_by_junit_key.values():
                mapped = node_by_junit_key.get((case["classname"], case["name"]))
                if mapped is not None:
                    canonical = mapped
                    case = {**case, "nodeid": canonical}
            per_node.setdefault(canonical, case)
        all_failures.extend(out["failures"])
        if out["crash"] is not None:
            crashes.append(out["crash"])
        if out["timeout"] is not None:
            timeouts.append(out["timeout"])
        chunk_results.append(out["chunk_result"])

    suite_duration = round(time.perf_counter() - suite_start, 2)

    # --- population accounting -------------------------------------------------
    recorded: dict[str, str] = {}
    for node in nodes:
        case = per_node.get(node)
        recorded[node] = case["status"] if case is not None else "not_run"
    unaccounted = sorted(node for node, status in recorded.items() if status == "not_run")
    counts = {
        status: sum(1 for value in recorded.values() if value == status)
        for status in (
            "passed", "failed", "error", "skipped", "xfailed", "xpassed", "not_run",
        )
    }

    results: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "marker": marker,
        "chunk_size": chunk_size,
        "paused_early": paused_early,
        "duration_seconds": suite_duration,
        "population": len(nodes),
        "collected": len(nodes),
        "recorded": len(nodes) - len(unaccounted),
        "counts": counts,
        "unaccounted_nodes": unaccounted,
        "timeouts": timeouts,
        "crashes": crashes,
        "chunks": chunk_results,
        "failures": all_failures,
        "nodes": [
            {
                "nodeid": node,
                "status": recorded[node],
                "duration_seconds": (per_node.get(node) or {}).get("time_seconds", 0.0),
            }
            for node in nodes
        ],
    }

    if junit_out is not None:
        junit_out.parent.mkdir(parents=True, exist_ok=True)
        junit_out.write_text(
            _render_merged_junit(nodes, recorded, per_node, suite_duration),
            encoding="utf-8",
        )
        results["junit_path"] = str(junit_out.relative_to(ROOT))
    if results_out is not None:
        results_out.parent.mkdir(parents=True, exist_ok=True)
        results_out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        results["results_path"] = str(results_out.relative_to(ROOT))

    summary: dict[str, Any] = {
        "marker": marker,
        "chunk_size": chunk_size,
        "per_chunk_timeout": per_chunk_timeout,
        "per_test_timeout": per_test_timeout,
        "pytest_timeout_plugin_active": use_timeout_plugin,
        "total_test_files": len({node.split("::")[0] for node in nodes}),
        "total_chunks": len(chunks),
        "duration_seconds": suite_duration,
        "counts": {
            "total_executed": len(nodes) - len(unaccounted),
            "passed": counts["passed"],
            "failed": counts["failed"],
            "errors": counts["error"],
            "skipped": counts["skipped"],
            "xfailed": counts["xfailed"],
            "xpassed": counts["xpassed"],
            "deselected": 0,
            "timeouts": len(timeouts),
            "crashes": len(crashes),
            "not_run": counts["not_run"],
        },
        "population": len(nodes),
        "unaccounted_nodes": unaccounted,
        "slowest_tests": sorted(
            (per_node[n] for n in nodes if n in per_node),
            key=lambda tc: tc["time_seconds"],
            reverse=True,
        )[:20],
        "per_file": _per_file_rollup(nodes, recorded, per_node),
        "failures": all_failures,
        "timeouts": timeouts,
        "crashes": crashes,
        "chunks": chunk_results,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _per_file_rollup(
    nodes: list[str],
    recorded: dict[str, str],
    per_node: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    per_file_map: dict[str, dict[str, Any]] = {}
    for node in nodes:
        key = node.split("::")[0]
        entry = per_file_map.setdefault(
            key,
            {
                "file": key,
                "tests": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "not_run": 0,
                "duration_seconds": 0.0,
            },
        )
        entry["tests"] += 1
        status = recorded[node]
        if status == "passed":
            entry["passed"] += 1
        elif status == "failed":
            entry["failed"] += 1
        elif status == "error":
            entry["errors"] += 1
        elif status == "not_run":
            entry["not_run"] += 1
        else:
            entry["skipped"] += 1
        entry["duration_seconds"] = round(
            entry["duration_seconds"] + float((per_node.get(node) or {}).get("time_seconds") or 0.0), 4
        )
    return sorted(per_file_map.values(), key=lambda x: x["file"])


def _render_merged_junit(
    nodes: list[str],
    recorded: dict[str, str],
    per_node: dict[str, dict[str, Any]],
    duration: float,
) -> str:
    """One JUnit document covering the whole collected population.

    Nodes with no outcome are emitted as ``<testcase><error/></testcase>`` so a
    JUnit viewer shows them as failures instead of dropping them silently.
    """
    counts = {
        status: sum(1 for value in recorded.values() if value == status)
        for status in ("passed", "failed", "error", "skipped", "xfailed", "xpassed", "not_run")
    }
    lines: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<testsuites name="voxdesk-full-population" time="{duration:.3f}">',
        (
            '<testsuite name="pytest" errors="{error}" failures="{failed}" '
            'skipped="{skipped}" tests="{total}" time="{duration:.3f}">'
        ).format(
            error=counts["error"] + counts["not_run"],
            failed=counts["failed"],
            skipped=counts["skipped"] + counts["xfailed"] + counts["xpassed"],
            total=len(nodes),
            duration=duration,
        ),
    ]
    for node in nodes:
        case = per_node.get(node)
        file_part, _, name_part = node.partition("::")
        classname = (case or {}).get("classname") or file_part.replace("/", ".").removesuffix(".py")
        name = (case or {}).get("name") or name_part
        time_val = float((case or {}).get("time_seconds") or 0.0)
        status = recorded[node]
        lines.append(
            f'<testcase classname="{_esc(classname)}" name="{_esc(name)}" '
            f'file="{_esc(file_part)}" time="{time_val:.4f}"'
        )
        if status in {"passed"}:
            lines.append("/>")
        elif status == "failed":
            lines.append('><failure message="failed"/></testcase>')
        elif status == "error":
            lines.append('><error message="error"/></testcase>')
        elif status == "not_run":
            lines.append('><error message="NOT_RUN: no outcome recorded"/></testcase>')
        else:
            lines.append(f'><skipped type="pytest.skip" message="{_esc(status)}"/></testcase>')
    lines.append("</testsuite>")
    lines.append("</testsuites>")
    return "\n".join(lines) + "\n"


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-dir", type=Path, default=ROOT / "tests")
    parser.add_argument("--chunk-size", "--size", dest="chunk_size", type=int, default=25)
    parser.add_argument("--per-chunk-timeout", type=int, default=180)
    parser.add_argument("--per-test-timeout", "--timeout", dest="per_test_timeout", type=int, default=30)
    parser.add_argument("--marker", type=str, default="not real_provider and not live")
    parser.add_argument("--junit-dir", "--junit", dest="junit_dir", type=Path, default=ROOT / "reports/check/chunks")
    parser.add_argument(
        "--summary", type=Path, default=ROOT / "reports/check/pytest_summary.json"
    )
    parser.add_argument(
        "--junit-out",
        type=Path,
        default=None,
        help="Write one merged JUnit XML covering every collected node.",
    )
    parser.add_argument(
        "--results-out",
        type=Path,
        default=None,
        help="Write the machine-generated results JSON (one entry per collected node).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing chunk_XX.xml files if already present (file mode only).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent chunk subprocesses in node mode (default: 1).",
    )
    parser.add_argument(
        "--worker-db-url",
        action="append",
        default=[],
        dest="worker_db_urls",
        help="Optional per-worker DATABASE_URL (repeatable, one per worker slot).",
    )
    parser.add_argument(
        "--stop-after-seconds",
        type=int,
        default=0,
        help="Stop cleanly after finishing the current chunk once elapsed >= N seconds (for --resume).",
    )
    args = parser.parse_args(argv)

    summary = run_chunks(
        tests_dir=args.tests_dir.resolve(),
        chunk_size=args.chunk_size,
        per_chunk_timeout=args.per_chunk_timeout,
        per_test_timeout=args.per_test_timeout,
        marker=args.marker,
        junit_dir=args.junit_dir.resolve(),
        summary_path=args.summary.resolve(),
        resume=args.resume,
        junit_out=args.junit_out.resolve() if args.junit_out else None,
        results_out=args.results_out.resolve() if args.results_out else None,
        workers=args.workers,
        worker_db_urls=args.worker_db_urls,
        stop_after_seconds=args.stop_after_seconds,
    )

    if summary.get("paused_early") and not summary["counts"]["failed"] and not summary["counts"]["errors"]:
        return 0

    if args.results_out is not None:
        counts = summary["counts"]
        unaccounted = summary.get("unaccounted_nodes") or []
        print(
            f"POPULATION: collected={summary['population']} "
            f"passed={counts['passed']} failed={counts['failed']} "
            f"errors={counts['errors']} skipped={counts['skipped']} "
            f"xfailed={counts['xfailed']} xpassed={counts['xpassed']} "
            f"not_run={counts['not_run']} duration={summary['duration_seconds']}s",
            flush=True,
        )
        if unaccounted:
            print(
                f"UNACCOUNTED: {len(unaccounted)} collected node(s) have no recorded outcome",
                flush=True,
            )
            for node in unaccounted[:20]:
                print(f"  {node}", flush=True)
        if counts["failed"] or counts["errors"] or counts["timeouts"] or counts["crashes"]:
            return 1
        if unaccounted:
            return EXIT_UNACCOUNTED
        return 0

    counts = summary["counts"]
    print(
        f"SUMMARY: passed={counts['passed']} failed={counts['failed']} "
        f"errors={counts['errors']} skipped={counts['skipped']} "
        f"xfailed={counts['xfailed']} xpassed={counts['xpassed']} "
        f"deselected={counts['deselected']} timeouts={counts['timeouts']} "
        f"crashes={counts['crashes']} duration={summary['duration_seconds']}s",
        flush=True,
    )
    if counts["failed"] > 0 or counts["errors"] > 0 or counts["timeouts"] > 0 or counts["crashes"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
