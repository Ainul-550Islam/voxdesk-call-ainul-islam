"""PHI tagging and propagation to exports and webhooks.

Which fields can carry protected health information is declared once, here, and
the rest of the product reads that declaration:

* ``PHI_FIELDS`` maps table → columns. The DSAR registry is required to cover
  every entry (``dsar.registry.check_registry`` enforces it), so a field cannot
  be PHI without also being part of an access/erasure request.
* A tenant has a :class:`PhiPolicyView`: HIPAA mode, whether a BAA is on file,
  and whether webhook payloads must be redacted. HIPAA mode off means these
  fields are ordinary personal data, and the export path is unchanged.
* Propagation: with HIPAA mode on and **no** BAA on file, PHI fields are
  redacted on the way out — exports, replays and webhook payloads — even for
  roles allowed to see them inside the product. A BAA is the instrument that
  makes an external disclosure lawful; without one there is no basis to send
  PHI to a third-party endpoint, so the code refuses.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: table -> columns that may contain PHI.
PHI_FIELDS: dict[str, frozenset[str]] = {
    "contacts": frozenset({"custom_fields", "name", "email"}),
    "calls": frozenset({"summary"}),
    "turns": frozenset({"text"}),
    "appointments": frozenset({"reason", "customer_name"}),
    "users": frozenset({"full_name"}),
}

#: Replacement written over a redacted value.
REDACTION = "[redacted:phi]"


@dataclasses.dataclass(frozen=True)
class PhiPolicyView:
    """The effective PHI policy for one tenant."""

    tenant_id: str
    hipaa_mode: bool = False
    baa_on_file: bool = False
    baa_reference: str = ""
    redact_webhooks: bool = True
    minimum_necessary: dict[str, Any] = dataclasses.field(default_factory=dict)
    source: str = "default"

    @property
    def external_disclosure_allowed(self) -> bool:
        """PHI may leave VoxDesk only under a BAA."""
        return bool(self.hipaa_mode and self.baa_on_file)

    @property
    def must_redact(self) -> bool:
        """HIPAA mode without a BAA redacts on every outbound path."""
        return bool(self.hipaa_mode and not self.baa_on_file)

    def as_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "hipaa_mode": self.hipaa_mode,
            "baa_on_file": self.baa_on_file,
            "baa_reference": self.baa_reference,
            "redact_webhooks": self.redact_webhooks,
            "external_disclosure_allowed": self.external_disclosure_allowed,
            "source": self.source,
        }


def phi_fields_for(table: str) -> frozenset[str]:
    return PHI_FIELDS.get(table, frozenset())


def is_phi_column(table: str, column: str) -> bool:
    return column in phi_fields_for(table)


async def load_policy(session: AsyncSession, tenant_id: uuid.UUID) -> PhiPolicyView:
    """Read the tenant's PHI policy, defaulting to "not in HIPAA mode"."""
    from app.db.compliance_v2_models import PhiPolicy

    row = await session.scalar(
        select(PhiPolicy).where(PhiPolicy.tenant_id == tenant_id)
    )
    if row is None:
        return PhiPolicyView(tenant_id=str(tenant_id), source="default")
    return PhiPolicyView(
        tenant_id=str(tenant_id),
        hipaa_mode=bool(row.hipaa_mode),
        baa_on_file=bool(row.baa_on_file),
        baa_reference=row.baa_reference or "",
        redact_webhooks=bool(row.redact_webhooks),
        minimum_necessary=dict(row.minimum_necessary or {}),
        source="tenant_row",
    )


