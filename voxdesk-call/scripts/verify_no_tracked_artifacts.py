#!/usr/bin/env python3
"""Fail when a generated artifact is tracked by git.

Three classes of file must never reach a clone the buyer pays for:

* ``*.log``  -- run output. 677 of them were committed at one point; they are
  noise for a reviewer and they leak absolute paths, usernames, and timings.
* ``*.zip``  -- previous delivery archives. A repo that ships its own past
  zips grows without bound and confuses "what is the code?" with "what was
  sent last week?".
* anything over 1 MB -- generated evidence (OpenAPI dumps, pytest result
  dumps, report snapshots). These belong in CI artifacts or in
  ``docs/archive/``, not in the object database of every future clone.

The check is deliberately about *what git tracks*, not what is on disk:
untracking a file keeps it for the developer and removes it from the product.
Genuine exceptions live in ``scripts/tracked_artifact_allowlist.txt``, each
with a stated reason; the allowlist is intentionally tiny.

Exit codes: 0 clean, 1 violations found, 2 the check could not run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = REPO_ROOT / "scripts" / "tracked_artifact_allowlist.txt"
MAX_BYTES = 1_000_000
CLASS_SUFFIXES = (".log", ".zip")
SCHEMA = "voxdesk.tracked_artifact_check/v1"


def load_allowlist(path: Path | None = None) -> set[str]:
    """Parse the allowlist, tolerating comments and blank lines.

    The default is resolved at call time rather than bound as a default
    argument, so a caller (or a test) can point the whole module at a different
    repository by rebinding the module globals.
    """
    path = ALLOWLIST if path is None else path
    if not path.exists():
        return set()
    allowed: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            allowed.add(line)
    return allowed


def tracked_files(repo_root: Path | None = None) -> list[str]:
    """Every path git currently tracks, relative to the repo root."""
    repo_root = REPO_ROOT if repo_root is None else repo_root
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("verify_no_tracked_artifacts: git is not installed", file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace").strip()
        print(
            f"verify_no_tracked_artifacts: `git ls-files` failed: {message}\n"
            "  this check must run inside a git checkout",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return [item for item in result.stdout.decode("utf-8", "replace").split("\0") if item]


def find_violations(repo_root: Path | None = None) -> list[dict[str, object]]:
    repo_root = REPO_ROOT if repo_root is None else repo_root
    allowed = load_allowlist()
    violations: list[dict[str, object]] = []
    for rel in tracked_files(repo_root):
        lowered = rel.lower()
        reason: str | None = None
        if lowered.endswith(CLASS_SUFFIXES):
            reason = f"tracked generated artifact (*{Path(rel).suffix})"
        elif rel in allowed:
            reason = None
        else:
            path = repo_root / rel
            if path.is_file() and path.stat().st_size > MAX_BYTES:
                reason = (
                    f"{path.stat().st_size} bytes exceeds the {MAX_BYTES}-byte "
                    "limit for a tracked file"
                )
        if reason is not None:
            violations.append({"path": rel, "reason": reason})
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="also write the full result as JSON to PATH",
    )
    parser.add_argument(
        "--skip-untracked-check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    violations = find_violations()
    payload = {
        "schema": SCHEMA,
        "ok": not violations,
        "tracked_files_checked": len(tracked_files()),
        "max_bytes": MAX_BYTES,
        "class_suffixes": list(CLASS_SUFFIXES),
        "allowlist": sorted(load_allowlist()),
        "violation_count": len(violations),
        "violations": violations,
    }
    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    if not violations:
        print(f"PASS  no generated artifacts tracked ({payload['tracked_files_checked']} files checked)")
        return 0

    print(
        f"FAIL  {len(violations)} tracked artifact(s) must be untracked "
        f"(of {payload['tracked_files_checked']} tracked files):",
        file=sys.stderr,
    )
    for item in violations[:40]:
        print(f"  - {item['path']}: {item['reason']}", file=sys.stderr)
    if len(violations) > 40:
        print(f"  ... and {len(violations) - 40} more", file=sys.stderr)
    print(
        "\n  Remediate with:  bash scripts/repo_cleanup.sh            # dry run\n"
        "                   bash scripts/repo_cleanup.sh --apply    # untrack\n"
        "  A file that must stay tracked (with a reason) is listed in\n"
        "  scripts/tracked_artifact_allowlist.txt",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
