#!/usr/bin/env python3
"""Build the patch and the numbered ZIP parts for a delivery.

* The patch is built from a temporary git index, so the real index is not touched.
  It includes new files and modifications. It excludes dist/, .cache/ and evidence/closer/,
  which travel in the ZIP parts as evidence.
* Files are packed in name order into parts of at most 30 files. Part 1 carries
  FILE_INDEX.json. The final part carries verify_parts_complete.py.
* Part names are ``<name>-part-XX-of-YY.zip``.

Usage: python scripts/closer/build_delivery.py OUT_DIR NAME
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_FILES_PER_PART = 30
VERIFIER = "scripts/closer/verify_parts_complete.py"
INDEX = "FILE_INDEX.json"


def _git(*args: str, env: dict | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, env=env
    ).stdout


def build_patch(target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        _git("read-tree", "HEAD", env=env)
        # evidence/closer travels in the ZIP parts, not in the patch: it is generated output
        # (coverage JSON, logs) and would bloat the patch without changing any code.
        _git("add", "-A", "--", ".", ":(exclude)dist", ":(exclude).cache", ":(exclude)evidence/closer", env=env)
        patch = _git("diff", "--cached", "--binary", "HEAD", env=env)
    target.write_bytes(patch)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    out_dir = Path(argv[0]).resolve()
    name = argv[1]
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    patch_name = f"{name}.patch"
    build_patch(work / patch_name)
    files: list[tuple[str, Path]] = [(patch_name, work / patch_name)]
    for path in sorted((REPO_ROOT / "evidence" / "closer").rglob("*")):
        if path.is_file():
            files.append((path.relative_to(REPO_ROOT).as_posix(), path))
    verifier = REPO_ROOT / VERIFIER
    files = [f for f in files if f[0] != VERIFIER]
    files.append(("verify_parts_complete.py", verifier))

    total_parts = math.ceil((len(files) + 1) / MAX_FILES_PER_PART)
    assignments: list[tuple[str, Path, int]] = []
    for position, (arcname, path) in enumerate(files):
        slot = position + 1  # FILE_INDEX.json occupies the first slot of part 1
        assignments.append((arcname, path, (slot - 1) // MAX_FILES_PER_PART + 1))
    assert all(part <= total_parts for _a, _p, part in assignments)
    assert assignments[-1][0] == "verify_parts_complete.py"
    # verify_parts_complete.py must sit in the final part, so the final part is never empty.
    final_part = assignments[-1][2]
    if final_part != total_parts:
        raise SystemExit(f"verifier landed in part {final_part}, final part is {total_parts}")

    index = {
        "name": name,
        "parts": total_parts,
        "git_head": _git("rev-parse", "HEAD").decode().strip(),
        "files": [
            {"path": arcname, "part": part, "size": path.stat().st_size, "sha256": _sha256(path)}
            for arcname, path, part in assignments
        ],
    }
    index_path = work / INDEX
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    assignments.insert(0, (INDEX, index_path, 1))

    for part in range(1, total_parts + 1):
        zip_path = out_dir / f"{name}-part-{part:02d}-of-{total_parts:02d}.zip"
        members = [(a, p) for a, p, n in assignments if n == part]
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for arcname, path in members:
                archive.write(path, arcname)
        print(f"{zip_path.name}: {len(members)} files")
    shutil.rmtree(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
