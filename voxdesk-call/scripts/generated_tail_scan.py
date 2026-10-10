#!/usr/bin/env python3
"""Scan frontend trees for generated-tail residue, verified:true/real:true literals, and silent API catch blocks.

Usage:
    python3 scripts/generated_tail_scan.py [paths...] [--json-out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    "node_modules",
    "dist",
    "build",
    ".next",
    ".cache",
    "coverage",
    "__pycache__",
}
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs"}

NUMBERED_DEF_RE = re.compile(
    r"\b(?:Helper|Service|Utility|Manager|Controller|Processor|Handler|Validator|"
    r"Formatter|Mapper|Adapter|Factory|Builder|Provider|Consumer|Observer|"
    r"Listener|Dispatcher|Resolver|Interceptor)_\d+\b"
)
VERIFIED_REAL_RE = re.compile(
    r"verified\s*:\s*true\s*,\s*real\s*:\s*true",
    re.IGNORECASE,
)
BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
LINE_COMMENT_RE = re.compile(r"(?<!:)//.*$")
SILENT_CATCH_RE = re.compile(
    r"catch\s*(?:\([^)]*\))?\s*\{\s*return\s*(?:\[\s*\]|null)\s*;?\s*\}"
)


def strip_comments(text: str) -> str:
    without_blocks = BLOCK_COMMENT_RE.sub("", text)
    return "\n".join(
        LINE_COMMENT_RE.sub("", line) for line in without_blocks.splitlines()
    )


def iter_source_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in SOURCE_SUFFIXES else []
    if not target.exists():
        return []
    out: list[Path] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        rel_parts = path.relative_to(target).parts
        if SKIP_DIRS.intersection(rel_parts):
            continue
        out.append(path)
    return out


def scan_generated_tails(paths: list[Path]) -> dict[str, Any]:
    files_scanned: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        for f in iter_source_files(p):
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files_scanned.append(f)

    # Also ensure both dashboard/src and dashboard-next are checked for verified:true, real:true
    extra_roots = [ROOT / "dashboard" / "src", ROOT / "dashboard-next"]
    all_ui_files: list[Path] = list(files_scanned)
    for extra in extra_roots:
        for f in iter_source_files(extra):
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                all_ui_files.append(f)

    tail_files: list[dict[str, Any]] = []
    total_generated_lines = 0
    for f in files_scanned:
        text = f.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(text)
        matches = list(NUMBERED_DEF_RE.finditer(code))
        matched_lines = [
            idx + 1
            for idx, line in enumerate(code.splitlines())
            if NUMBERED_DEF_RE.search(line)
        ]
        if len(matches) >= 15:
            first_line = matched_lines[0] if matched_lines else 1
            total_lines = len(text.splitlines())
            gen_lines = max(0, total_lines - first_line + 1)
            total_generated_lines += gen_lines
            tail_files.append(
                {
                    "file": str(f.resolve().relative_to(ROOT)),
                    "numbered_definitions": len(matches),
                    "first_generated_line": first_line,
                    "generated_lines": gen_lines,
                }
            )

    verified_real_matches: list[dict[str, Any]] = []
    for f in all_ui_files:
        rel = str(f.resolve().relative_to(ROOT))
        # Skip self-guard test that asserts absence of the literal
        if rel.endswith("no-generated-tails.test.ts"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(text)
        for line_no, line in enumerate(code.splitlines(), 1):
            if VERIFIED_REAL_RE.search(line):
                verified_real_matches.append(
                    {
                        "file": rel,
                        "line": line_no,
                        "snippet": line.strip()[:120],
                    }
                )

    silent_api_catches: list[dict[str, Any]] = []
    api_dir = ROOT / "dashboard" / "src" / "api"
    for f in iter_source_files(api_dir):
        rel = str(f.resolve().relative_to(ROOT))
        # client.ts readErrorBody parses optional JSON error bodies on non-2xx responses before throwing ApiError
        if rel == "dashboard/src/api/client.ts":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(text)
        for m in SILENT_CATCH_RE.finditer(code):
            line_no = code[: m.start()].count("\n") + 1
            silent_api_catches.append(
                {
                    "file": rel,
                    "line": line_no,
                    "match": " ".join(m.group(0).split()),
                }
            )

    return {
        "scanned_files_count": len(files_scanned),
        "ui_files_checked_for_verified_real": len(all_ui_files),
        "baseline_appendix_d": {
            "files_with_generated_tails": 84,
            "generated_lines": 50425,
        },
        "files_with_generated_tails_count": len(tail_files),
        "generated_lines_remaining": total_generated_lines,
        "files_with_generated_tails": tail_files,
        "verified_real_pairs_count": len(verified_real_matches),
        "verified_real_pairs": verified_real_matches,
        "silent_api_catch_blocks_count": len(silent_api_catches),
        "silent_api_catch_blocks": silent_api_catches,
        "status": "PASS"
        if (
            len(tail_files) == 0
            and total_generated_lines == 0
            and len(verified_real_matches) == 0
            and len(silent_api_catches) == 0
        )
        else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["dashboard/src"],
        help="Directories or files to scan for generated tails.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write JSON scan report.",
    )
    args = parser.parse_args(argv)

    target_paths = [
        (ROOT / p if not Path(p).is_absolute() else Path(p)) for p in args.paths
    ]
    report = scan_generated_tails(target_paths)
    payload = json.dumps(report, indent=2)
    print(payload)
    if args.json_out:
        out_path = ROOT / args.json_out if not Path(args.json_out).is_absolute() else Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
