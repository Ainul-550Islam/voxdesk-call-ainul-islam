#!/usr/bin/env python3
"""Fail when a route module is neither mounted nor explicitly accounted for.

A ``app/api/*.py`` module that creates an ``APIRouter`` and is never imported
into ``app/main.py`` is dead code with a URL attached: it advertises endpoints
nobody can call, it inflates route/file counts quoted in sales material, and it
is the exact shape a generated "endpoint-N" file arrives in. This guard reads
the real application entry point with :mod:`ast` (never by importing it, so it
stays fast and cannot execute application code) and decides, for every module:

* **mounted**   -- ``app/main.py`` imports a name from it and passes that name to
  ``app.include_router(...)``;
* **helper**    -- the module builds no ``APIRouter`` at all (error mappers,
  guards, schema helpers). Nothing to mount, nothing to report;
* **allowlisted** -- a ``APIRouter`` module that is knowingly not mounted. Every
  entry names a reason and the list is shrink-only: it may not grow;
* **orphan**    -- none of the above. This fails the build.

The same pass scans every route module for the filler markers that arrive with
generated padding (``/endpoint-N``, ``NO SKIP FULL CODE``, "1050+ lines").

Exit codes: ``0`` clean, ``1`` findings, ``2`` the entry point could not be read.

Usage
-----
    python scripts/verify_no_dead_routers.py
    python scripts/verify_no_dead_routers.py --json
    python scripts/verify_no_dead_routers.py --strict   # allowlist must be empty
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = "app/main.py"
ROUTE_PACKAGE = "app/api"

#: Route modules that build an ``APIRouter`` but are deliberately not mounted.
#: Shrink-only: adding a key here is the change this guard exists to stop, so CI
#: compares the set against the frozen baseline below.
ALLOWLIST: dict[str, str] = {
    "app.api.pcap_routes": (
        "packet-capture surface is retained for audit history but is not mounted: the "
        "capture artifact table was dropped in 0062_drop_pcap_artifacts, so every route in "
        "it would answer 501; deletion is scheduled with the Batch 07 hardening pass"
    ),
    "app.api.machine_routes": (
        "machine-credential administration is superseded by app/api/service_account_routes.py, "
        "which is mounted and covers the same operations; deletion is scheduled with the "
        "Batch 07 hardening pass"
    ),
    "app.api.call_outcome_routes": (
        "human-recorded outcome and QA metric endpoints for /api/conversations/{call_id}/outcomes; "
        "not mounted in app/main.py, so no operator can reach them. Mounting is a product "
        "decision (it duplicates part of app/qa), queued for the Batch 07 pass"
    ),
    "app.api.industry_template_routes": (
        "compliance industry-template instantiation under /industry-templates; not mounted in "
        "app/main.py. Surfacing it is a compliance-product decision queued for Batch 03/07"
    ),
}

#: Cannot grow without an explicit, reviewed edit to this constant.
ALLOWLIST_BASELINE = frozenset(ALLOWLIST)

#: Markers that only appear in generated filler, never in reviewed code.
FILLER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("generated endpoint path", re.compile(r"/endpoint-\d+")),
    ("line-count banner", re.compile(r"NO SKIP FULL CODE|\b\d{3,}\+\s*lines\b", re.I)),
    ("numbered clone helper", re.compile(r"\b_(?:real|CONST)_\d+\b")),
    ("numbered clone class", re.compile(r"\b(?:Struct|Class)\d+\b")),
    ("padding banner", re.compile(r"Padding\b[^\n]*\blin(?:e|es)\s+\d+", re.I)),
)


@dataclass(frozen=True)
class ModuleVerdict:
    module: str
    path: str
    defines_router: bool
    status: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FillerFinding:
    path: str
    line: int
    marker: str
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def mounted_names(entrypoint: Path = ROOT / ENTRYPOINT) -> tuple[set[str], dict[str, str]]:
    """Return ``(mounted_names, imported_aliases)`` from the FastAPI entry point."""
    tree = ast.parse(_read(entrypoint), filename=str(entrypoint))
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported[alias.asname or alias.name] = node.module
    mounted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "include_router":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Name):
                mounted.add(argument.id)
    return mounted, imported


def defines_router(tree: ast.Module) -> bool:
    """True when the module assigns ``APIRouter(...)`` to a name."""
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if isinstance(value, ast.Call) and (
            (isinstance(value.func, ast.Name) and value.func.id == "APIRouter")
            or (isinstance(value.func, ast.Attribute) and value.func.attr == "APIRouter")
        ):
            return True
    return False


def module_name_for(path: Path, root: Path = ROOT) -> str:
    relative = Path(path).resolve().relative_to(Path(root).resolve()).with_suffix("")
    return ".".join(relative.parts)


def classify_modules(root: Path = ROOT) -> list[ModuleVerdict]:
    mounted, imported = mounted_names(root / ENTRYPOINT)
    mounted_modules = {imported[name] for name in mounted if name in imported}
    verdicts: list[ModuleVerdict] = []
    for path in sorted((root / ROUTE_PACKAGE).rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = module_name_for(path, root)
        try:
            tree = ast.parse(_read(path), filename=str(path))
        except SyntaxError as exc:
            verdicts.append(
                ModuleVerdict(module, str(path.relative_to(root)), False, "orphan", f"unparsable source: {exc.msg}")
            )
            continue
        builds_router = defines_router(tree)
        if module in mounted_modules:
            status, reason = "mounted", "imported and include_router()'d in app/main.py"
        elif not builds_router:
            status, reason = "helper", "module defines no APIRouter; nothing to mount"
        elif module in ALLOWLIST:
            status, reason = "allowlisted", ALLOWLIST[module]
        else:
            status, reason = "orphan", "module defines an APIRouter but app/main.py never mounts it"
        verdicts.append(ModuleVerdict(module, str(path.relative_to(root)), builds_router, status, reason))
    return verdicts


def scan_filler(root: Path = ROOT) -> list[FillerFinding]:
    findings: list[FillerFinding] = []
    for path in sorted((root / ROUTE_PACKAGE).rglob("*.py")):
        for number, line in enumerate(_read(path).splitlines(), 1):
            for label, pattern in FILLER_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        FillerFinding(
                            path=str(path.relative_to(root)),
                            line=number,
                            marker=label,
                            text=line.strip()[:200],
                        )
                    )
    return findings


def scan(root: Path = ROOT, *, strict: bool = False) -> list[dict]:
    """Return every problem. An empty list means the routing surface is honest."""
    problems: list[dict] = []
    verdicts = classify_modules(root)
    for verdict in verdicts:
        if verdict.status == "orphan":
            problems.append({"kind": "orphan_route_module", **verdict.as_dict()})
    for finding in scan_filler(root):
        problems.append({"kind": "filler_marker", **finding.as_dict()})
    allowlisted = {verdict.module for verdict in verdicts if verdict.status == "allowlisted"}
    for module in sorted(allowlisted - ALLOWLIST_BASELINE):
        problems.append(
            {
                "kind": "allowlist_growth",
                "module": module,
                "reason": "a new dead-router exception was added; mount the module or delete it",
            }
        )
    for module in sorted(set(ALLOWLIST) - allowlisted):
        problems.append(
            {
                "kind": "stale_allowlist_entry",
                "module": module,
                "reason": "the module no longer exists or is now mounted; remove the exception",
            }
        )
    if strict:
        for module in sorted(allowlisted):
            problems.append(
                {
                    "kind": "allowlisted_dead_router",
                    "module": module,
                    "reason": ALLOWLIST[module],
                }
            )
    return problems


def summary(root: Path = ROOT) -> dict[str, int]:
    counts: dict[str, int] = {}
    for verdict in classify_modules(root):
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print machine-readable findings")
    parser.add_argument("--strict", action="store_true", help="treat allowlisted modules as failures too")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        problems = scan(root, strict=args.strict)
    except (OSError, SyntaxError) as exc:
        print(f"cannot read the FastAPI entry point: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(problems, indent=2))
    else:
        counts = summary(root)
        print(
            "route modules: "
            + ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
        )
        for problem in problems:
            location = problem.get("path", problem.get("module", ""))
            detail = problem.get("reason", problem.get("text", ""))
            print(f"{problem['kind']}: {location}: {detail}")
        if not problems:
            print("no dead route modules and no filler markers")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
