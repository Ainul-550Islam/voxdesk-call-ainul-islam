#!/usr/bin/env python3
"""Find (and optionally delete) all-NUL and empty source files.

A file that is entirely NUL bytes is a truncated write or a corrupted checkout,
not source code: Python cannot parse it (`SyntaxError: source code string cannot
contain null bytes`), so every AST-based truth guard in this repository aborts on
the first one it meets. This script is the detector the guards rely on, and it
ships with the repository so an operator can *see* the population before
anything is deleted.

Two kinds of finding are reported:

* ``nul``   -- the file is not empty but every byte is ``0x00``.
* ``empty`` -- the file has zero bytes and is not a sanctioned empty marker.

Empty ``__init__.py`` files (and the other names in :data:`ALLOWED_EMPTY`) are
deliberate package/build markers, not corruption, and are never reported. That
keeps this script agreeing with ``scripts/verify_no_null_bytes.py`` instead of
creating a second, contradictory definition of "corrupt".

Exit codes
----------
``0``  nothing found (or ``--apply`` removed everything it found)
``1``  findings exist in ``--check`` mode
``2``  usage error, or ``--apply`` could not delete every finding

Usage
-----
    python scripts/find_zero_byte_files.py --check
    python scripts/find_zero_byte_files.py --check --json
    python scripts/find_zero_byte_files.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Directories never walked. ``docs/archive`` holds audit snapshots of the
#: repository exactly as it was, corruption included; rewriting history there
#: would destroy the evidence the snapshots exist to preserve.
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "third_party",
        "target",
        "build",
        "dist",
        "vendor",
        ".next",
        ".cache",
        "archive",
    }
)

#: Relative prefixes that look like a skipped directory name but are real code.
SKIP_RELATIVE_PREFIXES: tuple[str, ...] = ()

#: Empty files that are intentional. Mirrors ``verify_no_null_bytes.py``.
ALLOWED_EMPTY = frozenset(
    {
        "__init__.py",
        "__init__.pyi",
        "py.typed",
        ".gitkeep",
        "fake_success_allowlist.txt",
    }
)

#: Only text files are considered. A ``.wav`` fixture or a static ``.a`` archive
#: contains NUL bytes by definition; flagging those would train an operator to
#: ignore the report. The suffix set matches ``verify_no_null_bytes.py``.
TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".proto",
        ".sql",
        ".go",
        ".rs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".sh",
        ".ini",
        ".cfg",
        ".html",
        ".css",
    }
)
TEXT_NAMES = frozenset({"Makefile", "Dockerfile", ".gitattributes", ".gitignore", ".env.example"})

#: Trees that carry product source. ``--check`` in CI scopes to these so a
#: local ``.venv`` or a downloaded artifact can never fail the gate.
DEFAULT_ROOTS = (
    "app",
    "services",
    "scripts",
    "tests",
    "loadtest",
    "dashboard",
    "dashboard-next",
    "sdk",
    "alembic",
    "docs",
)


@dataclass(frozen=True)
class ZeroByteFinding:
    """One corrupt file, with the evidence needed to decide what to do."""

    path: str
    kind: str
    size: int
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _is_skipped(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if any(part in SKIP_DIR_NAMES for part in relative.parts[:-1]):
        return True
    return any(str(relative).startswith(prefix) for prefix in SKIP_RELATIVE_PREFIXES)


def _is_text_candidate(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES


def _classify(data: bytes, name: str) -> tuple[str, str] | None:
    """Return ``(kind, reason)`` for a corrupt file, or ``None`` when healthy."""
    if data:
        if data.count(0) == len(data):
            return "nul", "every byte is NUL (0x00)"
        if b"\x00" in data:
            return "nul", "contains NUL (0x00) bytes"
        return None
    if name in ALLOWED_EMPTY:
        return None
    return "empty", "zero-byte file is not a sanctioned empty marker"


def find_zero_byte_files(
    root: Path | str = ROOT,
    roots: tuple[str, ...] | list[str] | None = None,
) -> list[ZeroByteFinding]:
    """Walk ``root`` (or the given subtrees) and return every finding."""
    root_path = Path(root).resolve()
    findings: list[ZeroByteFinding] = []
    for entry in roots if roots is not None else DEFAULT_ROOTS:
        base = root_path / entry
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = sorted(path for path in base.rglob("*") if path.is_file())
        else:
            continue
        for path in candidates:
            if _is_skipped(path, root_path):
                continue
            if not _is_text_candidate(path):
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:  # pragma: no cover - unreadable file is an error
                findings.append(
                    ZeroByteFinding(
                        path=str(path.relative_to(root_path)),
                        kind="unreadable",
                        size=-1,
                        reason=f"could not read file: {exc.__class__.__name__}",
                    )
                )
                continue
            verdict = _classify(data, path.name)
            if verdict is None:
                continue
            kind, reason = verdict
            findings.append(
                ZeroByteFinding(
                    path=str(path.relative_to(root_path)),
                    kind=kind,
                    size=len(data),
                    reason=reason,
                )
            )
    return findings


def delete_findings(findings: list[ZeroByteFinding], root: Path | str = ROOT) -> list[str]:
    """Delete every reported file. Returns the paths that could not be removed."""
    root_path = Path(root)
    failures: list[str] = []
    for finding in findings:
        target = root_path / finding.path
        try:
            target.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{finding.path}: {exc.__class__.__name__}")
    return failures


def _report(findings: list[ZeroByteFinding], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([finding.as_dict() for finding in findings], indent=2))
        return
    if not findings:
        print("no zero-byte or NUL-only files found")
        return
    for finding in findings:
        print(f"{finding.kind:5s} {finding.path} ({finding.size} bytes) -- {finding.reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report findings and exit 1 when any exist")
    parser.add_argument("--apply", action="store_true", help="delete every reported file")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    parser.add_argument(
        "--roots",
        default=",".join(DEFAULT_ROOTS),
        help="comma-separated subtrees to scan",
    )
    args = parser.parse_args(argv)

    if not args.check and not args.apply:
        args.check = True
    if args.check and args.apply:
        parser.error("--check and --apply are mutually exclusive")

    roots = [item.strip() for item in args.roots.split(",") if item.strip()]
    findings = find_zero_byte_files(args.root, roots)

    if args.apply:
        failures = delete_findings(findings, args.root)
        _report(findings, as_json=args.json)
        for failure in failures:
            print(f"could not delete {failure}", file=sys.stderr)
        if failures:
            return 2
        return 0

    _report(findings, as_json=args.json)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
