"""Declarative registry of every table and column that holds personal data.

A subject access request is only as complete as this list. So the list is
*checked*, not trusted:

* every registered table and column must exist in ``Base.metadata`` — a typo or
  a renamed column fails the startup check instead of silently exporting less;
* every table on the curated inventory of personal-data holders must be
  registered, so a new table added to that inventory without a registry entry is
  a test failure rather than a compliance gap;
* columns the PHI module tags are required to be registered too, which keeps the
  HIPAA tagging and the DSAR registry from drifting apart.

``check_registry(metadata)`` returns a list of problems; the CI script
(``scripts/check_pii_registry.py``) exits non-zero when it is non-empty.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

from sqlalchemy import Table

SUBJECT_CONTACT = "contact"
SUBJECT_USER = "user"
SUBJECT_TYPES = (SUBJECT_CONTACT, SUBJECT_USER)

STRATEGY_ANONYMIZE = "anonymize"
STRATEGY_DELETE = "delete"
#: Kept, deliberately: a suppression record exists to *prevent* contact, so
#: deleting it would re-enable calls the person opted out of. Erasure reports it
#: as retained-by-obligation and the export still lists it.
STRATEGY_RETAIN = "retain"

#: Matcher used by every entry whose table holds the subject key in its own
#: columns. The identifying fields of the entry are OR-ed, so one entry can cover
#: a table with both a ``from_number`` and a ``to_number``.
MATCHER_DIRECT = "direct"


@dataclasses.dataclass(frozen=True)
class SubjectField:
    """One column holding personal data for a subject."""

    column: str
    #: Replacement used by erasure. ``None`` means the column is deleted with
    #: the row (a transcript turn) or replaced by the strategy's default.
    replacement: str = ""
    #: True when the value identifies the subject (used to find rows).
    identifying: bool = False


@dataclasses.dataclass(frozen=True)
class RegistryEntry:
    """One table that can hold data about one kind of subject."""

    name: str
    table: str
    subject_type: str
    fields: tuple[SubjectField, ...]
    strategy: str
    #: How a row is attributed to a subject. Built by :func:`entry_filter` from
    #: the model classes, so no SQL is written by hand here.
    matcher: str
    #: Human description of the join when the table has no direct subject key.
    join_note: str = ""

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(field.column for field in self.fields)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "table": self.table,
            "subject_type": self.subject_type,
            "strategy": self.strategy,
            "fields": [field.column for field in self.fields],
            "matcher": self.matcher,
        }


#: The registry. ``matcher`` values are resolved in :mod:`dsar.export`.
REGISTRY: tuple[RegistryEntry, ...] = (
    RegistryEntry(
        name="contacts",
        table="contacts",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_ANONYMIZE,
        matcher="contacts_by_phone",
        fields=(
            SubjectField("phone", replacement="+00000000000", identifying=True),
            SubjectField("phone_raw", replacement=""),
            SubjectField("name", replacement="Redacted contact"),
            SubjectField("email", replacement=""),
            SubjectField("company", replacement=""),
            SubjectField("custom_fields", replacement=""),
        ),
    ),
    RegistryEntry(
        name="leads",
        table="leads",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_ANONYMIZE,
        matcher="leads_by_phone",
        fields=(
            SubjectField("phone", replacement="+00000000000", identifying=True),
            SubjectField("name", replacement="Redacted lead"),
            SubjectField("email", replacement=""),
            SubjectField("company", replacement=""),
            SubjectField("notes", replacement=""),
        ),
    ),
    RegistryEntry(
        name="calls",
        table="calls",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_ANONYMIZE,
        matcher="calls_by_number",
        fields=(
            SubjectField("from_number", replacement="+00000000000", identifying=True),
            SubjectField("to_number", replacement="+00000000000", identifying=True),
            SubjectField("summary", replacement=""),
        ),
    ),
    RegistryEntry(
        name="turns",
        table="turns",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_DELETE,
        matcher="turns_by_call",
        join_note="turns belong to a call, and a call is matched by phone number",
        fields=(SubjectField("text", identifying=False),),
    ),
    RegistryEntry(
        name="appointments",
        table="appointments",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_ANONYMIZE,
        matcher="appointments_by_phone",
        fields=(
            SubjectField("customer_phone", replacement="+00000000000", identifying=True),
            SubjectField("customer_name", replacement="Redacted caller"),
            SubjectField("attendee_email", replacement=""),
            SubjectField("reason", replacement=""),
        ),
    ),
    RegistryEntry(
        name="users",
        table="users",
        subject_type=SUBJECT_USER,
        strategy=STRATEGY_ANONYMIZE,
        matcher="users_by_email",
        fields=(
            SubjectField("email", replacement="redacted@invalid", identifying=True),
            SubjectField("full_name", replacement="Redacted user"),
        ),
    ),
    RegistryEntry(
        name="user_emails",
        table="user_emails",
        subject_type=SUBJECT_USER,
        strategy=STRATEGY_DELETE,
        matcher=MATCHER_DIRECT,
        fields=(SubjectField("email", identifying=True),),
    ),
    RegistryEntry(
        name="user_email_verification_tokens",
        table="email_verification_tokens",
        subject_type=SUBJECT_USER,
        strategy=STRATEGY_DELETE,
        matcher=MATCHER_DIRECT,
        fields=(SubjectField("email", identifying=True),),
    ),
    RegistryEntry(
        name="membership_invitations",
        table="membership_invitations",
        subject_type=SUBJECT_USER,
        strategy=STRATEGY_DELETE,
        matcher=MATCHER_DIRECT,
        fields=(SubjectField("email", identifying=True),),
    ),
    RegistryEntry(
        name="telephony_call_sessions",
        table="telephony_call_sessions",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_ANONYMIZE,
        matcher=MATCHER_DIRECT,
        fields=(
            SubjectField("from_number", replacement="+00000000000", identifying=True),
            SubjectField("to_number", replacement="+00000000000", identifying=True),
            SubjectField("transfer_target", replacement=""),
            SubjectField("recording_reference", replacement=""),
            SubjectField("transcript_reference", replacement=""),
            SubjectField("transcript_turns", replacement=[]),
            SubjectField("dtmf_buffer", replacement=""),
            SubjectField("dtmf_events", replacement=[]),
            SubjectField("metadata_json", replacement={}),
        ),
    ),
    RegistryEntry(
        name="batch_recipients",
        table="batch_recipients",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_ANONYMIZE,
        matcher=MATCHER_DIRECT,
        fields=(
            SubjectField("phone", replacement="+00000000000", identifying=True),
            SubjectField("name", replacement="Redacted recipient"),
            SubjectField("custom_fields", replacement={}),
        ),
    ),
    RegistryEntry(
        name="dnc_entries",
        table="dnc_entries",
        subject_type=SUBJECT_CONTACT,
        strategy=STRATEGY_RETAIN,
        matcher=MATCHER_DIRECT,
        fields=(
            SubjectField("phone", replacement="+00000000000", identifying=True),
            SubjectField("reason", replacement=""),
        ),
        join_note="a do-not-call record is retained to honour the opt-out itself",
    ),
)

#: Tables that are known to hold personal data. A table on this list without a
#: registry entry is a compliance gap, so the check fails rather than waits for
#: an auditor to notice.
REQUIRED_TABLES: frozenset[str] = frozenset(
    {
        "appointments",
        "batch_recipients",
        "calls",
        "contacts",
        "dnc_entries",
        "email_verification_tokens",
        "leads",
        "membership_invitations",
        "telephony_call_sessions",
        "turns",
        "user_emails",
        "users",
    }
)

REGISTRY_BY_NAME = {entry.name: entry for entry in REGISTRY}
REGISTRY_BY_TABLE = {entry.table: entry for entry in REGISTRY}


def entries_for(subject_type: str) -> tuple[RegistryEntry, ...]:
    return tuple(entry for entry in REGISTRY if entry.subject_type == subject_type)


def entry_for_table(table: str) -> RegistryEntry | None:
    return REGISTRY_BY_TABLE.get(table)


def check_registry(metadata) -> list[str]:
    """Validate the registry against the live metadata. Returns problems."""
    problems: list[str] = []
    tables: dict[str, Table] = dict(metadata.tables)

    for entry in REGISTRY:
        table = tables.get(entry.table)
        if table is None:
            problems.append(f"registered table {entry.table!r} does not exist in metadata")
            continue
        for field in entry.fields:
            if field.column not in table.columns:
                problems.append(
                    f"{entry.table}.{field.column} is registered but the column does not exist"
                )
        if not any(field.identifying for field in entry.fields) and not entry.join_note:
            problems.append(f"{entry.table} has no identifying column, so it cannot be matched")
        if entry.strategy not in (STRATEGY_ANONYMIZE, STRATEGY_DELETE, STRATEGY_RETAIN):
            problems.append(f"{entry.table} has an unknown erasure strategy {entry.strategy!r}")
        if entry.strategy == STRATEGY_RETAIN and not entry.join_note:
            problems.append(
                f"{entry.table} is retained on erasure but does not say why "
                "(a retention without a stated obligation is a gap)"
            )

    registered_tables = {entry.table for entry in REGISTRY}
    for table in sorted(REQUIRED_TABLES - registered_tables):
        problems.append(f"table {table!r} holds personal data but is not registered")

    from app.compliance_v2.hipaa.phi_tagging import PHI_FIELDS

    for table, columns in PHI_FIELDS.items():
        entry = REGISTRY_BY_TABLE.get(table)
        if entry is None:
            problems.append(f"PHI-tagged table {table!r} is not in the DSAR registry")
            continue
        missing = sorted(set(columns) - set(entry.columns))
        if missing:
            problems.append(
                f"PHI-tagged column(s) {', '.join(missing)} in {table} are not registered"
            )
    return problems


def assert_registry(metadata) -> None:
    """Raise when the registry has drifted from the models."""
    problems = check_registry(metadata)
    if problems:
        raise RegistryDrift("; ".join(problems))


class RegistryDrift(RuntimeError):
    """The registry no longer matches the models."""


def registry_document() -> dict:
    """Machine-readable registry, included in a DSAR export manifest."""
    return {
        "subject_types": list(SUBJECT_TYPES),
        "entries": [entry.as_dict() for entry in REGISTRY],
    }


MatcherResolver = Callable[[str], Callable]
__all__ = [
    "REGISTRY",
    "REGISTRY_BY_NAME",
    "REGISTRY_BY_TABLE",
    "REQUIRED_TABLES",
    "STRATEGY_ANONYMIZE",
    "STRATEGY_DELETE",
    "SUBJECT_CONTACT",
    "SUBJECT_TYPES",
    "SUBJECT_USER",
    "MatcherResolver",
    "RegistryDrift",
    "RegistryEntry",
    "SubjectField",
    "assert_registry",
    "check_registry",
    "entries_for",
    "entry_for_table",
    "registry_document",
]
