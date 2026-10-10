#!/usr/bin/env python3
"""Compare frontend API client paths against the backend route inventory.

Supports both CSV (`reports/check/routes.csv`) and JSON (`reports/check/routes.json`)
route tables produced by SELL CHECK 1 (`scripts/route_inventory.py`).

Usage:
    python3 scripts/frontend_api_contract_check.py --routes reports/check/routes.csv [--json-out PATH]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
LINE_COMMENT_RE = re.compile(r"(?<!:)//.*$")
LITERAL_RE = re.compile(
    r"`((?:/api|/auth|/health|/scim|/ws)[^`]*?(?:\$\{[^}]*`[^`]*`[^}]*\}[^`]*)*)`"
    r"|\"((?:/api|/auth|/health|/scim|/ws)[^\"]+)\""
    r"|'((?:/api|/auth|/health|/scim|/ws)[^']+)'"
)
LEGACY_AGENT_X_RE = re.compile(
    r"^/api(?:/agents/[^/]+)?/agent-(?:models|voices|tools|knowledge|templates|"
    r"metrics|validation|conflict|search|filters|sort|conversation|security|"
    r"actions|publish|versions|builder)\b"
)
SILENT_CATCH_RE = re.compile(
    r"catch\s*(?:\([^)]*\))?\s*\{\s*return\s*(?:\[\s*\]|null)\s*;?\s*\}"
)


def load_backend_routes(routes_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if routes_path.suffix.lower() == ".json":
        payload = json.loads(routes_path.read_text(encoding="utf-8"))
        raw_routes = payload.get("routes", payload) if isinstance(payload, dict) else payload
        for r in raw_routes:
            methods = r.get("methods", [])
            if isinstance(methods, list):
                methods_str = "|".join(methods)
            else:
                methods_str = str(methods)
            rows.append(
                {
                    "path": str(r.get("path", "")),
                    "methods": methods_str,
                    "endpoint": str(r.get("endpoint", "")),
                }
            )
    else:
        with routes_path.open("r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows.append(
                    {
                        "path": str(row.get("path", "")),
                        "methods": str(row.get("methods", "")),
                        "endpoint": str(row.get("endpoint", "")),
                    }
                )

    compiled: list[dict[str, Any]] = []
    for row in rows:
        bpath = row["path"].rstrip("/") or "/"
        pattern_str = "^" + re.sub(
            r"\{[^}/]+:path\}",
            ".+",
            re.sub(r"\{[^}/]+\}", "[^/]+", bpath),
        ) + "$"
        is_clone = bool(re.search(r"/endpoint-\d+$", bpath))
        compiled.append(
            {
                "path": bpath,
                "methods": row["methods"],
                "endpoint": row["endpoint"],
                "regex": re.compile(pattern_str),
                "is_template_clone": is_clone,
            }
        )
    return compiled


def normalize_frontend_path(fe_path: str) -> tuple[str, str]:
    s = re.sub(r"\$\{(?:q|qs|query|suffix|search)\b[\s\S]*\}$", "", fe_path)
    s = s.split("?")[0]
    s = s.rstrip("/") or "/"
    norm = re.sub(r"\$\{[\s\S]*?\}", "PARAM", s)
    norm = re.sub(r":[A-Za-z_]\w*", "PARAM", norm)
    return s, norm


def extract_api_literals(text: str) -> list[tuple[int, str]]:
    without_blocks = BLOCK_COMMENT_RE.sub(
        lambda m: "\n" * m.group(0).count("\n"),
        text,
    )
    results: list[tuple[int, str]] = []
    for line_no, raw_line in enumerate(without_blocks.splitlines(), 1):
        line = LINE_COMMENT_RE.sub("", raw_line)
        for m in LITERAL_RE.finditer(line):
            val = m.group(1) or m.group(2) or m.group(3)
            if val:
                results.append((line_no, val))
    return results


def collect_frontend_files() -> list[Path]:
    files = (
        sorted((ROOT / "dashboard/src/api").rglob("*.ts"))
        + sorted((ROOT / "dashboard/src/hooks").rglob("*.ts"))
        + sorted((ROOT / "dashboard/src/lib").glob("*.ts"))
        + sorted((ROOT / "dashboard/src/lib").glob("*.js"))
        + [
            ROOT / "dashboard-next/lib/api.ts",
            ROOT / "dashboard-next/lib/identity.ts",
        ]
    )
    return [p for p in files if p.exists() and p.is_file()]


def scan_silent_catches() -> list[dict[str, Any]]:
    catches: list[dict[str, Any]] = []
    api_dir = ROOT / "dashboard/src/api"
    for f in sorted(api_dir.rglob("*.ts")):
        rel = str(f.relative_to(ROOT))
        if rel == "dashboard/src/api/client.ts":
            continue
        code = BLOCK_COMMENT_RE.sub("", f.read_text(encoding="utf-8"))
        code = "\n".join(LINE_COMMENT_RE.sub("", ln) for ln in code.splitlines())
        for m in SILENT_CATCH_RE.finditer(code):
            line_no = code[: m.start()].count("\n") + 1
            catches.append(
                {
                    "file": rel,
                    "line": line_no,
                    "snippet": " ".join(m.group(0).split()),
                }
            )
    return catches


def check_contract(routes_path: Path) -> dict[str, Any]:
    backend_routes = load_backend_routes(routes_path)
    fe_files = collect_frontend_files()

    matched_calls: list[dict[str, Any]] = []
    template_clone_calls: list[dict[str, Any]] = []
    unmatched_calls: list[dict[str, Any]] = []
    legacy_agent_x_calls: list[dict[str, Any]] = []

    for f in fe_files:
        rel = str(f.relative_to(ROOT))
        ui = "dashboard-next" if rel.startswith("dashboard-next/") else "dashboard"
        text = f.read_text(encoding="utf-8", errors="replace")
        for line_no, raw in extract_api_literals(text):
            if raw in ("/api", "/api/", "/auth/", "/api/v1", "/api/v1/", "/scim/v2"):
                continue
            if raw.startswith("/scim/v2/${segment("):
                continue
            if raw.endswith("/") and "${" not in raw and len(raw.split("/")) <= 4:
                continue

            clean, norm = normalize_frontend_path(raw)
            if LEGACY_AGENT_X_RE.search(clean):
                legacy_agent_x_calls.append(
                    {"ui": ui, "file": rel, "line": line_no, "path": raw}
                )

            hits = [b for b in backend_routes if b["regex"].match(norm)]
            if hits:
                is_clone = any(h["is_template_clone"] for h in hits)
                entry = {
                    "ui": ui,
                    "file": rel,
                    "line": line_no,
                    "frontend_path": raw,
                    "normalized_path": clean,
                    "backend_path": hits[0]["path"],
                    "backend_methods": "|".join(sorted({h["methods"] for h in hits if h["methods"]})),
                    "backend_endpoint": hits[0]["endpoint"],
                }
                if is_clone:
                    template_clone_calls.append(entry)
                else:
                    matched_calls.append(entry)
            else:
                unmatched_calls.append(
                    {
                        "ui": ui,
                        "file": rel,
                        "line": line_no,
                        "frontend_path": raw,
                        "normalized_path": clean,
                    }
                )

    silent_catches = scan_silent_catches()

    by_ui = {
        "dashboard": {
            "matched_real_routes": sum(1 for c in matched_calls if c["ui"] == "dashboard"),
            "matched_template_clone_routes": sum(1 for c in template_clone_calls if c["ui"] == "dashboard"),
            "unmatched_routes": sum(1 for c in unmatched_calls if c["ui"] == "dashboard"),
        },
        "dashboard_next": {
            "matched_real_routes": sum(1 for c in matched_calls if c["ui"] == "dashboard-next"),
            "matched_template_clone_routes": sum(1 for c in template_clone_calls if c["ui"] == "dashboard-next"),
            "unmatched_routes": sum(1 for c in unmatched_calls if c["ui"] == "dashboard-next"),
        },
    }

    return {
        "routes_source": str(routes_path.relative_to(ROOT)) if routes_path.is_relative_to(ROOT) else str(routes_path),
        "backend_routes_loaded": len(backend_routes),
        "frontend_files_scanned": len(fe_files),
        "matched_real_routes_count": len(matched_calls),
        "matched_template_clone_routes_count": len(template_clone_calls),
        "unmatched_routes_count": len(unmatched_calls),
        "legacy_agent_x_calls_count": len(legacy_agent_x_calls),
        "silent_catch_blocks_in_dashboard_api_count": len(silent_catches),
        "by_ui": by_ui,
        "unmatched_calls": unmatched_calls,
        "template_clone_calls": template_clone_calls,
        "legacy_agent_x_calls": legacy_agent_x_calls,
        "silent_catch_blocks": silent_catches,
        "status": "PASS"
        if (
            len(unmatched_calls) == 0
            and len(template_clone_calls) == 0
            and len(legacy_agent_x_calls) == 0
            and len(silent_catches) == 0
        )
        else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routes",
        default="reports/check/routes.csv",
        help="Path to backend routes.csv or routes.json",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write JSON report",
    )
    args = parser.parse_args(argv)

    routes_path = Path(args.routes)
    if not routes_path.is_absolute():
        routes_path = ROOT / routes_path
    if not routes_path.exists():
        alt = routes_path.with_suffix(".json" if routes_path.suffix == ".csv" else ".csv")
        if alt.exists():
            routes_path = alt
        else:
            print(f"ERROR: routes file not found: {routes_path}", file=sys.stderr)
            return 2

    report = check_contract(routes_path)
    payload = json.dumps(report, indent=2)
    print(payload)

    if args.json_out:
        out_path = Path(args.json_out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
