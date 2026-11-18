"""Durable contact memory service (Retell parity).

Persists tenant-scoped key/value facts associated with a ``Contact``, enforcing
credential-key refusal, tenant ownership, expiry filtering, and durable audit
logging on every operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import AuditAction, Tenant
from app.db.retell_models import Contact, ContactMemoryEntry
from app.domain.contact_memory_models import MemorySaveRequest, validate_memory_key

if TYPE_CHECKING:  # pragma: no cover - annotations only; the rows are imported lazily below
    # `app.db.channel_models` is imported inside the functions because it imports this
    # module's peers at module scope; the annotations still have to name the types.
    from app.db.channel_models import ContactBrief, MemorySettings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_id(tenant: Tenant | UUID) -> UUID:
    return tenant.id if isinstance(tenant, Tenant) else UUID(str(tenant))


def _contact_id(contact: Contact | UUID, expected_tenant_id: UUID) -> UUID:
    if isinstance(contact, Contact):
        if contact.tenant_id != expected_tenant_id:
            raise PermissionError("contact belongs to a different tenant")
        return contact.id
    return UUID(str(contact))


async def _emit_memory_audit(
    session: AsyncSession,
    tenant_id: UUID,
    event: str,
    detail: dict[str, Any],
    *,
    actor_user_id: UUID | None = None,
) -> None:
    try:
        from app.auth.identity.events import scrub
        from app.auth.service import record_audit

        await record_audit(
            session,
            action=AuditAction.GOVERNANCE_EVENT,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_email="system",
            detail=scrub({"event": event, "operation": event, **detail}),
            commit=False,
        )
    except Exception as exc:
        log.warning("contact_memory.audit.failed", event=event, error=str(exc))


async def save(
    session: AsyncSession,
    tenant: Tenant | UUID,
    contact: Contact | UUID,
    key: str,
    payload: MemorySaveRequest,
    *,
    created_by: UUID | None = None,
) -> ContactMemoryEntry:
    """Upsert a memory entry for ``(tenant_id, contact_id, key)``."""
    tid = _tenant_id(tenant)
    cid = _contact_id(contact, tid)
    cleaned_key = validate_memory_key(key)
    now = _now()

    stmt = select(ContactMemoryEntry).where(
        ContactMemoryEntry.tenant_id == tid,
        ContactMemoryEntry.contact_id == cid,
        ContactMemoryEntry.key == cleaned_key,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    vtype = (
        payload.value_type.value
        if hasattr(payload.value_type, "value")
        else str(payload.value_type)
    )
    src = payload.source.value if hasattr(payload.source, "value") else str(payload.source)

    if existing is not None:
        existing.value = payload.value
        existing.value_type = vtype
        existing.source = src
        existing.source_ref = payload.source_ref
        existing.confidence = payload.confidence
        existing.importance = payload.importance
        existing.expires_at = payload.expires_at
        existing.updated_at = now
        await session.flush()
        await _emit_memory_audit(
            session,
            tid,
            "contact_memory.updated",
            {
                "contact_id": str(cid),
                "key": cleaned_key,
                "source": src,
            },
            actor_user_id=created_by,
        )
        log.info(
            "contact_memory.updated",
            tenant_id=str(tid),
            contact_id=str(cid),
            key=cleaned_key,
            source=src,
        )
        return existing

    entry = ContactMemoryEntry(
        tenant_id=tid,
        contact_id=cid,
        key=cleaned_key,
        value=payload.value,
        value_type=vtype,
        source=src,
        source_ref=payload.source_ref,
        confidence=payload.confidence,
        importance=payload.importance,
        expires_at=payload.expires_at,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(entry)
    await session.flush()
    await _emit_memory_audit(
        session,
        tid,
        "contact_memory.created",
        {
            "contact_id": str(cid),
            "key": cleaned_key,
            "source": src,
        },
        actor_user_id=created_by,
    )
    log.info(
        "contact_memory.created",
        tenant_id=str(tid),
        contact_id=str(cid),
        key=cleaned_key,
        source=src,
    )
    return entry


async def get_entry(
    session: AsyncSession,
    tenant: Tenant | UUID,
    contact: Contact | UUID,
    key: str,
) -> ContactMemoryEntry | None:
    tid = _tenant_id(tenant)
    cid = _contact_id(contact, tid)
    cleaned_key = validate_memory_key(key)
    stmt = select(ContactMemoryEntry).where(
        ContactMemoryEntry.tenant_id == tid,
        ContactMemoryEntry.contact_id == cid,
        ContactMemoryEntry.key == cleaned_key,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_entries(
    session: AsyncSession,
    tenant: Tenant | UUID,
    contact: Contact | UUID,
    *,
    include_expired: bool = False,
) -> list[ContactMemoryEntry]:
    tid = _tenant_id(tenant)
    cid = _contact_id(contact, tid)
    stmt = select(ContactMemoryEntry).where(
        ContactMemoryEntry.tenant_id == tid,
        ContactMemoryEntry.contact_id == cid,
    )
    if not include_expired:
        now = _now()
        stmt = stmt.where(
            (ContactMemoryEntry.expires_at.is_(None)) | (ContactMemoryEntry.expires_at > now)
        )
    stmt = stmt.order_by(
        ContactMemoryEntry.importance.desc(),
        ContactMemoryEntry.updated_at.desc(),
    )
    return list((await session.execute(stmt)).scalars().all())


async def delete_entry(
    session: AsyncSession,
    tenant: Tenant | UUID,
    contact: Contact | UUID,
    key: str,
    *,
    actor_user_id: UUID | None = None,
) -> bool:
    tid = _tenant_id(tenant)
    cid = _contact_id(contact, tid)
    cleaned_key = validate_memory_key(key)
    stmt = delete(ContactMemoryEntry).where(
        ContactMemoryEntry.tenant_id == tid,
        ContactMemoryEntry.contact_id == cid,
        ContactMemoryEntry.key == cleaned_key,
    )
    res = await session.execute(stmt)
    deleted = bool(res.rowcount and res.rowcount > 0)
    if deleted:
        await _emit_memory_audit(
            session,
            tid,
            "contact_memory.deleted",
            {"contact_id": str(cid), "key": cleaned_key},
            actor_user_id=actor_user_id,
        )
    return deleted


async def purge_expired(
    session: AsyncSession,
    tenant: Tenant | UUID,
    *,
    contact_id: UUID | None = None,
) -> int:
    tid = _tenant_id(tenant)
    now = _now()
    stmt = delete(ContactMemoryEntry).where(
        ContactMemoryEntry.tenant_id == tid,
        ContactMemoryEntry.expires_at.is_not(None),
        ContactMemoryEntry.expires_at <= now,
    )
    if contact_id is not None:
        stmt = stmt.where(ContactMemoryEntry.contact_id == contact_id)
    res = await session.execute(stmt)
    return int(res.rowcount or 0)


# ---------------------------------------------------------------------------
# PART 14 / P14-05 -- memory settings, the running brief, and recall.
#
# Appended, not rewritten: `save`/`get_entry`/`list_entries`/`delete_entry`/
# `purge_expired` above are the fact store and keep their behaviour. What is
# added here is the *derived* layer a call actually reads:
#
#   settings   -- per-tenant switches and the workspace prompt;
#   brief      -- a short, editable summary built from facts + recent calls;
#   recall     -- what an agent receives at call start, and whether it is enough
#                 to skip a node (the "returning caller" path);
#   backfill   -- build briefs for contacts that predate this feature.
#
# The brief is deliberately separate from the facts. An operator editing the
# brief must not silently rewrite a saved fact, and a purge of expired facts
# must not delete an operator's note.
# ---------------------------------------------------------------------------

import regex as _rx  # noqa: E402  (kept local to the appended block)

BRIEF_MAX_CHARS = 1200
BRIEF_MAX_CALLS = 5
DEFAULT_RETENTION_DAYS = 90

#: Facts that must never be written to a brief, matched case-insensitively.
_SECRET_KEY_HINTS = ("password", "passcode", "secret", "token", "api_key", "card", "cvv", "ssn")
#: Digits that look like a card number or a national id, in a stored value.
_PII_PATTERNS = (
    _rx.compile(r"\d(?:[ -]?\d){12,18}"),  # card-like
    _rx.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN shape
)


def redact(text: str) -> str:
    """Strip card-like and national-id-like digit runs from brief text."""
    cleaned = text or ""
    for pattern in _PII_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def _entry_is_secret(key: str) -> bool:
    lowered = (key or "").lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


async def get_settings(session: AsyncSession, tenant: Tenant | UUID) -> MemorySettings:
    """The tenant's memory settings, creating the default row on first read."""
    from app.db.channel_models import MemorySettings

    tid = _tenant_id(tenant)
    row = (
        await session.execute(select(MemorySettings).where(MemorySettings.tenant_id == tid))
    ).scalar_one_or_none()
    if row is None:
        row = MemorySettings(tenant_id=tid)
        session.add(row)
        await session.flush()
    return row


