"""Contact attribute sync across channels (PART 14, P14-06).

The same person arrives by voice, by SMS, by widget chat and from a CRM dump.
Each channel knows different things and each is occasionally wrong. This module
is the one place that decides what a contact row ends up saying.

Three decisions, made explicitly:

1. **Only known fields are written.** A field map, not `dict.update`: a call
   analysis cannot inject arbitrary keys into a contact.
2. **Conflicts are resolved by policy, and the loser is recorded.** The default
   policy is `fill_empty` -- a call fills in a missing company, but never
   overwrites a value a human typed. When a policy does allow overwriting, the
   previous value is returned so the caller can log it.
3. **Every value carries provenance.** `custom_fields["_sync"]` records which
   channel and which call set each value, so "where did this come from?" has an
   answer that is not "someone probably typed it".

Phone numbers are normalised before they are compared, because otherwise
`+8801712345678` and `01712345678` look like two different people.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

POLICIES = ("fill_empty", "overwrite", "keep_existing")
PROVENANCE_KEY = "_sync"

#: Canonical contact field -> accepted input keys, in priority order.
FIELD_MAP: dict[str, tuple[str, ...]] = {
    "name": ("name", "full_name", "contact_name", "caller_name"),
    "email": ("email", "email_address", "contact_email"),
    "company": ("company", "company_name", "organisation", "organization", "account"),
    "phone": ("phone", "phone_number", "from_number", "caller_number"),
    "job_title": ("job_title", "title", "role"),
    "city": ("city", "town"),
    "country": ("country", "country_code"),
    "language": ("language", "locale", "preferred_language"),
    "intent": ("intent", "last_intent"),
    "budget": ("budget", "budget_range"),
}

#: Free-text analysis fields are never copied into a contact: summaries and
#: transcripts are call records, not attributes.
NOT_SYNCED = ("summary", "transcript", "sentiment", "notes", "recording_url")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_DIGITS = re.compile(r"\D+")


class SyncError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_phone(value: str | None) -> str | None:
    """Best-effort E.164. Returns None when the input cannot be a phone number.

    No country list is consulted here: contact sync must not depend on a
    library's metadata version to decide two rows are the same person. Numbers
    that already start with `+` are kept; anything else is digit-normalised and
    returned as-is (the tenant's default country is applied upstream).
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    plus = raw.startswith("+")
    digits = _DIGITS.sub("", raw)
    if len(digits) < 7 or len(digits) > 15:
        return None
    return f"+{digits}" if plus else digits


def pick(incoming: Mapping[str, Any], canonical: str) -> Any:
    for key in FIELD_MAP[canonical]:
        if key in incoming and incoming[key] not in (None, ""):
            return incoming[key]
    return None


def coerce(canonical: str, value: Any) -> Any:
    if value is None:
        return None
    if canonical == "phone":
        return normalize_phone(value)
    if canonical == "email":
        text = str(value).strip()
        return text if _EMAIL.match(text) else None
    return str(value).strip()[:200]


@dataclass(frozen=True)
class MergeResult:
    values: dict[str, Any]
    applied: dict[str, str] = field(default_factory=dict)  # canonical -> source
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "applied": dict(self.applied),
            "conflicts": list(self.conflicts),
            "rejected": list(self.rejected),
        }


def merge(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    *,
    policy: str = "fill_empty",
    source: str = "unknown",
) -> MergeResult:
    """Merge one channel's view of a contact into the stored values."""
    if policy not in POLICIES:
        raise SyncError("invalid_policy", f"policy must be one of {', '.join(POLICIES)}")

    values = dict(existing)
    applied: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for canonical in FIELD_MAP:
        raw = pick(incoming, canonical)
        if raw is None:
            continue
        value = coerce(canonical, raw)
        if value is None:
            rejected.append({"field": canonical, "reason": "invalid_value", "source": source})
            continue
        current = values.get(canonical)
        if current in (None, ""):
            values[canonical] = value
            applied[canonical] = source
            continue
        if str(current) == str(value):
            continue
        if policy == "keep_existing":
            conflicts.append(
                {"field": canonical, "kept": current, "offered": value, "source": source}
            )
            continue
        if policy == "fill_empty":
            conflicts.append(
                {
                    "field": canonical,
                    "kept": current,
                    "offered": value,
                    "source": source,
                    "reason": "existing value not empty under fill_empty policy",
                }
            )
            continue
        # overwrite
        values[canonical] = value
        applied[canonical] = source
        conflicts.append(
            {"field": canonical, "kept": value, "replaced": current, "source": source}
        )

    for blocked in NOT_SYNCED:
        if blocked in incoming:
            rejected.append({"field": blocked, "reason": "not_a_contact_attribute", "source": source})
    unknown = sorted(set(incoming) - {key for keys in FIELD_MAP.values() for key in keys} - set(NOT_SYNCED))
    for key in unknown:
        rejected.append({"field": key, "reason": "unmapped_field", "source": source})

    return MergeResult(values=values, applied=applied, conflicts=conflicts, rejected=rejected)


