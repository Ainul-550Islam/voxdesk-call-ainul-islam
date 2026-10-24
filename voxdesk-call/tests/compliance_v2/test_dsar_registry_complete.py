"""Incidents, breach clocks, access-review campaigns, DSAR and HIPAA safeguards.

These are the controls an auditor asks to see operating: an incident has a
timeline nobody can rewrite, a regulatory clock records when notice was given,
an access-review campaign can only be decided by its reviewers, a DSAR export is
signed and can be re-verified later, and break-glass access needs a second
person and expires on its own.
"""

from __future__ import annotations

import datetime as dt


from app.compliance_v2.dsar import registry as dsar_registry
from app.db.models import Base

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


def test_pii_registry_matches_the_orm_metadata():
    assert dsar_registry.check_registry(Base.metadata) == []
    document = dsar_registry.registry_document()
    assert document["subject_types"] == ["contact", "user"]
    assert {entry["table"] for entry in document["entries"]} >= {"contacts", "calls", "users"}


