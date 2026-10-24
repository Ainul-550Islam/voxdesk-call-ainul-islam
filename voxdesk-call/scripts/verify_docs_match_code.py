#!/usr/bin/env python3
"""Fail when a number quoted in the docs no longer matches the code.

Documents drift silently. A README that claims "1182 registered routes" or a
sales sheet that claims a route/test count is a factual claim about *this*
commit, so it has to be measured, not remembered. This script measures and
compares:

* ``README.md``           -- the generated statistics block (source files,
  physical source lines, registered routes, collected pytest nodes).
* ``docs/SALES/*.md``     -- every numeric claim about routes, tests or the
  Alembic head that appears in prose, e.g. "1089 routes", "4711 tests",
  "migration head 0062_...".

Collecting pytest nodes costs a real subprocess run, so it is skipped only when
``--skip-tests`` is passed; the value is then reported as unmeasured and the
corresponding comparison is skipped rather than passed silently.

Exit codes: ``0`` every claim matches, ``1`` at least one mismatch, ``2`` the
live values could not be measured.

Usage
-----
    python scripts/verify_docs_match_code.py
    python scripts/verify_docs_match_code.py --skip-tests
    python scripts/verify_docs_match_code.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE_ROOTS = ("app", "services", "dashboard/src", "dashboard-next", "sdk")
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".rs", ".go", ".cpp", ".h", ".hpp"})
SOURCE_SKIP_PARTS = frozenset({"node_modules", "target", "build", "dist", "vendor", "__pycache__", ".next"})
SALES_DIR = "docs/SALES"
README = "README.md"

#: `"registered_routes": 1182` inside the README's fenced JSON block.
README_KEY = re.compile(r'"(?P<key>source_files|source_physical_lines|registered_routes|collected_pytest_nodes)"\s*:\s*(?P<value>\d+)')

#: Prose claims. Each pattern must capture the number in group ``value``.
PROSE_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("routes", re.compile(r"(?P<value>\d{1,6})\s+(?:registered\s+|API\s+)?routes\b", re.I)),
    ("tests", re.compile(r"(?P<value>\d{1,6})\s+(?:collected\s+|passing\s+|backend\s+)?tests\b", re.I)),
    ("migration_head", re.compile(r"(?:alembic|migration)\s+head\s*(?:is|=|:)?\s*`?(?P<value>[0-9]{4}_[a-z0-9_]+)`?", re.I)),
)


@dataclass(frozen=True)
class Mismatch:
    document: str
    line: int
    claim: str
    documented: str
    measured: str

    def as_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def measure_sources(root: Path = ROOT) -> tuple[int, int]:
    """Return ``(source_files, physical_lines)`` exactly as ``repo_stats.py`` counts."""
    files = 0
    lines = 0
    for directory in SOURCE_ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if SOURCE_SKIP_PARTS.intersection(path.relative_to(root).parts):
                continue
            files += 1
            lines += len(_read(path).splitlines())
    return files, lines


def measure_routes(root: Path = ROOT) -> int:
    """Registered routes on the real FastAPI app (not a synthetic list)."""
    from app.main import app  # imported lazily: the app pulls the whole stack

    del root
    return len(app.routes)


def measure_migration_head(root: Path = ROOT) -> str:
    """The single Alembic head id, read through Alembic's own script directory."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one Alembic head, found {len(heads)}: {sorted(heads)}")
    return heads[0]


def measure_collected_tests(root: Path = ROOT, *, timeout: int = 900) -> int:
    """Collected pytest node count, from a real ``--collect-only`` run."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "pytest --collect-only failed; refusing to compare an unmeasured value\n"
            + (result.stdout or "")[-2000:]
            + (result.stderr or "")[-2000:]
        )
    nodes = [line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    unique = set(nodes)
    if not unique or len(unique) != len(nodes):
        raise RuntimeError("pytest collection produced no unique node list")
    return len(nodes)


def readme_claims(root: Path = ROOT) -> tuple[dict[str, tuple[int, int]], list[Mismatch]]:
    """Parse the README statistics block, returning ``{key: (value, line)}``."""
    path = root / README
    claims: dict[str, tuple[int, int]] = {}
    problems: list[Mismatch] = []
    if not path.is_file():
        problems.append(Mismatch(README, 0, "file", "missing", "present"))
        return claims, problems
    for number, line in enumerate(_read(path).splitlines(), 1):
        for match in README_KEY.finditer(line):
            key = match.group("key")
            if key in claims:
                problems.append(
                    Mismatch(README, number, key, str(match.group("value")), "single occurrence required")
                )
                continue
            claims[key] = (int(match.group("value")), number)
    return claims, problems


def compare_readme(root: Path = ROOT, *, measured: dict[str, int | str | None]) -> list[Mismatch]:
    claims, problems = readme_claims(root)
    expected_keys = {"source_files", "source_physical_lines", "registered_routes", "collected_pytest_nodes"}
    for key in sorted(expected_keys - set(claims)):
        problems.append(Mismatch(README, 0, key, "absent", "present"))
    for key, (documented, line) in sorted(claims.items()):
        live = measured.get(key)
        if live is None:
            continue
        if str(documented) != str(live):
            problems.append(Mismatch(README, line, key, str(documented), str(live)))
    return problems


def compare_sales_docs(root: Path = ROOT, *, measured: dict[str, int | str | None]) -> list[Mismatch]:
    problems: list[Mismatch] = []
    sales = root / SALES_DIR
    if not sales.is_dir():
        return problems
    for path in sorted(sales.glob("*.md")):
        relative = str(path.relative_to(root))
        for number, line in enumerate(_read(path).splitlines(), 1):
            for kind, pattern in PROSE_CLAIMS:
                for match in pattern.finditer(line):
                    live = measured.get(kind)
                    if live is None:
                        continue
                    documented = match.group("value")
                    if str(documented) != str(live):
                        problems.append(Mismatch(relative, number, kind, documented, str(live)))
    return problems


def verify(root: Path = ROOT, *, skip_tests: bool = False) -> tuple[list[Mismatch], dict[str, object]]:
    source_files, source_lines = measure_sources(root)
    measured: dict[str, object] = {
        "source_files": source_files,
        "source_physical_lines": source_lines,
        "registered_routes": measure_routes(root),
        "migration_head": measure_migration_head(root),
        "collected_pytest_nodes": None,
    }
    if not skip_tests:
        measured["collected_pytest_nodes"] = measure_collected_tests(root)

    # prose uses the plural noun as the key; the README uses the JSON key.
    prose_view = {
        "routes": measured["registered_routes"],
        "tests": measured["collected_pytest_nodes"],
        "migration_head": measured["migration_head"],
    }
    problems = compare_readme(root, measured=measured)
    problems += compare_sales_docs(root, measured=prose_view)
    return problems, measured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print machine-readable findings")
    parser.add_argument("--skip-tests", action="store_true", help="do not run pytest collection")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        problems, measured = verify(root, skip_tests=args.skip_tests)
    except Exception as exc:  # noqa: BLE001 - any measurement failure is exit 2, never a pass
        print(f"could not measure the live values: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"measured": measured, "mismatches": [item.as_dict() for item in problems]}, indent=2))
    else:
        print("measured: " + json.dumps(measured, sort_keys=True))
        for item in problems:
            print(f"{item.document}:{item.line}: {item.claim} documented={item.documented} measured={item.measured}")
        if not problems:
            print("documented numbers match the code")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