def derive_attributes(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a call analysis payload into contact-ish attributes.

    `extracted` is what the analysis model was asked to return (intent, budget,
    language, ...). It is filtered through the field map like any other source;
    a model that hallucinates `favourite_colour` cannot write it.
    """
    extracted = analysis.get("extracted") or analysis.get("attributes") or {}
    if not isinstance(extracted, Mapping):
        return {}
    derived: dict[str, Any] = {}
    for canonical in FIELD_MAP:
        value = pick(extracted, canonical)
        if value not in (None, ""):
            coerced = coerce(canonical, value)
            if coerced is not None:
                derived[canonical] = coerced
    if analysis.get("caller_number") and "phone" not in derived:
        phone = normalize_phone(analysis.get("caller_number"))
        if phone:
            derived["phone"] = phone
    return derived


def provenance_view(custom_fields: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict((custom_fields or {}).get(PROVENANCE_KEY) or {})


async def sync_contact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact: Any,
    incoming: Mapping[str, Any],
    source: str,
    policy: str = "fill_empty",
) -> MergeResult:
    """Merge `incoming` into a contact row and record provenance per field."""
    current = {
        key: getattr(contact, key, None)
        for key in ("name", "email", "company", "phone", "job_title", "city", "country")
    }
    current.update({k: v for k, v in (contact.custom_fields or {}).items() if not k.startswith("_")})
    result = merge(current, incoming, policy=policy, source=source)

    from app.db.retell_models import Contact  # local import: keeps this module import-light

    for canonical, value in result.values.items():
        if hasattr(contact, canonical) and canonical != "custom_fields":
            setattr(contact, canonical, value)
        else:
            fields = dict(contact.custom_fields or {})
            fields[canonical] = value
            contact.custom_fields = fields

    fields = dict(contact.custom_fields or {})
    provenance = dict(fields.get(PROVENANCE_KEY) or {})
    stamp = _now().isoformat()
    for canonical, src in result.applied.items():
        provenance[canonical] = {"source": src, "at": stamp}
    fields[PROVENANCE_KEY] = provenance
    contact.custom_fields = fields
    await session.flush()
    return result


async def backfill_attributes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    analyses: Iterable[Mapping[str, Any]],
    policy: str = "fill_empty",
    limit: int = 200,
) -> dict[str, Any]:
    """Apply historical analyses to contacts. Dry-run friendly.

    `analyses` are supplied by the caller (the post-call analysis table) rather
    than queried here, so this function is pure enough to test with fixtures and
    a dry run can be produced without touching a row.
    """
    from app.db.retell_models import Contact

    considered = 0
    updated = 0
    skipped: list[dict[str, Any]] = []
    for analysis in list(analyses)[:limit]:
        considered += 1
        phone = normalize_phone(analysis.get("caller_number"))
        if not phone:
            skipped.append({"reason": "no_caller_number"})
            continue
        contact = (
            await session.execute(
                select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == phone)
            )
        ).scalar_one_or_none()
        if contact is None:
            skipped.append({"reason": "no_contact", "phone": phone})
            continue
        derived = derive_attributes(analysis)
        if not derived:
            skipped.append({"reason": "nothing_derivable", "phone": phone})
            continue
        result = await sync_contact(
            session,
            tenant_id=tenant_id,
            contact=contact,
            incoming=derived,
            source=f"call:{analysis.get('call_id', 'unknown')}",
            policy=policy,
        )
        if result.changed:
            updated += 1
    return {"considered": considered, "updated": updated, "skipped": skipped[:50]}
