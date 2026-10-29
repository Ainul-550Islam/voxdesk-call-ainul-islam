#!/usr/bin/env python3
"""Verify that a set of delivery ZIP parts is complete and unmodified.

Run from a directory that contains every extracted part (or every part ZIP).
It checks, using only the standard library:

* every part named ``*-part-XX-of-YY.zip`` is present for XX in 1..YY, exactly once;
* every ZIP's members match the sha256 and size recorded in FILE_INDEX.json;
* every file recorded in FILE_INDEX.json is present in exactly one part;
* no extra file appears in any part;
* FILE_INDEX.json itself is in part 1 and this verifier is in the final part.

Exit 0 when everything matches, 1 otherwise, with one line per problem.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

PART_RE = re.compile(r"-part-(\d{2})-of-(\d{2})\.zip$")
VERIFIER = "verify_parts_complete.py"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(directory: Path) -> list[str]:
    problems: list[str] = []
    parts: dict[int, Path] = {}
    totals: set[int] = set()
    for zip_path in sorted(directory.glob("*.zip")):
        match = PART_RE.search(zip_path.name)
        if not match:
            problems.append(f"unexpected ZIP name: {zip_path.name}")
            continue
        number, total = int(match.group(1)), int(match.group(2))
        totals.add(total)
        if number in parts:
            problems.append(f"duplicate part {number}")
        parts[number] = zip_path
    if len(totals) != 1:
        problems.append(f"parts disagree on the total: {sorted(totals)}")
        return problems
    total = totals.pop()
    missing = [n for n in range(1, total + 1) if n not in parts]
    if missing:
        problems.append(f"missing parts: {missing} of {total}")
    if not parts:
        return problems + ["no parts found"]

    index_bytes: bytes | None = None
    index_part: int | None = None
    members_by_part: dict[int, dict[str, bytes]] = {}
    for number, zip_path in sorted(parts.items()):
        with zipfile.ZipFile(zip_path) as archive:
            members = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                members[info.filename] = archive.read(info.filename)
        members_by_part[number] = members
        if "FILE_INDEX.json" in members:
            index_bytes, index_part = members["FILE_INDEX.json"], number
    if index_bytes is None:
        return problems + ["FILE_INDEX.json is not in any part"]
    if index_part != 1:
        problems.append(f"FILE_INDEX.json is in part {index_part}, expected part 1")

    index = json.loads(index_bytes.decode("utf-8"))
    recorded = {entry["path"]: entry for entry in index["files"]}
    seen: dict[str, int] = {}
    for number, members in members_by_part.items():
        for name, data in members.items():
            if name == "FILE_INDEX.json":
                continue
            if name in seen:
                problems.append(f"{name} appears in parts {seen[name]} and {number}")
            seen[name] = number
            entry = recorded.get(name)
            if entry is None:
                problems.append(f"{name} (part {number}) is not in FILE_INDEX.json")
                continue
            if entry["part"] != number:
                problems.append(
                    f"{name} is in part {number} but FILE_INDEX says part {entry['part']}"
                )
            if len(data) != entry["size"]:
                problems.append(f"{name}: size {len(data)} != {entry['size']}")
            if _sha256(data) != entry["sha256"]:
                problems.append(f"{name}: sha256 mismatch")
    for name in recorded:
        if name not in seen:
            problems.append(f"{name} is in FILE_INDEX.json but in no part")
    final = members_by_part.get(total, {})
    if VERIFIER not in final:
        problems.append(f"{VERIFIER} is not in the final part {total}")
    elif _sha256(final[VERIFIER]) != recorded.get(VERIFIER, {}).get("sha256"):
        problems.append(f"{VERIFIER} in part {total} differs from FILE_INDEX.json")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    directory = Path(args[0]) if args else Path(".")
    problems = verify(directory)
    for problem in problems:
        print(f"FAIL  {problem}")
    if not problems:
        print("OK: every part present, every file hashed and placed as FILE_INDEX.json records.")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
