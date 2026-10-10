#!/usr/bin/env python3
"""Measure repository truth metrics and write ``reports/check/baseline_numbers.json``.

Computes and prints a table of:
- Routes (real vs template-clone, from ``reports/check/routes.csv`` or live app inspection)
- Clone modules (from ``scripts/verify_no_filler.py``)
- ``Padding ... line N`` lines (from ``scripts/strip_padding_markers.py --check``)
- Code lines of the three retired filler engine dirs (``app/voice_engine``, ``app/rtc_engine``, ``app/pstn_engine``)
- Generated-tail files & lines in ``dashboard/src`` (from ``scripts/strip_generated_tails.py --check``)
- Code lines by area (``app``, ``tests``, ``alembic``, ``scripts``, ``dashboard``, ``dashboard-next``, ``services``) split REAL vs FAKE
- Pytest tests collected (from ``reports/check/pytest_summary.json`` or ``pytest --collect-only``)
- Alembic heads
- Docker image sizes (from ``reports/check/docker.json`` if present)
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "check"
OUT_JSON = REPORT_DIR / "baseline_numbers.json"

PADDING_RE = re.compile(r"Padding\s+.*\bline\s+\d+", re.IGNORECASE)
TAIL_MARKER_RE = re.compile(
    r"(AUTO-GENERATED\s+TAIL|GENERATED[_\s-]+TAIL|SYNTHETIC[_\s-]+PADDING)",
    re.IGNORECASE,
)

SOURCE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
    ".sh",
    ".proto",
    ".css",
    ".sql",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    "target",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}


def _run_json_script(cmd: list[str]) -> Any:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        start_obj = out.find("{")
        start_arr = out.find("[")
        if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
            end_arr = out.rfind("]")
            if end_arr > start_arr:
                return json.loads(out[start_arr : end_arr + 1])
        if start_obj != -1:
            end_obj = out.rfind("}")
            if end_obj > start_obj:
                return json.loads(out[start_obj : end_obj + 1])
    return None


def _count_routes() -> dict[str, int]:
    routes_csv = REPORT_DIR / "routes.csv"
    total = 0
    real_count = 0
    clone_count = 0
    if routes_csv.exists():
        with routes_csv.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                total += 1
                classification = (
                    row.get("classification")
                    or row.get("kind")
                    or row.get("status")
                    or "real"
                ).strip().lower()
                is_clone = (
                    "clone" in classification
                    or "filler" in classification
                    or "fake" in classification
                    or row.get("is_clone", "").strip().lower() in {"1", "true", "yes"}
                )
                if is_clone:
                    clone_count += 1
                else:
                    real_count += 1
    else:
        routes_json = REPORT_DIR / "routes.json"
        if routes_json.exists():
            data = json.loads(routes_json.read_text(encoding="utf-8"))
            total = int(data.get("total_routes", 0))
            clone_count = int(data.get("clone_routes", 0))
            real_count = total - clone_count
    return {
        "total": total,
        "real": real_count,
        "template_clone": clone_count,
    }


def _count_filler_engine_lines() -> dict[str, Any]:
    filler_dirs = ["app/voice_engine", "app/rtc_engine", "app/pstn_engine"]
    per_dir: dict[str, int] = {}
    total_lines = 0
    for rel in filler_dirs:
        d = ROOT / rel
        lines = 0
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in SOURCE_EXTENSIONS:
                    try:
                        lines += len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                    except OSError:
                        pass
        per_dir[rel] = lines
        total_lines += lines
    return {"total_lines": total_lines, "by_dir": per_dir}


def _count_area_lines(clone_files: set[str], tail_lines_by_file: dict[str, int]) -> dict[str, dict[str, int]]:
    areas = [
        "app",
        "tests",
        "alembic",
        "scripts",
        "dashboard",
        "dashboard-next",
        "services",
    ]
    results: dict[str, dict[str, int]] = {}
    for area in areas:
        area_root = ROOT / area
        files_count = 0
        total_lines = 0
        fake_lines = 0
        if area_root.exists():
            for p in area_root.rglob("*"):
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                if not p.is_file() or p.suffix.lower() not in SOURCE_EXTENSIONS:
                    continue
                rel = p.relative_to(ROOT).as_posix()
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                n_lines = len(lines)
                files_count += 1
                total_lines += n_lines

                file_fake = 0
                if rel in clone_files or rel.startswith(("app/voice_engine/", "app/rtc_engine/", "app/pstn_engine/")):
                    file_fake = n_lines
                else:
                    file_fake = int(tail_lines_by_file.get(rel, 0))
                fake_lines += min(n_lines, file_fake)

        real_lines = max(0, total_lines - fake_lines)
        results[area] = {
            "files": files_count,
            "total_lines": total_lines,
            "real_lines": real_lines,
            "fake_lines": fake_lines,
        }
    return results


def _alembic_heads() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    heads: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line:
            heads.append(line.split()[0])
    return heads


def _tests_collected() -> int:
    summary_path = REPORT_DIR / "pytest_summary.json"
    if summary_path.exists():
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        counts = data.get("counts") or data.get("totals") or data
        total = int(counts.get("total_executed", 0)) + int(counts.get("deselected", 0))
        if total == 0:
            total = (
                int(counts.get("passed", 0))
                + int(counts.get("failed", 0))
                + int(counts.get("errors", 0))
                + int(counts.get("skipped", 0))
                + int(counts.get("deselected", 0))
            )
        if total > 0:
            return total
    backend_path = REPORT_DIR / "backend.json"
    if backend_path.exists():
        data = json.loads(backend_path.read_text(encoding="utf-8"))
        t = data.get("pytest", {}).get("totals", {}).get("total", 0) or data.get("pytest", {}).get("total", 0)
        if t:
            return int(t)
    return 0


def _docker_images() -> list[dict[str, Any]]:
    docker_path = REPORT_DIR / "docker.json"
    if docker_path.exists():
        data = json.loads(docker_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("images"), list):
            return data["images"]
    return []


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    filler_res = _run_json_script([sys.executable, "scripts/verify_no_filler.py"])
    clone_modules: list[str] = filler_res if isinstance(filler_res, list) else []

    pad_res = _run_json_script([sys.executable, "scripts/strip_padding_markers.py", "--check"])
    padding_files = 0
    padding_lines = 0
    if isinstance(pad_res, list):
        padding_files = len(pad_res)
        padding_lines = len(pad_res)
    elif isinstance(pad_res, dict):
        padding_files = int(pad_res.get("files_with_markers", len(pad_res.get("matches", []))))
        padding_lines = int(pad_res.get("total_markers", padding_files))

    tail_res = _run_json_script([sys.executable, "scripts/strip_generated_tails.py", "--check"])
    tail_files = 0
    tail_lines = 0
    tail_lines_by_file: dict[str, int] = {}
    if isinstance(tail_res, dict):
        tail_files = int(tail_res.get("files_with_tails", 0))
        tail_lines = int(tail_res.get("tail_lines", 0))
    elif isinstance(tail_res, list):
        tail_files = len(tail_res)
        tail_lines = len(tail_res)

    routes = _count_routes()
    filler_engines = _count_filler_engine_lines()
    areas = _count_area_lines(set(clone_modules), tail_lines_by_file)
    heads = _alembic_heads()
    tests_collected = _tests_collected()
    images = _docker_images()

    total_real_lines = sum(v["real_lines"] for v in areas.values())
    total_fake_lines = sum(v["fake_lines"] for v in areas.values())
    total_code_lines = sum(v["total_lines"] for v in areas.values())

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "routes": routes,
        "clone_modules_count": len(clone_modules),
        "clone_modules": clone_modules,
        "padding_marker_files": padding_files,
        "padding_marker_lines": padding_lines,
        "filler_engine_dirs": filler_engines,
        "dashboard_generated_tails": {
            "files": tail_files,
            "lines": tail_lines,
        },
        "code_lines_by_area": areas,
        "code_lines_totals": {
            "total_lines": total_code_lines,
            "real_lines": total_real_lines,
            "fake_lines": total_fake_lines,
        },
        "tests_collected": tests_collected,
        "alembic_heads": heads,
        "docker_images": images,
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("=== VoxDesk Measured Baseline / Readiness Numbers ===")
    print(f"Routes (total / real / template-clone): {routes['total']} / {routes['real']} / {routes['template_clone']}")
    print(f"Clone modules (verify_no_filler.py):    {len(clone_modules)}")
    print(f"Padding '... line N' lines:             {padding_lines} ( across {padding_files} files )")
    print(
        f"Retired filler engine dirs lines:       {filler_engines['total_lines']} "
        f"(voice={filler_engines['by_dir']['app/voice_engine']}, "
        f"rtc={filler_engines['by_dir']['app/rtc_engine']}, "
        f"pstn={filler_engines['by_dir']['app/pstn_engine']})"
    )
    print(f"Dashboard generated-tail files / lines: {tail_files} / {tail_lines}")
    print(f"Pytest tests collected:                 {tests_collected}")
    print(f"Alembic heads ({len(heads)}):                    {', '.join(heads) if heads else 'none'}")
    print(f"Docker images recorded:                 {len(images)}")
    print("")
    print(f"{'Area':<18} {'Files':>7} {'Total Lines':>13} {'REAL Lines':>13} {'FAKE Lines':>12}")
    print("-" * 67)
    for area, stats in areas.items():
        print(
            f"{area:<18} {stats['files']:>7} {stats['total_lines']:>13,} "
            f"{stats['real_lines']:>13,} {stats['fake_lines']:>12,}"
        )
    print("-" * 67)
    print(
        f"{'TOTAL':<18} {sum(s['files'] for s in areas.values()):>7} "
        f"{total_code_lines:>13,} {total_real_lines:>13,} {total_fake_lines:>12,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
