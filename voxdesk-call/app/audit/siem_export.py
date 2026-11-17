"""Audit export a SIEM can actually ingest (PART 16, P16-06).

Two things make an export trustworthy and both are easy to fake: the *shape* (a
stable envelope a Splunk/Sentinel parser can be pointed at without a custom
regex) and the *framing* (a signature the receiver can verify, plus a delivery
contract that survives a crash mid-batch).

Design decisions worth stating, because they are the ones a reviewer will ask:

* **Allowlist, not denylist.** A field appears in the export only if it is named
  in :data:`EXPORTED_FIELDS`. The audit ledger keeps growing columns; a new one
  must be added here deliberately before any customer sees it. Free-form
  ``detail`` is filtered key-by-key against :data:`DETAIL_DENYLIST` and every
  value is passed through the same redactor the ledger uses, so a token somebody
  put in a detail field does not travel to a third-party SIEM.
* **Keyset cursor, not offset.** ``(created_at, id)`` with a stable ascending
  sort. Offset pagination silently skips rows when new events land during an
  export; a keyset cursor cannot, and the same cursor replayed returns the same
  rows.
* **Signed frame, over the exact bytes.** HMAC-SHA256 over the body as sent, with
  a ``key_id`` so a receiver can hold two keys during rotation. The signature
  covers the header line too, so a batch cannot be reordered or split by an
  intermediary without the check failing.
* **Bounded batches.** A hard row cap keeps one call from pinning a worker; the
  caller loops on ``next_cursor``.

Nothing here claims the receiver stored the event. Delivery proof is the
receiver's acknowledgement, which is why :class:`ExportBatch` carries
``complete`` (there is more to read) rather than a boolean that sounds like
"delivered".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.redaction import configured_secret_values, redact_text, redact_tree
from app.db.models import AuditLog

#: Envelope version. A consumer pins it, so a breaking change is a new value.
SCHEMA_VERSION = "voxdesk.siem.v1"

#: Hard caps. A caller cannot ask for an unbounded page or an unbounded window.
MAX_ROWS = 500
DEFAULT_ROWS = 200
MAX_CLOCK_SKEW_SECONDS = 300

#: The only columns that leave the building. Everything else in the ledger stays.
EXPORTED_FIELDS: tuple[str, ...] = (
    "id",
    "occurred_at",
    "tenant_id",
    "environment_id",
    "action",
    "event_type",
    "actor_type",
    "actor_user_id",
    "actor_email",
    "target_user_id",
    "resource_type",
    "resource_id",
    "request_id",
    "result",
    "ip_address",
    "user_agent",
)

#: Keys that must never appear inside ``detail`` on the way out, matched
#: case-insensitively and by substring, because operators write ``apiKey``,
#: ``api_key`` and ``API-KEY`` and all three mean the same problem.
DETAIL_DENYLIST: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "cookie",
    "session_id",
    "private_key",
    "client_secret",
    "signature",
    "otp",
    "mfa_code",
    "card_number",
    "cvv",
    "ssn",
)


class ExportError(ValueError):
    """The request itself is malformed; nothing was read."""


class SignatureError(ExportError):
    """A frame did not verify. Never retried as if it were transient."""


# --------------------------------------------------------------- projection ---


def _denied(key: str) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in DETAIL_DENYLIST)


def clean_detail(detail: Any, *, secrets: Iterable[str] | None = None) -> dict:
    """Filter a ``detail`` blob for export: drop denied keys, redact the rest.

    Nested dictionaries are walked, so a secret cannot hide one level down. The
    result is a plain JSON-safe dict; values that are not JSON-safe are stringified
    rather than dropped, because a silently missing field is a lie about the event.
    """
    known = tuple(secrets or ())
    if not isinstance(detail, dict):
        return {}

    def walk(value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "[depth-limit]"
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if _denied(key):
                    out[str(key)] = "[redacted]"
                    continue
                out[str(key)] = walk(item, depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            return [walk(item, depth + 1) for item in value[:100]]
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, str):
                return redact_text(value, known_secrets=known, redact_pii=True)[:2000]
            return value
        return str(value)[:200]

    cleaned = walk(detail)
    # Belt and braces: the shared redactor also knows about configured secrets
    # this process can see but the request did not name.
    return redact_tree(cleaned, known_secrets=known, redact_pii=True)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def project_row(row: AuditLog, *, secrets: Iterable[str] | None = None) -> dict:
    """One ledger row as a SIEM record. Keys are exactly ``EXPORTED_FIELDS``+detail."""
    record: dict[str, Any] = {
        "id": str(row.id),
        "occurred_at": _iso(row.created_at),
        "tenant_id": str(row.tenant_id) if row.tenant_id else None,
        "environment_id": str(row.environment_id) if row.environment_id else None,
        "action": getattr(row.action, "value", row.action),
        "event_type": row.event_type,
        "actor_type": row.actor_type,
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "actor_email": redact_text(
            row.actor_email or "", known_secrets=tuple(secrets or ()), redact_pii=True
        )[:320],
        "target_user_id": str(row.target_user_id) if row.target_user_id else None,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "request_id": row.request_id,
        "result": row.result,
        "ip_address": row.ip_address,
        "user_agent": (row.user_agent or "")[:300],
    }
    # Assert the allowlist is real: a column added to the model but not to
    # EXPORTED_FIELDS must not appear here by accident.
    assert set(record) == set(EXPORTED_FIELDS), (
        "export projection drifted from EXPORTED_FIELDS: "
        f"{sorted(set(record) ^ set(EXPORTED_FIELDS))}"
    )
    record["detail"] = clean_detail(row.detail, secrets=secrets)
    return record


# ------------------------------------------------------------------- cursor ---


@dataclass(frozen=True)
class Cursor:
    """A resumable position in the ledger: ``(created_at, id)`` ascending."""

    occurred_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = f"{_iso(self.occurred_at)}|{self.id}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> "Cursor":
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode()).decode()
            stamp, _, ident = raw.partition("|")
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return cls(occurred_at=parsed, id=uuid.UUID(ident))
        except Exception as exc:  # noqa: BLE001 - any malformation is the same answer
            raise ExportError("cursor is not a token this export produced") from exc


# ------------------------------------------------------------------ signing ---


def sign(body: bytes, *, key: str, key_id: str, timestamp: int | None = None) -> dict:
    """Sign the exact bytes of a frame. Returns the header a receiver checks."""
    if not key:
        raise ExportError("a signing key is required")
    issued = int(time.time() if timestamp is None else timestamp)
    mac = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    return {
        "schema": SCHEMA_VERSION,
        "key_id": key_id,
        "timestamp": issued,
        "algorithm": "hmac-sha256",
        "signature": mac,
        "length": len(body),
    }


def verify(
    body: bytes,
    header: dict,
    *,
    keys: dict[str, str],
    now: int | None = None,
    tolerance: int = MAX_CLOCK_SKEW_SECONDS,
) -> bool:
    """Verify a frame. Fails closed on an unknown key id or a stale timestamp.

    ``compare_digest`` on the whole frame, so a partial body is a failure rather
    than a shorter valid signature.
    """
    key_id = str(header.get("key_id") or "")
    key = keys.get(key_id)
    if not key:
        return False
    if header.get("algorithm") != "hmac-sha256":
        return False
    try:
        issued = int(header.get("timestamp"))
    except (TypeError, ValueError):
        return False
    if abs((int(time.time()) if now is None else int(now)) - issued) > tolerance:
        return False
    expected = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(header.get("signature") or ""))


# -------------------------------------------------------------------- frame ---


@dataclass(frozen=True)
class ExportBatch:
    """One signed frame plus the cursor to continue from."""

    frame: bytes
    header: dict
    records: list[dict] = field(default_factory=list)
    next_cursor: str | None = None
    complete: bool = False  # False = more rows remain to be read
    generated_at: str | None = None

    def __len__(self) -> int:
        return len(self.records)

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "header": self.header,
            "next_cursor": self.next_cursor,
            "complete": self.complete,
            "count": len(self.records),
            "records": self.records,
        }


def frame_records(
    records: list[dict],
    *,
    key: str,
    key_id: str,
    tenant_id: uuid.UUID,
    batch_id: str | None = None,
    now: int | None = None,
) -> tuple[bytes, dict]:
    """Build the body for a batch and sign it.

    The body is newline-delimited JSON: one header line (metadata + its own
    digest-free copy of the identifiers), one line per record, and — deliberately
    — no trailing blank line, so the signature covers exactly what was sent.
    """
    issued = int(time.time() if now is None else now)
    head = {
        "schema": SCHEMA_VERSION,
        "batch_id": batch_id or uuid.uuid4().hex,
        "tenant_id": str(tenant_id),
        "generated_at": _iso(datetime.fromtimestamp(issued, tz=timezone.utc)),
        "count": len(records),
        "record_ids": [record.get("id") for record in records],
    }
    lines = [json.dumps(head, separators=(",", ":"), sort_keys=True)]
    lines.extend(json.dumps(record, separators=(",", ":"), sort_keys=True) for record in records)
    body = ("\n".join(lines) + "\n").encode()
    return body, sign(body, key=key, key_id=key_id, timestamp=issued)


# ------------------------------------------------------------------- export ---


def _coerce_action(row: AuditLog) -> str:
    return str(getattr(row.action, "value", row.action))


async def export_batch(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = DEFAULT_ROWS,
    key: str,
    key_id: str,
    environment_id: uuid.UUID | None = None,
    action_prefix: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    now: int | None = None,
) -> ExportBatch:
    """Read one page of a tenant's ledger into a signed frame.

    Filters are all optional and all tenant-scoped: the ``tenant_id`` predicate is
    unconditional, so no combination of arguments reaches another tenant's rows.
    """
    if limit < 1 or limit > MAX_ROWS:
        raise ExportError(f"limit must be between 1 and {MAX_ROWS}")
    conditions = [AuditLog.tenant_id == tenant_id]
    if environment_id is not None:
        conditions.append(AuditLog.environment_id == environment_id)
    if action_prefix:
        conditions.append(AuditLog.event_type.like(f"{action_prefix}%"))
    if since is not None:
        conditions.append(AuditLog.created_at >= since)
    if until is not None:
        conditions.append(AuditLog.created_at < until)
    if cursor:
        position = Cursor.decode(cursor)
        # Strictly after the cursor in (created_at, id) order. The compound
        # predicate is what keeps two events with the same timestamp from
        # collapsing into one.
        conditions.append(
            or_(
                AuditLog.created_at > position.occurred_at,
                and_(
                    AuditLog.created_at == position.occurred_at,
                    AuditLog.id > position.id,
                ),
            )
        )

    query = (
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .limit(limit + 1)  # one extra row tells us whether more remain
    )
    rows = list((await session.scalars(query)).all())
    more = len(rows) > limit
    page = rows[:limit]

    secrets = configured_secret_values()
    records = [project_row(row, secrets=secrets) for row in page]
    body, header = frame_records(
        records, key=key, key_id=key_id, tenant_id=tenant_id, now=now
    )
    next_cursor = None
    if page:
        last = page[-1]
        next_cursor = Cursor(occurred_at=last.created_at, id=last.id).encode()
    return ExportBatch(
        frame=body,
        header=header,
        records=records,
        next_cursor=next_cursor,
        complete=not more,
        generated_at=_iso(datetime.fromtimestamp(int(time.time() if now is None else now), tz=timezone.utc)),
    )


async def export_all(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key: str,
    key_id: str,
    limit: int = DEFAULT_ROWS,
    max_batches: int = 50,
    **filters: Any,
) -> list[ExportBatch]:
    """Page through a tenant's ledger, bounded by ``max_batches``.

    The bound exists so one caller cannot hold a worker for an unbounded read; the
    last batch's ``complete`` flag says whether the caller needs to come back.
    """
    batches: list[ExportBatch] = []
    cursor = filters.pop("cursor", None)
    for attempt in range(max_batches):
        batch = await export_batch(
            session, tenant_id=tenant_id, cursor=cursor, limit=limit, key=key, key_id=key_id, **filters
        )
        batches.append(batch)
        if batch.complete or not batch.next_cursor:
            break
        cursor = batch.next_cursor
        filters.pop("now", None)  # only the first call pins a timestamp in tests
        if attempt == max_batches - 1:
            break
    return batches