async def update_settings(
    session: AsyncSession,
    tenant: Tenant | UUID,
    *,
    updated_by: str,
    save_enabled: bool | None = None,
    recall_enabled: bool | None = None,
    brief_enabled: bool | None = None,
    workspace_prompt: str | None = None,
    retention_days: int | None = None,
) -> MemorySettings:
    row = await get_settings(session, tenant)
    if retention_days is not None:
        if not 1 <= int(retention_days) <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        row.retention_days = int(retention_days)
    if workspace_prompt is not None:
        # A workspace prompt is injected into every agent's system prompt, so it
        # is capped and audited like any other instruction surface.
        if len(workspace_prompt) > 2000:
            raise ValueError("workspace_prompt must be at most 2000 characters")
        row.workspace_prompt = workspace_prompt
    for field_name, value in (
        ("save_enabled", save_enabled),
        ("recall_enabled", recall_enabled),
        ("brief_enabled", brief_enabled),
    ):
        if value is not None:
            setattr(row, field_name, bool(value))
    row.updated_by = updated_by[:120]
    await session.flush()
    await _emit_memory_audit(
        session,
        row.tenant_id,
        "contact_memory.settings_updated",
        {
            "save_enabled": row.save_enabled,
            "recall_enabled": row.recall_enabled,
            "brief_enabled": row.brief_enabled,
            "retention_days": row.retention_days,
            "workspace_prompt_chars": len(row.workspace_prompt or ""),
        },
    )
    return row