async def set_policy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    hipaa_mode: bool,
    baa_on_file: bool,
    baa_reference: str,
    redact_webhooks: bool,
    minimum_necessary: dict | None,
    updated_by: uuid.UUID | None,
) -> "PhiPolicyView":
    """Create or update the tenant's PHI policy. Flushes."""
    from app.audit.service import record_event
    from app.db.compliance_v2_models import PhiPolicy

    if baa_on_file and not (baa_reference or "").strip():
        raise ValueError("recording a BAA requires its reference")
    row = await session.scalar(select(PhiPolicy).where(PhiPolicy.tenant_id == tenant_id))
    if row is None:
        row = PhiPolicy(tenant_id=tenant_id)
        session.add(row)
    row.hipaa_mode = bool(hipaa_mode)
    row.baa_on_file = bool(baa_on_file)
    row.baa_reference = (baa_reference or "")[:200]
    row.redact_webhooks = bool(redact_webhooks)
    row.minimum_necessary = dict(minimum_necessary or {})
    row.updated_by = updated_by
    await session.flush()

    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=updated_by,
        event_type="phi_policy_updated",
        resource_type="phi_policy",
        resource_id=tenant_id,
        detail={
            "hipaa_mode": row.hipaa_mode,
            "baa_on_file": row.baa_on_file,
            "redact_webhooks": row.redact_webhooks,
        },
    )
    return PhiPolicyView(
        tenant_id=str(tenant_id),
        hipaa_mode=row.hipaa_mode,
        baa_on_file=row.baa_on_file,
        baa_reference=row.baa_reference,
        redact_webhooks=row.redact_webhooks,
        minimum_necessary=dict(row.minimum_necessary or {}),
        source="tenant_row",
    )


def redact_record(
    record: dict[str, Any],
    *,
    table: str,
    policy: PhiPolicyView,
    force_when: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Redact tagged fields of one record. Returns ``(record, redacted_fields)``.

    ``force_when`` adds extra column names to redact regardless of policy (used
    by the minimum-necessary filter). The input mapping is never mutated.
    """
    if not isinstance(record, dict):
        return record, []
    targets = set(phi_fields_for(table)) | set(force_when or ())
    redacted: list[str] = []
    output = dict(record)
    if not policy.must_redact:
        return output, redacted
    for column in sorted(targets):
        if column not in output:
            continue
        value = output[column]
        if value in (None, "", {}, []):
            continue
        output[column] = REDACTION
        redacted.append(column)
    return output, redacted


def redact_export_records(
    records: Iterable[dict[str, Any]], *, table: str, policy: PhiPolicyView
) -> tuple[list[dict[str, Any]], list[str]]:
    """Redact a whole export payload for one table."""
    redacted_columns: set[str] = set()
    output: list[dict[str, Any]] = []
    for record in records:
        cleaned, redacted = redact_record(record, table=table, policy=policy)
        output.append(cleaned)
        redacted_columns.update(redacted)
    return output, sorted(redacted_columns)


def propagate_to_webhook(payload: dict[str, Any], *, policy: PhiPolicyView) -> dict[str, Any]:
    """Redact PHI keys from a webhook payload when the policy requires it.

    The scan is recursive over dictionaries and lists, and it matches tagged
    column names wherever they appear, because a webhook payload nests the
    record under ``data``/``call`` keys rather than exposing table columns at
    the top level.
    """
    if not isinstance(payload, dict) or not policy.must_redact or not policy.redact_webhooks:
        return payload
    tagged = {column for columns in PHI_FIELDS.values() for column in columns}

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: (REDACTION if key in tagged and node[key] not in (None, "", [], {}) else _walk(node[key]))
                for key in node
            }
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    cleaned = _walk(payload)
    return cleaned if isinstance(cleaned, dict) else payload


def tagged_columns_document() -> dict[str, list[str]]:
    """The tag table, for the evidence bundle and the CI check."""
    return {table: sorted(columns) for table, columns in sorted(PHI_FIELDS.items())}


__all__ = [
    "PHI_FIELDS",
    "REDACTION",
    "PhiPolicyView",
    "is_phi_column",
    "load_policy",
    "phi_fields_for",
    "propagate_to_webhook",
    "redact_export_records",
    "redact_record",
    "set_policy",
    "tagged_columns_document",
]
