"""Signed, offline-verifiable audit export bundles.

A bundle is a ZIP containing:

* ``events.jsonl`` — one governance evidence event per line, in sequence order;
* ``chain-report.json`` — the verification result over the covered range, so a
  reader sees what the exporter actually checked;
* ``manifest.json`` — counts, range, chain head, and the SHA-256 of every other
  member;
* ``manifest.sig`` — the Ed25519 signature over the exact bytes of
  ``manifest.json``.

Verification needs only the ZIP and the signer's public key: no database, no
VoxDesk process, no network. That is the property an auditor relies on, and it
is why the manifest lists member hashes rather than the ZIP listing itself, and
why :func:`verify_bundle_bytes` re-derives the hash chain from the exported
lines instead of trusting the included report.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.compliance_v2.audit_integrity.signing import sign_bytes
from app.governance.hashing import chain_hash, sha256_hex

BUNDLE_FORMAT = "voxdesk.audit.bundle.v1"
EVENTS_MEMBER = "events.jsonl"
MANIFEST_MEMBER = "manifest.json"
SIGNATURE_MEMBER = "manifest.sig"
CHAIN_REPORT_MEMBER = "chain-report.json"

#: Domain separation: a signature made over a manifest cannot be replayed as a
#: signature over some other document with the same bytes.
SIGNING_CONTEXT = b"voxdesk.audit.bundle.manifest.v1\n"


def _iso(moment: dt.datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclasses.dataclass(frozen=True)
class BundleRequest:
    tenant_id: uuid.UUID | str
    range_start: dt.datetime
    range_end: dt.datetime
    export_id: str = ""
    requested_by: str = ""
    chain_scope: str = ""


@dataclasses.dataclass(frozen=True)
class BundleVerification:
    ok: bool
    reason: str = ""
    event_count: int = 0
    head_hash: str = ""
    key_id: str = ""
    chain_ok: bool = False
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "event_count": self.event_count,
            "head_hash": self.head_hash,
            "key_id": self.key_id,
            "chain_ok": self.chain_ok,
            "detail": self.detail,
        }


def canonical_manifest(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _event_line(row: Any) -> dict[str, Any]:
    """Project an evidence row into the exported line shape.

    The fields are exactly the ones the hash covers plus the identifiers a
    reader needs; nothing is added that the chain does not already commit to,
    so re-deriving the hash from the line is possible without the database.
    """
    return {
        "sequence": int(row.sequence),
        "chain_scope": row.chain_scope,
        "event_type": row.event_type,
        "actor_id": str(row.actor_id) if getattr(row, "actor_id", None) else None,
        "actor_type": row.actor_type,
        "correlation_id": row.correlation_id,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "payload": row.payload,
        "payload_hash": row.payload_hash,
        "previous_hash": row.previous_hash,
        "event_hash": row.event_hash,
        "occurred_at": _iso(row.occurred_at),
    }


def rehash_line(line: Mapping[str, Any]) -> str:
    """Re-derive ``event_hash`` from an exported line."""
    return chain_hash(
        previous_hash=line.get("previous_hash"),
        payload={
            "actor_id": line.get("actor_id"),
            "actor_type": line.get("actor_type"),
            "correlation_id": line.get("correlation_id"),
            "subject_type": line.get("subject_type"),
            "subject_id": line.get("subject_id"),
            "payload_hash": line.get("payload_hash"),
            "payload": line.get("payload"),
        },
        event_type=str(line.get("event_type") or ""),
        sequence=int(line.get("sequence") or 0),
    )


def build_bundle(
    *,
    request: BundleRequest,
    events: Iterable[Any],
    signer: Any,
    chain_report: dict | None = None,
) -> bytes:
    """Build the ZIP bytes for one export request."""
    lines: list[str] = []
    count = 0
    head_hash = ""
    first_sequence: int | None = None
    last_sequence: int | None = None
    for row in events:
        line = _event_line(row)
        lines.append(json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        count += 1
        head_hash = str(line.get("event_hash") or head_hash)
        first_sequence = line["sequence"] if first_sequence is None else first_sequence
        last_sequence = line["sequence"]

    events_bytes = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    chain_bytes = canonical_manifest(
        chain_report or {"verified": False, "reason": "chain_report_not_supplied"}
    )

    manifest = {
        "format": BUNDLE_FORMAT,
        "export_id": request.export_id or str(uuid.uuid4()),
        "tenant_id": str(request.tenant_id),
        "chain_scope": request.chain_scope or f"tenant:{request.tenant_id}",
        "requested_by": request.requested_by,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "range_start": _iso(request.range_start),
        "range_end": _iso(request.range_end),
        "event_count": count,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "head_hash": head_hash,
        "files": {
            EVENTS_MEMBER: hashlib.sha256(events_bytes).hexdigest(),
            CHAIN_REPORT_MEMBER: hashlib.sha256(chain_bytes).hexdigest(),
        },
        "signing": {
            "algorithm": "ed25519",
            "key_id": signer.key_id,
            "context": SIGNING_CONTEXT.decode("utf-8").strip(),
        },
    }
    manifest_bytes = canonical_manifest(manifest)
    signature = sign_bytes(signer, SIGNING_CONTEXT + manifest_bytes)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(EVENTS_MEMBER, events_bytes)
        archive.writestr(CHAIN_REPORT_MEMBER, chain_bytes)
        archive.writestr(MANIFEST_MEMBER, manifest_bytes)
        archive.writestr(SIGNATURE_MEMBER, canonical_manifest(signature))
    return buffer.getvalue()


def write_bundle(path: str | Path, data: bytes) -> dict[str, str]:
    """Persist bundle bytes; returns the path and SHA-256 of the file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": str(target), "sha256": hashlib.sha256(data).hexdigest()}