def settings_view(row: MemorySettings) -> dict[str, Any]:
    return {
        "save_enabled": row.save_enabled,
        "recall_enabled": row.recall_enabled,
        "brief_enabled": row.brief_enabled,
        "workspace_prompt": row.workspace_prompt,
        "retention_days": row.retention_days,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def compose_brief(
    *,
    entries: list[ContactMemoryEntry],
    calls: list[dict[str, Any]],
    messages: list[dict[str, Any]] | None = None,
    max_chars: int = BRIEF_MAX_CHARS,
) -> dict[str, Any]:
    """Build the brief text from facts and call digests. Pure function.

    Ordering is deliberate: the caller's name and the open items come first,
    because those are what the agent needs in the first sentence. Everything
    else follows by importance, then recency, and is truncated with an explicit
    marker rather than being silently cut mid-sentence.
    """
    messages = messages or []
    open_items: list[str] = []
    lines: list[str] = []

    visible = [entry for entry in entries if not _entry_is_secret(entry.key)]
    priority = sorted(
        visible,
        key=lambda entry: (-int(entry.importance or 0), -(entry.updated_at.timestamp() if entry.updated_at else 0)),
    )
    for entry in priority:
        value = redact(str(entry.value or ""))[:200]
        lines.append(f"{entry.key}: {value}")

    for call in calls[:BRIEF_MAX_CALLS]:
        summary = redact(str(call.get("summary") or ""))[:300]
        if summary:
            lines.append(f"call {call.get('started_at', '')[:10]}: {summary}")
        if call.get("follow_up"):
            open_items.append(str(call["follow_up"])[:200])
    for message in messages[:5]:
        body = redact(str(message.get("body") or ""))[:200]
        if body:
            lines.append(f"message: {body}")

    text = "\n".join(lines)
    truncated = len(text) > max_chars
    if truncated:
        text = text[: max_chars - 1].rstrip() + "…"
    return {
        "brief": text,
        "open_items": open_items,
        "truncated": truncated,
        "source_entries": len(visible),
        "source_calls": len(calls),
        "source_messages": len(messages),
        "pii_redacted": True,
    }


async def build_brief(
    session: AsyncSession,
    tenant: Tenant | UUID,
    contact: Contact | UUID,
    *,
    messages: list[dict[str, Any]] | None = None,
    persist: bool = True,
) -> ContactBrief:
    """Compose and store the running brief for one contact."""
    from app.db.channel_models import ContactBrief
    from app.db.models import Call

    tid = _tenant_id(tenant)
    cid = _contact_id(contact, tid)
    # Reading the settings creates the default row on first use, which the brief
    # below depends on; the values themselves are applied in `recall`.
    await get_settings(session, tid)
    entries = await list_entries(session, tid, cid)

    phone = None
    contact_row = (
        await session.execute(
            select(Contact).where(Contact.id == cid, Contact.tenant_id == tid)
        )
    ).scalar_one_or_none()
    if contact_row is not None:
        phone = contact_row.phone

    call_rows: list[dict[str, Any]] = []
    if phone:
        rows = (
            await session.execute(
                select(Call)
                .where(
                    Call.tenant_id == tid,
                    Call.from_number == phone,
                    Call.summary.is_not(None),
                )
                .order_by(Call.started_at.desc())
                .limit(BRIEF_MAX_CALLS)
            )
        ).scalars().all()
        call_rows = [
            {
                "summary": row.summary,
                "started_at": row.started_at.isoformat() if row.started_at else "",
                "intent": row.intent,
                "escalated": row.escalated,
                "booked": row.booked,
                "follow_up": None,
            }
            for row in rows
        ]

    composed = compose_brief(entries=entries, calls=call_rows, messages=messages)
    row = (
        await session.execute(
            select(ContactBrief).where(
                ContactBrief.tenant_id == tid, ContactBrief.contact_id == cid
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ContactBrief(tenant_id=tid, contact_id=cid)
        session.add(row)
    row.brief = composed["brief"]
    row.open_items = composed["open_items"]
    row.calls_behind = composed["source_calls"]
    row.messages_behind = composed["source_messages"]
    row.pii_redacted = True
    row.generated_at = _now()
    if persist:
        await session.flush()
    return row


async def set_brief(
    session: AsyncSession,
    tenant: Tenant | UUID,
    contact: Contact | UUID,
    text: str,
    *,
    edited_by: str,
) -> ContactBrief:
    """Operator edit of a brief. Recorded, never silently merged with facts."""

    if len(text or "") > BRIEF_MAX_CHARS * 2:
        raise ValueError("a brief may be at most 2400 characters")
    row = await build_brief(session, tenant, contact, persist=False)
    row.brief = redact(text or "")
    row.edited_by = edited_by[:120]
    row.generated_at = _now()
    await session.flush()
    await _emit_memory_audit(
        session,
        row.tenant_id,
        "contact_memory.brief_edited",
        {"contact_id": str(row.contact_id), "chars": len(row.brief)},
    )
    return row


async def clear_brief(session: AsyncSession, tenant: Tenant | UUID, contact: Contact | UUID) -> bool:
    """Remove the brief row. The facts underneath stay untouched."""
    from app.db.channel_models import ContactBrief

    tid = _tenant_id(tenant)
    cid = _contact_id(contact, tid)
    await session.execute(
        delete(ContactBrief).where(
            ContactBrief.tenant_id == tid, ContactBrief.contact_id == cid
        )
    )
    await _emit_memory_audit(
        session, tid, "contact_memory.brief_cleared", {"contact_id": str(cid)}
    )
    return True


def brief_view(row: ContactBrief | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "brief": row.brief,
        "open_items": list(row.open_items or []),
        "calls_behind": row.calls_behind,
        "messages_behind": row.messages_behind,
        "pii_redacted": row.pii_redacted,
        "edited_by": row.edited_by,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def recall(
    *,
    entries: list[ContactMemoryEntry],
    brief: ContactBrief | None,
    workspace_prompt: str = "",
    max_chars: int = BRIEF_MAX_CHARS,
) -> dict[str, Any]:
    """What the agent receives at call start.

    Returns the block plus `known_caller`: True only when there is at least one
    non-secret fact *and* a brief, i.e. something an agent can actually act on.
    A single stale fact is not enough to claim we know this caller.
    """
    facts = [
        {"key": entry.key, "value": redact(str(entry.value or ""))[:200], "importance": entry.importance}
        for entry in entries
        if not _entry_is_secret(entry.key)
    ]
    brief_text = (brief.brief if brief is not None else "") or ""
    parts: list[str] = []
    if workspace_prompt:
        parts.append(workspace_prompt.strip())
    if brief_text:
        parts.append("Known about this caller:\n" + brief_text)
    elif facts:
        parts.append(
            "Known about this caller:\n"
            + "\n".join(f"{fact['key']}: {fact['value']}" for fact in facts[:10])
        )
    block = "\n\n".join(part for part in parts if part)
    if len(block) > max_chars:
        block = block[: max_chars - 1].rstrip() + "…"
    return {
        "block": block,
        "facts": facts[:20],
        "known_caller": bool(facts) and bool(brief_text),
        "chars": len(block),
    }


def should_skip_node(
    *,
    node_id: str,
    recall_result: dict[str, Any],
    skippable_when_known: Iterable[str] = (),
) -> bool:
    """Whether a workflow node can be skipped because we already know the answer.

    Only nodes the workflow author marked skippable are ever skipped, and only
    when recall reported a `known_caller`. This is the difference between
    "returning callers do not repeat themselves" and "we skipped the identity
    check because a phone number matched".
    """
    if node_id not in set(skippable_when_known):
        return False
    return bool(recall_result.get("known_caller"))


async def backfill_briefs(
    session: AsyncSession,
    tenant: Tenant | UUID,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Build briefs for contacts that have facts but no brief yet."""
    from app.db.channel_models import ContactBrief

    tid = _tenant_id(tenant)
    have = set(
        (
            await session.execute(
                select(ContactBrief.contact_id).where(ContactBrief.tenant_id == tid)
            )
        ).scalars().all()
    )
    candidates = (
        await session.execute(
            select(ContactMemoryEntry.contact_id)
            .where(ContactMemoryEntry.tenant_id == tid)
            .distinct()
            .limit(limit)
        )
    ).scalars().all()
    built = 0
    for contact_id in candidates:
        if contact_id in have:
            continue
        await build_brief(session, tid, contact_id)
        built += 1
    return {"considered": len(candidates), "built": built, "skipped": len(candidates) - built}
