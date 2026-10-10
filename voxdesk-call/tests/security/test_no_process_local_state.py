"""AST and route-table verification that no process-local state or /extended/ padding remains in app/api/ (Part 1E / Gate G2)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.main import app

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"
FORBIDDEN_STATE_NAMES = {"_rate_buckets", "_idempotency_cache", "_oauth_states"}
FORBIDDEN_STATE_RE = re.compile(r"^_.*(?:_cache|_buckets|_states)$")
PADDING_LINE_RE = re.compile(r"#\s*Line padding\s+\d+")


def _is_mutable_collection_node(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.Dict, ast.List, ast.Set)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"dict", "list", "set", "defaultdict"}:
            return True
    return False


def test_no_process_local_mutable_state_in_api_modules():
    offenders: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name in FORBIDDEN_STATE_NAMES or (
                            FORBIDDEN_STATE_RE.match(name) and _is_mutable_collection_node(stmt.value)
                        ):
                            offenders.append(f"{path.name}:{stmt.lineno}:{name}")
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                if name in FORBIDDEN_STATE_NAMES or (
                    FORBIDDEN_STATE_RE.match(name) and _is_mutable_collection_node(stmt.value)
                ):
                    offenders.append(f"{path.name}:{stmt.lineno}:{name}")
    assert offenders == [], f"Process-local mutable state found in app/api: {offenders}"


def test_no_extended_routes_or_line_padding_in_api_modules():
    padding_offenders: list[str] = []
    extended_literal_offenders: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if PADDING_LINE_RE.search(line):
                padding_offenders.append(f"{path.name}:{lineno}")
            if "/extended/" in line:
                extended_literal_offenders.append(f"{path.name}:{lineno}")

    assert padding_offenders == [], f"Padding lines found in app/api: {padding_offenders}"
    assert extended_literal_offenders == [], (
        f"Literal /extended/ route strings found in app/api: {extended_literal_offenders}"
    )

    # Also verify no enterprise route module mounts /extended/ except the canonical post-call analysis compat router
    illegal_mounted = [
        getattr(r, "path", "")
        for r in app.routes
        if "/extended/" in getattr(r, "path", "")
        and not getattr(r, "path", "").startswith("/api/analysis/extended/")
    ]
    assert illegal_mounted == [], f"Unexpected /extended/ routes mounted: {illegal_mounted}"
