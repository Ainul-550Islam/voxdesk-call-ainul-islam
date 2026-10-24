"""Machine-readable output for the security harness.

The harness produces findings, not prose. Everything this module writes is JSON
with a digest beside it, so a later reader can tell whether an artefact still
matches the run that produced it:

``evidence/security/security-harness-<stamp>.json``
    one run: the combined status, per-suite counts and every probe with its
    evidence.

``evidence/security/index.json``
    an append-only list of every report this machine has written, each with the
    sha256 of its bytes. Two runs inside the same second are kept as separate
    files rather than overwriting one another, because an index entry that
    points at different bytes than it hashed is worse than no index at all.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from security_harness.models import HarnessReport

#: Where reports go unless the caller says otherwise.
DEFAULT_DIR = Path("evidence/security")

#: The index that lists every report written into a directory.
INDEX_NAME = "index.json"

#: Prefix of one report file. Kept stable: operators grep for it.
REPORT_PREFIX = "security-harness-"


def stamp(moment: dt.datetime | None = None) -> str:
    """``YYYYMMDDTHHMMSSZ`` — sortable, timezone-explicit, filesystem-safe."""
    value = moment or dt.datetime.now(dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def document(report: HarnessReport, *, generated_at: str | None = None) -> dict[str, Any]:
    """The JSON body for one run."""
    body = report.as_dict()
    body["generated_at"] = generated_at or stamp()
    return body


def serialise(report: HarnessReport, *, generated_at: str | None = None) -> str:
    """The exact bytes that will be written, so a caller can hash them first."""
    return json.dumps(
        document(report, generated_at=generated_at), indent=2, sort_keys=True, default=str
    )


def sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _free_path(directory: Path, generated_at: str) -> Path:
    candidate = directory / f"{REPORT_PREFIX}{generated_at}.json"
    sequence = 1
    while candidate.exists():
        sequence += 1
        candidate = directory / f"{REPORT_PREFIX}{generated_at}-{sequence}.json"
    return candidate


def write(
    report: HarnessReport,
    *,
    directory: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Write one report plus its index entry. Returns the metadata written.

    The returned mapping is JSON-serialisable and carries ``file``, ``path``,
    ``sha256``, ``generated_at``, ``status`` and ``counts`` — everything a CI job
    needs to publish the artefact and fail the build.
    """
    target_dir = Path(directory or DEFAULT_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    written_at = generated_at or stamp()
    body = serialise(report, generated_at=written_at)
    path = _free_path(target_dir, written_at)
    path.write_text(body, encoding="utf-8")
    digest = sha256(body)

    entry = {
        "file": path.name,
        "path": str(path),
        "sha256": digest,
        "generated_at": written_at,
        "status": report.status,
        "counts": report.by_status(),
    }
    _append_index(target_dir / INDEX_NAME, entry)
    return entry


def _append_index(index_path: Path, entry: dict[str, Any]) -> None:
    index: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            parsed = json.loads(index_path.read_text(encoding="utf-8"))
            index = parsed if isinstance(parsed, list) else []
        except (OSError, ValueError):
            # A corrupt index is rebuilt from this run rather than aborting a
            # security report: the findings matter more than the ledger.
            index = []
    index.append(entry)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def read_index(directory: Path | None = None) -> list[dict[str, Any]]:
    """Every indexed report under ``directory``; empty when there is none."""
    path = Path(directory or DEFAULT_DIR) / INDEX_NAME
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def verify_index(directory: Path | None = None) -> dict[str, Any]:
    """Re-hash every indexed report and report which entries no longer match.

    Used by operators after a restore: an index whose digests still hold is
    evidence that the files were not edited after the run.
    """
    root = Path(directory or DEFAULT_DIR)
    checked = 0
    mismatched: list[str] = []
    missing: list[str] = []
    for entry in read_index(root):
        name = str(entry.get("file", ""))
        if not name:
            continue
        path = root / name
        if not path.exists():
            missing.append(name)
            continue
        checked += 1
        if sha256(path.read_text(encoding="utf-8")) != entry.get("sha256"):
            mismatched.append(name)
    return {
        "ok": not mismatched and not missing,
        "checked": checked,
        "mismatched": mismatched,
        "missing": missing,
    }


__all__ = [
    "DEFAULT_DIR",
    "INDEX_NAME",
    "REPORT_PREFIX",
    "document",
    "read_index",
    "serialise",
    "sha256",
    "stamp",
    "verify_index",
    "write",
]
