#!/usr/bin/env python3
"""Fail when a number or reference quoted in the docs no longer matches the code.

Documents drift silently. A README that claims "1182 registered routes", a sales
sheet that claims a route/test count, or a runbook that cites a deleted script,
make target, API route, or env var is a factual claim about *this* commit, so it
has to be measured, not remembered. This script measures and compares:

* ``README.md``           -- the generated statistics block (source files,
  physical source lines, registered routes, collected pytest nodes).
* ``docs/SALES/*.md``     -- every numeric claim about routes, tests or the
  Alembic head that appears in prose, e.g. "1089 routes", "4711 tests",
  "migration head 0062_...".
* Active docs under ``README.md`` and ``docs/**/*.md`` (excluding ``docs/archive``):
  - every ``scripts/<name>.(py|sh)`` path exists on disk,
  - every ``make <target>`` reference exists in ``Makefile*``,
  - every backticked ``/api/...`` route matches a registered ``app.main:app`` route,
  - every backticked ``ENV_VAR`` in buyer/sales/due-diligence/deploy/security docs
    exists in ``Settings``, ``CarrierV2Settings``, ``.env.example``, or code.

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
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pytest_collection_stats import measure_collected_pytest_nodes  # noqa: E402

SOURCE_ROOTS = ("app", "services", "dashboard/src", "dashboard-next", "sdk")
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".rs", ".go", ".cpp", ".h", ".hpp"})
SOURCE_SKIP_PARTS = frozenset({"node_modules", "target", "build", "dist", "vendor", "__pycache__", ".next"})
SALES_DIR = "docs/SALES"
README = "README.md"

#: Historical point-in-time audit reports that quote legacy routes as negative examples.
HISTORICAL_REPORT_DOCS = frozenset(
    {
        "docs/PROMPT8B_CONTRACT_CORRECTIONS.md",
        "docs/STEP9-REPORT.md",
    }
)

#: Non-env-var uppercase status/protocol tokens appearing in backticks in prose.
NON_ENV_UPPER_TOKENS = frozenset(
    {
        "API_ONLY",
        "NOT_CONFIGURED",
        "LIVE_PROVEN",
        "VERIFIED_WITH_FAKES",
        "SKIPPED_NO_CREDENTIALS",
        "UNSUPPORTED_CHANNEL",
        "UNSUPPORTED_CAPABILITY",
        "VALIDATION_ERROR",
        "UPSTREAM_PROVIDER_ERROR",
        "MEDIA_STREAM_NOT_CONFIGURED",
        "SESSION_EXPIRED",
        "TEST_SESSION_EXPIRED",
        "EXPERIMENT_ALREADY_RUNNING",
        "AGENT_NOT_PUBLISHED",
        "EXPERIMENT_NOT_RUNNING",
        "RECIPIENT_OPTED_OUT",
        "INVALID_WEBHOOK_SIGNATURE",
        "INVALID_TWILIO_SIGNATURE",
        "NODE_ENV",
    }
)

#: `"registered_routes": 1182` inside the README's fenced JSON block.
README_KEY = re.compile(
    r'"(?P<key>source_files|source_physical_lines|registered_routes|collected_pytest_nodes)"\s*:\s*(?P<value>\d+)'
)

#: Prose claims. Each pattern must capture the number in group ``value``.
PROSE_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("routes", re.compile(r"(?P<value>\d{1,6})\s+(?:registered\s+|API\s+)?routes\b", re.I)),
    ("tests", re.compile(r"(?P<value>\d{1,6})\s+(?:collected\s+|passing\s+|backend\s+)?tests\b", re.I)),
    ("migration_head", re.compile(r"(?:alembic|migration)\s+head\s*(?:is|=|:)?\s*`?(?P<value>[0-9]{4}_[a-z0-9_]+)`?", re.I)),
)

SCRIPT_REF_RE = re.compile(r"\bscripts/([a-zA-Z0-9_./-]+\.(?:py|sh))\b")
MAKE_REF_RE = re.compile(r"`make(?:\s+-C\s+\S+)?\s+([a-zA-Z0-9_.-]+)")
API_ROUTE_REF_RE = re.compile(r"`(?:(?:GET|POST|PUT|PATCH|DELETE|WS)\s+)?(/api/[a-zA-Z0-9_./{}:<>*-]+)`")
ENV_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})(?:=[^`]*)?`")


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
        for current, child_dirs, filenames in os.walk(base):
            child_dirs[:] = sorted(name for name in child_dirs if name not in SOURCE_SKIP_PARTS)
            for filename in filenames:
                path = Path(current) / filename
                if path.suffix not in SOURCE_SUFFIXES:
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
    return measure_collected_pytest_nodes(root, timeout=timeout)


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


MeasuredValue = int | str | None


def compare_readme(root: Path = ROOT, *, measured: dict[str, MeasuredValue]) -> list[Mismatch]:
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


def compare_sales_docs(root: Path = ROOT, *, measured: dict[str, MeasuredValue]) -> list[Mismatch]:
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


def _collect_make_targets(root: Path) -> set[str]:
    targets: set[str] = set()
    for mf in sorted(root.glob("Makefile*")):
        if not mf.is_file():
            continue
        for match in re.finditer(r"^([a-zA-Z0-9_.-]+)\s*:", _read(mf), re.M):
            target = match.group(1)
            if not target.startswith("."):
                targets.add(target)
    return targets


def _collect_known_env_vars(root: Path) -> set[str]:
    from app.carriers_v2.settings import CarrierV2Settings
    from app.core.config import Settings

    known: set[str] = {name.upper() for name in Settings.model_fields}
    known.update(name.upper() for name in CarrierV2Settings.model_fields)

    scan_paths: list[Path] = (
        list(root.glob(".env*"))
        + list(root.glob("docker-compose*.yml"))
        + list(root.glob("Dockerfile*"))
        + list(root.glob("Makefile*"))
    )
    if (root / ".github" / "workflows").is_dir():
        scan_paths.extend((root / ".github" / "workflows").glob("*.yml"))
    if (root / "scripts").is_dir():
        scan_paths.extend((root / "scripts").glob("*.py"))
        scan_paths.extend((root / "scripts").glob("*.sh"))
    if (root / "app").is_dir():
        scan_paths.extend((root / "app").rglob("*.py"))
    if (root / "dashboard-next").is_dir():
        for p in (root / "dashboard-next").rglob("*"):
            if p.suffix in {".ts", ".tsx"} and not SOURCE_SKIP_PARTS.intersection(p.parts):
                scan_paths.append(p)

    upper_re = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
    for path in scan_paths:
        if not path.is_file():
            continue
        for match in upper_re.finditer(_read(path)):
            known.add(match.group(1))
    return known


def compare_doc_references(root: Path = ROOT) -> list[Mismatch]:
    """Verify scripts, make targets, /api routes, and env vars cited across active docs."""
    from app.main import app

    if not (root / "scripts").is_dir() or not (root / "Makefile").is_file():
        return []

    problems: list[Mismatch] = []
    make_targets = _collect_make_targets(root)
    known_envs = _collect_known_env_vars(root)

    route_regexes: list[re.Pattern[str]] = []
    route_prefixes: set[str] = set()
    for route in app.routes:
        raw_path = getattr(route, "path", None)
        if not raw_path:
            continue
        clean_path = raw_path.rstrip("/") or "/"
        route_prefixes.add(clean_path)
        escaped = re.escape(clean_path).replace(r"\{", "{").replace(r"\}", "}")
        pattern = "^" + re.sub(r"\{[^}/]+(?::path)?\}", r"[^/]+", escaped) + "$"
        route_regexes.append(re.compile(pattern))

    def _route_exists(doc_route: str) -> bool:
        cleaned = doc_route.split("?", 1)[0].rstrip("/*.,;:")
        if not cleaned or cleaned == "/api":
            return True
        sample = re.sub(r"(\{[^}]+\}|<[^>]+>|:[a-zA-Z_][a-zA-Z0-9_]*)", "sample_id", cleaned)
        if any(rx.match(sample) for rx in route_regexes):
            return True
        for prefix in route_prefixes:
            prefix_sample = re.sub(r"\{[^}]+\}", "sample_id", prefix)
            if prefix_sample.startswith(sample + "/"):
                return True
        return False

    doc_files: list[Path] = []
    if (root / README).is_file():
        doc_files.append(root / README)
    if (root / "docs").is_dir():
        for md in sorted((root / "docs").rglob("*.md")):
            if "archive" not in md.parts:
                doc_files.append(md)

    env_checked_dirs = {"docs/SALES", "docs/DUE_DILIGENCE"}
    env_checked_files = {
        "README.md",
        "docs/BUYER_DEPLOY_GUIDE.md",
        "docs/HANDOFF_CHECKLIST.md",
        "docs/SECURITY_POSTURE.md",
        "docs/LIVE_EVIDENCE.md",
        "docs/MESSAGING_CHANNELS.md",
    }

    for doc_path in doc_files:
        rel = str(doc_path.relative_to(root)).replace("\\", "/")
        check_routes = rel not in HISTORICAL_REPORT_DOCS
        check_envs = rel in env_checked_files or any(rel.startswith(d + "/") for d in env_checked_dirs)

        lines = _read(doc_path).splitlines()
        for lineno, line in enumerate(lines, 1):
            for sm in SCRIPT_REF_RE.finditer(line):
                script_rel = sm.group(0)
                if not (root / script_rel).is_file():
                    problems.append(Mismatch(rel, lineno, "script_path", script_rel, "existing file under scripts/"))

            for mm in MAKE_REF_RE.finditer(line):
                if "-C" in mm.group(0):
                    continue
                tgt = mm.group(1)
                if tgt not in make_targets:
                    problems.append(Mismatch(rel, lineno, "make_target", tgt, "defined target in Makefile*"))

            if check_routes:
                # Skip lines explicitly documenting non-matching or external service routes (e.g. Grafana /api/health, -> 404 probes)
                if "Grafana" not in line and "→ 404" not in line and "not `/api/" not in line:
                    prev_line = lines[lineno - 2] if lineno >= 2 else ""
                    if not prev_line.rstrip().endswith("not"):
                        for rm in API_ROUTE_REF_RE.finditer(line):
                            candidate = rm.group(1).split("*")[0].split("...")[0].rstrip("/")
                            if candidate and candidate != "/api" and not _route_exists(candidate):
                                problems.append(
                                    Mismatch(rel, lineno, "api_route", rm.group(1), "registered FastAPI route")
                                )

            if check_envs:
                for em in ENV_TOKEN_RE.finditer(line):
                    var = em.group(1)
                    if "_" not in var or var in NON_ENV_UPPER_TOKENS:
                        continue
                    if var not in known_envs:
                        problems.append(
                            Mismatch(rel, lineno, "env_var", var, "declared in Settings/.env.example/code")
                        )

    return problems


def verify(root: Path = ROOT, *, skip_tests: bool = False) -> tuple[list[Mismatch], dict[str, MeasuredValue]]:
    source_files, source_lines = measure_sources(root)
    measured: dict[str, MeasuredValue] = {
        "source_files": source_files,
        "source_physical_lines": source_lines,
        "registered_routes": measure_routes(root),
        "migration_head": measure_migration_head(root),
        "collected_pytest_nodes": None,
    }
    if not skip_tests:
        measured["collected_pytest_nodes"] = measure_collected_tests(root)

    prose_view = {
        "routes": measured["registered_routes"],
        "tests": measured["collected_pytest_nodes"],
        "migration_head": measured["migration_head"],
    }
    problems = compare_readme(root, measured=measured)
    problems += compare_sales_docs(root, measured=prose_view)
    problems += compare_doc_references(root)
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
            print("documented numbers and references match the code")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
