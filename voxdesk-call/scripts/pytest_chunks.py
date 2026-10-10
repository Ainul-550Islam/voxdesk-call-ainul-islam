#!/usr/bin/env python3
"""Deterministic chunked pytest runner with per-chunk isolation, bisection, and JUnit XML roll-up.

Usage:
    python scripts/pytest_chunks.py \\
      --chunk-size 25 \\
      --per-chunk-timeout 180 \\
      --marker "not real_provider and not live" \\
      --junit-dir reports/check/chunks \\
      --summary reports/check/pytest_summary.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


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

    tree = ET.parse(xml_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))

    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            file_attr = case.attrib.get("file", "")
            time_val = float(case.attrib.get("time", "0") or 0.0)
            nodeid = f"{file_attr}::{name}" if file_attr else f"{classname}::{name}"

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
                    "file": file_attr or classname.replace(".", "/") + ".py",
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
) -> dict[str, Any]:
    all_files = discover_test_files(tests_dir)
    chunks = [
        all_files[i : i + chunk_size] for i in range(0, len(all_files), chunk_size)
    ]
    junit_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    use_timeout_plugin = has_pytest_timeout()
    env = dict(os.environ)
    env["TZ"] = "UTC"
    if "DATABASE_URL" not in env:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", 5432)) == 0:
                env["DATABASE_URL"] = (
                    "postgresql+asyncpg://voxdesk:voxdesk-ci@localhost:5432/voxdesk_ci"
                )

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
        "--resume",
        action="store_true",
        help="Reuse existing chunk_XX.xml files if already present.",
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
    )
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