def verify_bundle_bytes(
    data: bytes,
    *,
    public_key_pem: str,
    verify_chain_links: bool = True,
) -> BundleVerification:
    """Verify a bundle without touching the database.

    Checks, in order: the ZIP is readable; every member the manifest names is
    present and hashes as expected; the manifest signature verifies under the
    supplied key; and — unless disabled — the exported lines still form a valid
    chain (each line hashes to its stored hash and links to its predecessor).
    """
    import base64

    from cryptography.exceptions import InvalidSignature

    from app.compliance_v2.audit_integrity.signing import public_key_from_pem

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if {MANIFEST_MEMBER, SIGNATURE_MEMBER, EVENTS_MEMBER} - names:
                return BundleVerification(ok=False, reason="bundle_incomplete")
            manifest_bytes = archive.read(MANIFEST_MEMBER)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            envelope = json.loads(archive.read(SIGNATURE_MEMBER).decode("utf-8"))
            events_bytes = archive.read(EVENTS_MEMBER)
            report_bytes = archive.read(CHAIN_REPORT_MEMBER) if CHAIN_REPORT_MEMBER in names else b""
    except (zipfile.BadZipFile, OSError, ValueError, KeyError):
        return BundleVerification(ok=False, reason="bundle_unreadable")

    files = manifest.get("files") or {}
    for member, expected in files.items():
        if member == EVENTS_MEMBER:
            actual = hashlib.sha256(events_bytes).hexdigest()
        elif member == CHAIN_REPORT_MEMBER:
            actual = hashlib.sha256(report_bytes).hexdigest()
        else:
            return BundleVerification(ok=False, reason=f"unknown_member:{member}")
        if actual != expected:
            return BundleVerification(ok=False, reason=f"member_hash_mismatch:{member}")

    try:
        key = public_key_from_pem(public_key_pem)
        raw_signature = base64.b64decode(str(envelope.get("signature") or ""), validate=True)
        key.verify(raw_signature, SIGNING_CONTEXT + manifest_bytes)
    except InvalidSignature:
        return BundleVerification(ok=False, reason="signature_invalid")
    except (ValueError, TypeError):
        return BundleVerification(ok=False, reason="signature_malformed")

    chain_ok = True
    chain_reason = ""
    if verify_chain_links:
        chain_ok, chain_reason = _verify_exported_chain(events_bytes)

    event_count = int(manifest.get("event_count") or 0)
    return BundleVerification(
        ok=chain_ok,
        reason="" if chain_ok else chain_reason,
        event_count=event_count,
        head_hash=str(manifest.get("head_hash") or ""),
        key_id=str(envelope.get("key_id") or ""),
        chain_ok=chain_ok,
        detail={
            "export_id": manifest.get("export_id", ""),
            "tenant_id": manifest.get("tenant_id", ""),
            "range_start": manifest.get("range_start", ""),
            "range_end": manifest.get("range_end", ""),
        },
    )


def _verify_exported_chain(events_bytes: bytes) -> tuple[bool, str]:
    """Re-derive the chain from exported lines. Returns ``(ok, reason)``."""
    previous_hash: str | None = None
    seen = 0
    for raw in events_bytes.decode("utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            return False, "events_member_malformed"
        seen += 1
        if (line.get("previous_hash") or None) != previous_hash:
            return False, f"broken_link_at:{line.get('sequence')}"
        if sha256_hex(line.get("payload")) != line.get("payload_hash"):
            return False, f"payload_hash_mismatch_at:{line.get('sequence')}"
        if rehash_line(line) != line.get("event_hash"):
            return False, f"event_hash_mismatch_at:{line.get('sequence')}"
        previous_hash = line.get("event_hash")
    if seen == 0:
        return False, "bundle_has_no_events"
    return True, ""


def verify_bundle(path: str | Path, *, public_key_pem: str) -> BundleVerification:
    """Verify a bundle file."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return BundleVerification(ok=False, reason="bundle_missing")
    return verify_bundle_bytes(data, public_key_pem=public_key_pem)


def read_bundle_manifest(path: str | Path) -> dict[str, Any]:
    """The manifest of a bundle, without verifying it (operator convenience)."""
    try:
        with zipfile.ZipFile(Path(path)) as archive:
            return json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


__all__ = [
    "BUNDLE_FORMAT",
    "CHAIN_REPORT_MEMBER",
    "EVENTS_MEMBER",
    "MANIFEST_MEMBER",
    "SIGNATURE_MEMBER",
    "SIGNING_CONTEXT",
    "BundleRequest",
    "BundleVerification",
    "build_bundle",
    "canonical_manifest",
    "read_bundle_manifest",
    "rehash_line",
    "verify_bundle",
    "verify_bundle_bytes",
    "write_bundle",
]
