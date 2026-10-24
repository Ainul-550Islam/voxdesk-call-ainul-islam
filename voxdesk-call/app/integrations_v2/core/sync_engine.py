"""Incremental cursor-checkpointed sync engine with conflict policies and dead-letter isolation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.integrations_v2_models import (
    ExternalIdentityLink,
    FieldMappingRule,
    IntegrationConnection,
    SyncCursor,
    SyncRun,
)
from app.db.models import Lead
from app.integrations_v2.core.errors import IntegrationV2Error, redact_secrets
from app.integrations_v2.core.field_mapping import (
    DEFAULT_CONTACT_RULES,
    MappingRuleSpec,
    execute_field_mapping,
)
from app.integrations_v2.core.pagination import PageBatch

CONFLICT_POLICIES = frozenset({"source_wins", "external_wins", "voxdesk_wins", "newest_wins"})


@dataclass(frozen=True)
class ExternalSyncRecord:
    external_id: str
    entity_type: str
    raw_fields: dict[str, Any]
    external_updated_at: datetime | None = None
    external_etag: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def should_apply_external_change(
    *,
    policy: str,
    local_updated_at: datetime | None,
    external_updated_at: datetime | None,
    existing_local: bool,
) -> bool:
    """Deterministic conflict resolution between a VoxDesk entity and an external CRM/Helpdesk record."""
    if not existing_local:
        return True
    if policy in ("external_wins", "source_wins"):
        return True
    if policy == "voxdesk_wins":
        return False
    # newest_wins (default)
    loc = _as_utc(local_updated_at)
    ext = _as_utc(external_updated_at)
    if loc is None or ext is None:
        return True
    return ext >= loc


async def _load_mapping_specs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    entity_type: str,
) -> list[MappingRuleSpec]:
    stmt = select(FieldMappingRule).where(
        FieldMappingRule.tenant_id == tenant_id,
        FieldMappingRule.connection_id == connection_id,
        FieldMappingRule.entity_type == entity_type,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        if entity_type in ("Contact", "Lead"):
            return list(DEFAULT_CONTACT_RULES)
        return []
    return [
        MappingRuleSpec(
            entity_type=r.entity_type,
            voxdesk_field=r.voxdesk_field,
            external_field=r.external_field,
            direction=r.direction,
            transform=r.transform,
            transform_config=dict(r.transform_config or {}),
            required=r.required,
            default_value=r.default_value,
        )
        for r in rows
    ]


async def get_or_create_cursor(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    entity_type: str,
    cursor_kind: str = "updated_since",
) -> SyncCursor:
    stmt = select(SyncCursor).where(
        SyncCursor.connection_id == connection_id,
        SyncCursor.entity_type == entity_type,
        SyncCursor.tenant_id == tenant_id,
    )
    cursor_row = (await session.execute(stmt)).scalar_one_or_none()
    if cursor_row is None:
        cursor_row = SyncCursor(
            tenant_id=tenant_id,
            connection_id=connection_id,
            entity_type=entity_type,
            cursor_value="",
            cursor_kind=cursor_kind,
        )
        session.add(cursor_row)
        await session.flush()
    return cursor_row


async def _upsert_inbound_lead_or_contact(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    external_rec: ExternalSyncRecord,
    mapped: dict[str, Any],
    policy: str,
) -> tuple[str, bool]:
    """Upsert into VoxDesk ``leads`` + ``int2_external_links`` with strict tenant scoping."""
    tenant_id = connection.tenant_id
    link_stmt = select(ExternalIdentityLink).where(
        ExternalIdentityLink.connection_id == connection.id,
        ExternalIdentityLink.entity_type == external_rec.entity_type,
        ExternalIdentityLink.external_id == external_rec.external_id,
        ExternalIdentityLink.tenant_id == tenant_id,
    )
    link = (await session.execute(link_stmt)).scalar_one_or_none()

    lead_row: Lead | None = None
    if link is not None:
        try:
            lead_uuid = uuid.UUID(link.voxdesk_id)
            lead_stmt = select(Lead).where(
                Lead.id == lead_uuid, Lead.tenant_id == tenant_id
            )
            lead_row = (await session.execute(lead_stmt)).scalar_one_or_none()
        except ValueError:
            lead_row = None

    email = str(mapped.get("email") or "").strip().lower()
    phone = str(mapped.get("phone") or "").strip()

    if lead_row is None and email:
        lead_row = (
            await session.execute(
                select(Lead).where(Lead.tenant_id == tenant_id, Lead.email == email)
            )
        ).scalar_one_or_none()

    if lead_row is None and phone:
        lead_row = (
            await session.execute(
                select(Lead).where(Lead.tenant_id == tenant_id, Lead.phone == phone)
            )
        ).scalar_one_or_none()

    full_name = str(mapped.get("name") or "").strip()
    if not full_name:
        fn = str(mapped.get("first_name") or "").strip()
        ln = str(mapped.get("last_name") or "").strip()
        full_name = f"{fn} {ln}".strip()

    if lead_row is not None:
        local_ts = link.last_synced_at if link is not None else lead_row.created_at
        apply_change = should_apply_external_change(
            policy=policy,
            local_updated_at=local_ts,
            external_updated_at=external_rec.external_updated_at,
            existing_local=True,
        )
        if apply_change:
            if full_name:
                lead_row.name = full_name[:120]
            if mapped.get("email"):
                lead_row.email = str(mapped["email"])[:255]
            if mapped.get("phone"):
                lead_row.phone = str(mapped["phone"])[:40]
            if mapped.get("company"):
                lead_row.company = str(mapped["company"])[:160]
            meta = dict(lead_row.custom_fields or {})
            if isinstance(mapped.get("custom_fields"), dict):
                meta.update(mapped["custom_fields"])
            meta["synced_from"] = connection.provider
            lead_row.custom_fields = meta
        voxdesk_id = str(lead_row.id)
    else:
        custom_meta = dict(mapped.get("custom_fields") or {})
        custom_meta["synced_from"] = connection.provider
        lead_row = Lead(
            tenant_id=tenant_id,
            name=(full_name or external_rec.external_id)[:120],
            email=str(mapped["email"])[:255] if mapped.get("email") else "",
            phone=str(mapped["phone"])[:40] if mapped.get("phone") else "+10000000000",
            company=str(mapped.get("company") or "")[:160],
            status=str(mapped.get("status") or "new")[:40],
            custom_fields=custom_meta,
        )
        session.add(lead_row)
        await session.flush()
        voxdesk_id = str(lead_row.id)
        apply_change = True

    now = _now()
    if link is None:
        link = ExternalIdentityLink(
            tenant_id=tenant_id,
            connection_id=connection.id,
            entity_type=external_rec.entity_type,
            voxdesk_id=voxdesk_id,
            external_id=external_rec.external_id[:160],
            external_etag=(external_rec.external_etag or "")[:160],
            external_updated_at=external_rec.external_updated_at,
            last_synced_at=now,
        )
        session.add(link)
    else:
        link.voxdesk_id = voxdesk_id
        link.external_etag = (external_rec.external_etag or link.external_etag)[:160]
        if external_rec.external_updated_at is not None:
            link.external_updated_at = external_rec.external_updated_at
        link.last_synced_at = now

    await session.flush()
    return voxdesk_id, apply_change


async def run_incremental_sync(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    entity_type: str,
    page_stream_factory: Callable[[str | None], AsyncIterator[PageBatch[ExternalSyncRecord]]],
    conflict_policy: str | None = None,
    cursor_kind: str = "updated_since",
) -> SyncRun:
    """Execute incremental sync page-by-page, checkpointing ``SyncCursor`` after each committed page.

    Any malformed record is isolated into ``SyncRun.dead_letters`` without aborting healthy records.
    If the stream raises mid-run, all prior checkpointed pages remain durable so the next run resumes
    cleanly from ``cursor_row.cursor_value``.
    """
    policy = conflict_policy or str(
        (connection.config_json or {}).get("conflict_policy") or "newest_wins"
    )
    if policy not in CONFLICT_POLICIES:
        raise IntegrationV2Error(f"Unsupported conflict policy {policy!r}")

    cursor_row = await get_or_create_cursor(
        session,
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        entity_type=entity_type,
        cursor_kind=cursor_kind,
    )
    rules = await _load_mapping_specs(
        session,
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        entity_type=entity_type,
    )

    run = SyncRun(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        entity_type=entity_type,
        direction="inbound",
        status="running",
        cursor_before=cursor_row.cursor_value or "",
        cursor_after=cursor_row.cursor_value or "",
        records_seen=0,
        records_upserted=0,
        records_failed=0,
        dead_letters=[],
    )
    session.add(run)
    await session.flush()

    dead_letters: list[dict[str, Any]] = []
    start_cursor = cursor_row.cursor_value or None

    try:
        async for page in page_stream_factory(start_cursor):
            for rec in page.items:
                run.records_seen += 1
                try:
                    if rules:
                        preview = execute_field_mapping(
                            rules, rec.raw_fields, direction="inbound"
                        )
                        if not preview.ok:
                            raise ValueError("; ".join(preview.errors))
                        mapped = preview.output
                    else:
                        mapped = dict(rec.raw_fields)

                    if entity_type in ("Contact", "Lead"):
                        _, changed = await _upsert_inbound_lead_or_contact(
                            session,
                            connection=connection,
                            external_rec=rec,
                            mapped=mapped,
                            policy=policy,
                        )
                    else:
                        link_stmt = select(ExternalIdentityLink).where(
                            ExternalIdentityLink.connection_id == connection.id,
                            ExternalIdentityLink.entity_type == entity_type,
                            ExternalIdentityLink.external_id == rec.external_id,
                            ExternalIdentityLink.tenant_id == connection.tenant_id,
                        )
                        link = (await session.execute(link_stmt)).scalar_one_or_none()
                        if link is None:
                            session.add(
                                ExternalIdentityLink(
                                    tenant_id=connection.tenant_id,
                                    connection_id=connection.id,
                                    entity_type=entity_type,
                                    voxdesk_id=f"ext:{rec.external_id}"[:120],
                                    external_id=rec.external_id[:160],
                                    external_etag=(rec.external_etag or "")[:160],
                                    external_updated_at=rec.external_updated_at,
                                )
                            )
                        changed = True

                    if changed:
                        run.records_upserted += 1
                except Exception as rec_exc:
                    run.records_failed += 1
                    if len(dead_letters) < 50:
                        dead_letters.append(
                            {
                                "external_id": rec.external_id,
                                "error": redact_secrets(str(rec_exc), max_len=240),
                            }
                        )

            # Checkpoint cursor after each page so crash recovery resumes from last completed page
            if page.next_cursor:
                cursor_row.cursor_value = page.next_cursor[:1024]
                run.cursor_after = cursor_row.cursor_value
            cursor_row.last_synced_at = _now()
            cursor_row.updated_at = _now()
            run.dead_letters = list(dead_letters)
            await session.flush()

        run.status = "partial" if run.records_failed > 0 else "succeeded"
        run.finished_at = _now()
        run.dead_letters = list(dead_letters)
        await session.flush()
        log.info(
            "int2.sync.completed",
            provider=connection.provider,
            entity=entity_type,
            seen=run.records_seen,
            upserted=run.records_upserted,
            failed=run.records_failed,
        )
        return run
    except Exception as exc:
        run.status = "failed"
        run.error_summary = redact_secrets(str(exc), max_len=480)
        run.dead_letters = list(dead_letters)
        run.finished_at = _now()
        await session.flush()
        log.warning(
            "int2.sync.failed",
            provider=connection.provider,
            entity=entity_type,
            error=run.error_summary,
        )
        return run
