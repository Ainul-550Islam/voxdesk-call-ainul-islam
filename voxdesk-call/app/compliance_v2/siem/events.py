"""Canonical SIEM event shape and the mapper from stored audit rows.

The audit table is the source of truth; a SIEM event is a *projection* of one
audit row, never a second record of what happened. Two properties matter:

* the projection is stable — ``SCHEMA_VERSION`` is part of every payload, so a
  receiver can evolve with us instead of guessing;
* the projection cannot leak a secret — the mapper copies an explicit list of
  fields and re-runs the existing audit redaction over ``detail``, so a value
  that was scrubbed before storage stays scrubbed on the way out, and one that
  was stored in clear (a phone number in a resource id, for example) is treated
  by the same policy as the audit API uses.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.audit.redaction import configured_secret_values, redact_text, redact_tree
from app.db.models import AuditLog

#: Bumped only when the payload shape changes in a way a receiver must notice.
SCHEMA_VERSION = "voxdesk.siem.audit.v1"

#: Fields copied from ``audit_logs``. Everything else stays behind.
_MAPPED_FIELDS = (
    "event_type",
    "action",
    "result",
    "actor_type",
    "resource_type",
    "request_id",
)


def _as_utc(value: dt.datetime | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None) -> str:
    return _as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class SiemEvent:
    """One audit fact, in the shape every sink serialises.

    ``event_id`` is the audit row id: a receiver that sees the same id twice has
    seen a redelivery, not a second event. That is the deduplication contract
    the at-least-once dispatcher relies on.
    """

    event_id: str
    schema_version: str
    tenant_id: str
    occurred_at: str
    event_type: str
    action: str
    result: str
    actor_type: str
    actor_id: str = ""
    actor_email: str = ""
    target_user_id: str = ""
    environment_id: str = ""
    resource_type: str = ""
    resource_id: str = ""
    request_id: str = ""
    ip_address: str = ""
    user_agent: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    chain_hash: str = ""
    dedupe_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The JSON-safe payload handed to a sink."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "occurred_at": self.occurred_at,
            "event_type": self.event_type,
            "action": self.action,
            "result": self.result,
            "actor": {
                "type": self.actor_type,
                "id": self.actor_id,
                "email": self.actor_email,
            },
            "target_user_id": self.target_user_id,
            "environment_id": self.environment_id,
            "resource": {"type": self.resource_type, "id": self.resource_id},
            "request_id": self.request_id,
            "source": {"ip_address": self.ip_address, "user_agent": self.user_agent},
            "detail": self.detail,
            "chain_hash": self.chain_hash,
        }

    def canonical_json(self) -> str:
        """Deterministic serialisation (used for syslog bodies and object hashes)."""
        return json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def dedupe(self) -> str:
        """The identity a sink uses to discard a redelivery."""
        return self.dedupe_key or self.event_id


def from_audit_log(row: AuditLog, *, chain_hash: str = "") -> SiemEvent:
    """Project one stored audit row into a :class:`SiemEvent`.

    The tenant is taken from the row (the audit writer already proved it); a
    system event with no tenant is projected with an empty tenant id and is
    still delivered, because "something happened with no tenant" is itself a
    fact an operator needs in the SIEM.
    """
    secrets = configured_secret_values()
    raw_detail = row.detail if isinstance(row.detail, dict) else {"value": row.detail}
    safe_detail = redact_tree(raw_detail, known_secrets=secrets, redact_pii=True)
    if not isinstance(safe_detail, dict):
        safe_detail = {"value": safe_detail}

    values = {name: getattr(row, name, None) for name in _MAPPED_FIELDS}
    return SiemEvent(
        event_id=str(row.id),
        schema_version=SCHEMA_VERSION,
        tenant_id=str(row.tenant_id) if row.tenant_id else "",
        occurred_at=_iso(row.created_at),
        event_type=str(values["event_type"] or ""),
        action=str(getattr(values["action"], "value", values["action"]) or ""),
        result=str(values["result"] or "success"),
        actor_type=str(values["actor_type"] or "system"),
        actor_id=str(row.actor_user_id) if row.actor_user_id else "",
        actor_email=redact_text(
            row.actor_email or "", known_secrets=secrets, redact_pii=False
        ),
        target_user_id=str(row.target_user_id) if row.target_user_id else "",
        environment_id=str(row.environment_id) if row.environment_id else "",
        resource_type=str(values["resource_type"] or ""),
        resource_id=redact_text(
            row.resource_id or "", known_secrets=secrets, redact_pii=True
        ),
        request_id=str(values["request_id"] or ""),
        ip_address=row.ip_address or "",
        user_agent=redact_text(
            row.user_agent or "", known_secrets=secrets, redact_pii=True
        ),
        detail=safe_detail,
        chain_hash=chain_hash,
        dedupe_key=str(row.id),
    )


def probe_event(*, tenant_id: str, actor: str) -> SiemEvent:
    """A synthetic event used to verify a destination end to end.

    It is marked as a probe in ``event_type`` and ``detail`` so a receiver can
    filter it out of real audit analysis, and it carries no customer data at
    all — only the ids needed to attribute the test to the operator who ran it.
    """
    moment = dt.datetime.now(dt.timezone.utc)
    return SiemEvent(
        event_id=str(uuid.uuid4()),
        schema_version=SCHEMA_VERSION,
        tenant_id=str(tenant_id),
        occurred_at=_iso(moment),
        event_type="siem_sink_probe",
        action="probe",
        result="success",
        actor_type="user",
        actor_id=str(actor),
        detail={"probe": True, "note": "connectivity test from the compliance console"},
        dedupe_key=str(uuid.uuid4()),
    )


def watermark_of(row: AuditLog) -> tuple[dt.datetime, uuid.UUID]:
    """The ``(created_at, id)`` pair the per-sink cursor advances to.

    Ordered as a tuple: audit rows written inside the same millisecond are
    ordered by id, which is stable and total, so a batch boundary can never
    skip or repeat a row.
    """
    return _as_utc(row.created_at), row.id


def is_after_watermark(
    moment: dt.datetime, row_id: uuid.UUID, *, watermark_at: dt.datetime | None, watermark_id: uuid.UUID | None
) -> bool:
    """True when ``(moment, row_id)`` sorts strictly after the stored watermark."""
    if watermark_at is None:
        return True
    left = _as_utc(moment)
    right = _as_utc(watermark_at)
    if left != right:
        return left > right
    if watermark_id is None:
        return True
    return str(row_id) > str(watermark_id)


__all__ = [
    "SCHEMA_VERSION",
    "SiemEvent",
    "from_audit_log",
    "probe_event",
    "is_after_watermark",
    "watermark_of",
]
