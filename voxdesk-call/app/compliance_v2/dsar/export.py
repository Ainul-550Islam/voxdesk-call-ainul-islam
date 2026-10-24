"""Collection and packaging of subject data for DSAR exports.

The registry (``dsar.registry``) says *what* to collect; this module knows *how*
to reach it. Erasure (:mod:`app.compliance_v2.dsar.erase`) reuses these same
matchers, so an export and an erasure can never disagree about which rows belong
to a subject.

Design notes that matter:

* matching is tenant-scoped and uses indexed columns — phone variants and exact
  e-mail — never a table scan; the variants cover the ways the same number is
  stored (raw dialled string, E.164, digits with and without the country code);
* a match on ``Turn`` is reached through the calls that matched, because a turn
  has no subject key of its own;
* exports are capped per table and *say so* in the manifest when capped, rather
  than silently returning a truncated answer;
* PHI is redacted through ``hipaa.phi_tagging`` when the tenant is in HIPAA mode
  without a BAA, and filtered to the caller's minimum-necessary allowlist.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from typing import Any, Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.dsar.registry import (
    MATCHER_DIRECT,
    REGISTRY,
    SUBJECT_CONTACT,
    SUBJECT_USER,
    RegistryEntry,
    entry_for_table,
)
from app.compliance_v2.hipaa.minimum_necessary import (
    SURFACE_EXPORT,
    filter_records,
)
from app.compliance_v2.hipaa.phi_tagging import PhiPolicyView, redact_export_records
from app.db.models import Appointment, Call, Contact, Lead, Turn, User

#: Rows returned per table before the export declares itself capped.
EXPORT_ROW_LIMIT = 5000

#: Columns never exported, whatever the registry says: they are credentials or
#: internal bookkeeping, not subject data.
NEVER_EXPORT: frozenset[str] = frozenset(
    {"password_hash", "token_version", "failed_login_count", "locked_until"}
)

_NON_DIGITS = re.compile(r"[^0-9]")


class DsarError(RuntimeError):
    code = "dsar_error"


class SubjectNotFound(DsarError):
    code = "subject_not_found"


@dataclasses.dataclass(frozen=True)
class Subject:
    """The person a request is about."""

    subject_type: str
    key: str
    digit_key: str = ""

    @property
    def is_phone(self) -> bool:
        return self.subject_type == SUBJECT_CONTACT

    def phone_variants(self) -> tuple[str, ...]:
        if not self.digit_key:
            return ()
        tail = self.digit_key[-10:]
        return tuple({self.key, self.digit_key, f"+{self.digit_key}", tail})

    def as_dict(self) -> dict[str, str]:
        return {"subject_type": self.subject_type, "key": self.key, "digit_key": self.digit_key}


def digits_of(value: str) -> str:
    return _NON_DIGITS.sub("", value or "")


def build_subject(subject_type: str, subject_key: str) -> Subject:
    """Normalise and validate a subject identifier."""
    kind = (subject_type or SUBJECT_CONTACT).strip().lower()
    key = (subject_key or "").strip()
    if not key:
        raise DsarError("a subject key is required")
    if kind == SUBJECT_USER:
        if "@" not in key:
            raise DsarError("a user subject key must be an e-mail address")
        return Subject(subject_type=SUBJECT_USER, key=key.lower())
    if kind != SUBJECT_CONTACT:
        raise DsarError(f"unknown subject type {subject_type!r}")
    digits = digits_of(key)
    if len(digits) < 7:
        raise DsarError("a contact subject key must be a phone number")
    return Subject(subject_type=SUBJECT_CONTACT, key=key, digit_key=digits)


def _serialise(row: Any, columns: Sequence[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in columns:
        if column in NEVER_EXPORT:
            continue
        value = getattr(row, column, None)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        output[column] = value
    return output


# ------------------------------------------------------------------ matchers --


async def _contacts_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, subject: Subject, limit: int
) -> list[Contact]:
    variants = subject.phone_variants()
    rows = await session.execute(
        select(Contact)
        .where(
            Contact.tenant_id == tenant_id,
            or_(Contact.phone.in_(variants), Contact.phone_raw.in_(variants)),
        )
        .limit(limit)
    )
    return list(rows.scalars())


async def _leads_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, subject: Subject, limit: int
) -> list[Lead]:
    variants = subject.phone_variants()
    rows = await session.execute(
        select(Lead)
        .where(Lead.tenant_id == tenant_id, Lead.phone.in_(variants))
        .limit(limit)
    )
    return list(rows.scalars())


async def _calls_by_number(
    session: AsyncSession, tenant_id: uuid.UUID, subject: Subject, limit: int
) -> list[Call]:
    variants = subject.phone_variants()
    rows = await session.execute(
        select(Call)
        .where(
            Call.tenant_id == tenant_id,
            or_(Call.from_number.in_(variants), Call.to_number.in_(variants)),
        )
        .limit(limit)
    )
    return list(rows.scalars())


async def _turns_by_call(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    subject: Subject,
    limit: int,
    *,
    call_ids: Iterable[uuid.UUID] | None = None,
) -> list[Turn]:
    ids = list(call_ids if call_ids is not None else [])
    if not ids:
        ids = [row.id for row in await _calls_by_number(session, tenant_id, subject, limit)]
    if not ids:
        return []
    rows = await session.execute(
        select(Turn)
        .where(Turn.call_id.in_(ids))
        .order_by(Turn.created_at)
        .limit(limit)
    )
    return list(rows.scalars())


async def _appointments_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, subject: Subject, limit: int
) -> list[Appointment]:
    variants = subject.phone_variants()
    conditions = [Appointment.customer_phone.in_(variants)]
    if "@" in subject.key:
        conditions.append(Appointment.attendee_email == subject.key.lower())
    rows = await session.execute(
        select(Appointment)
        .where(Appointment.tenant_id == tenant_id, or_(*conditions))
        .limit(limit)
    )
    return list(rows.scalars())


async def _users_by_email(
    session: AsyncSession, tenant_id: uuid.UUID, subject: Subject, limit: int
) -> list[User]:
    rows = await session.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.email == subject.key.lower())
        .limit(limit)
    )
    return list(rows.scalars())


# --------------------------------------------------------------- collection --


@dataclasses.dataclass
class CollectedTable:
    entry: RegistryEntry
    rows: list[Any]
    capped: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)


def _model_for_table(table: str) -> type[Any]:
    """Resolve the ORM class for a table name, or explain why not.

    Erasure deletes and anonymises rows through the ORM, so a table without a
    mapped class cannot be erased — that is a registry bug, and it is raised as
    one rather than silently skipped.
    """
    from app.db.models import Base

    for cls in Base.registry.mappers:
        mapped = cls.class_
        if getattr(mapped, "__tablename__", None) == table:
            return mapped
    raise DsarError(f"table {table!r} has no mapped class, so it cannot be erased")


async def _by_direct_column(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    subject: Subject,
    entry: RegistryEntry,
    limit: int,
) -> list[Any]:
    """Rows of ``entry.table`` whose own columns hold the subject key.

    The identifying fields are OR-ed, so one entry covers a table that stores the
    number on both sides of a call. Matching is exact for e-mail and
    digits-normalised for phone numbers, the same rules the specialised matchers
    use.
    """
    model = _model_for_table(entry.table)
    columns = [getattr(model, field.column) for field in entry.fields if field.identifying]
    if not columns:
        raise DsarError(f"registry entry {entry.table} has no identifying column")
    if subject.subject_type == SUBJECT_USER:
        wanted = subject.key.strip().lower()
        conditions = [func.lower(column) == wanted for column in columns]
    else:
        digits = digits_of(subject.key)
        if not digits:
            return []
        # Digits-normalised comparison in SQL is not portable, so the candidate
        # rows are narrowed by the stored prefix and confirmed in Python.
        conditions = [column.like(f"%{digits[-7:]}%") for column in columns]
    rows = (
        await session.execute(
            select(model)
            .where(model.tenant_id == tenant_id, or_(*conditions))
            .limit(max(1, limit))
        )
    ).scalars().all()
    if subject.subject_type == SUBJECT_USER:
        return list(rows)
    kept = []
    for row in rows:
        for field in entry.fields:
            if not field.identifying:
                continue
            value = getattr(row, field.column, None)
            if isinstance(value, str) and digits_of(value) == digits:
                kept.append(row)
                break
    return kept


async def collect(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subject: Subject,
    limit: int = EXPORT_ROW_LIMIT,
) -> list[CollectedTable]:
    """Gather every row the registry attributes to the subject."""
    cap = max(1, limit)
    collected: list[CollectedTable] = []
    calls: list[Call] = []

    for entry in REGISTRY:
        if entry.subject_type != subject.subject_type:
            continue
        if entry.matcher == "contacts_by_phone":
            rows: list[Any] = await _contacts_by_phone(session, tenant_id, subject, cap)
        elif entry.matcher == "leads_by_phone":
            rows = await _leads_by_phone(session, tenant_id, subject, cap)
        elif entry.matcher == "calls_by_number":
            calls = await _calls_by_number(session, tenant_id, subject, cap)
            rows = calls
        elif entry.matcher == "turns_by_call":
            rows = await _turns_by_call(
                session, tenant_id, subject, cap, call_ids=[row.id for row in calls]
            )
        elif entry.matcher == "appointments_by_phone":
            rows = await _appointments_by_phone(session, tenant_id, subject, cap)
        elif entry.matcher == "users_by_email":
            rows = await _users_by_email(session, tenant_id, subject, cap)
        elif entry.matcher == MATCHER_DIRECT:
            rows = await _by_direct_column(session, tenant_id, subject, entry, cap)
        else:
            raise DsarError(f"registry entry {entry.table} has an unimplemented matcher")
        collected.append(CollectedTable(entry=entry, rows=rows, capped=len(rows) >= cap))
    return collected


def build_export(
    collected: Sequence[CollectedTable],
    *,
    subject: Subject,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID | None = None,
    policy: PhiPolicyView | None = None,
    role: Any = None,
    registry: dict | None = None,
) -> dict[str, Any]:
    """Turn collected rows into the export document handed to the subject.

    Redaction happens here, once, so no caller can assemble the document without
    passing through the PHI and minimum-necessary filters.
    """
    sections: list[dict[str, Any]] = []
    redacted_columns: set[str] = set()
    total_rows = 0

    for table in collected:
        records = [_serialise(row, table.entry.columns) for row in table.rows]
        if policy is not None:
            records, redacted = redact_export_records(
                records, table=table.entry.table, policy=policy
            )
            redacted_columns.update(redacted)
        if role is not None and policy is not None:
            records, removed_counts = filter_records(
                records,
                table=table.entry.table,
                role=role,
                surface=SURFACE_EXPORT,
                policy=policy,
            )
            redacted_columns.update(removed_counts)
        total_rows += len(records)
        sections.append(
            {
                "table": table.entry.table,
                "subject_type": table.entry.subject_type,
                "count": len(records),
                "capped": table.capped,
                "records": records,
            }
        )

    document: dict[str, Any] = {
        "format": "voxdesk.dsar.export.v1",
        "request_id": str(request_id) if request_id else "",
        "tenant_id": str(tenant_id),
        "subject": subject.as_dict(),
        "generated_at_note": "see bundle manifest for the generation timestamp",
        "sections": sections,
        "counts": {section["table"]: section["count"] for section in sections},
        "total_rows": total_rows,
        "redacted_columns": sorted(redacted_columns),
        "capped_tables": [section["table"] for section in sections if section["capped"]],
        "registry": registry or {},
    }
    return document


def canonical_json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()


def document_sha256(document: Any) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


def summarise(collected: Sequence[CollectedTable]) -> dict[str, int]:
    return {table.entry.table: table.count for table in collected}


def table_is_registered(table: str) -> bool:
    return entry_for_table(table) is not None


__all__ = [
    "EXPORT_ROW_LIMIT",
    "NEVER_EXPORT",
    "CollectedTable",
    "DsarError",
    "Subject",
    "SubjectNotFound",
    "build_export",
    "build_subject",
    "canonical_json",
    "collect",
    "digits_of",
    "document_sha256",
    "summarise",
    "table_is_registered",
]
