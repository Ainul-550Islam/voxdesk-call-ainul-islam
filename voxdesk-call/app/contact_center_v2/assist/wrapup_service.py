"""Redacted after-call summaries and the durable wrap-up submission gate."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.guardrails.pii import redact
from app.contact_center_v2.dialer.wrapup_guard import assert_wrapup_complete, submit_wrapup
from app.contact_center_v2.errors import Conflict, NotFound
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import AssistTurn, WrapupDraft
from app.integrations.crm import events as crm_events
from app.db.models import (
    Call,
    CrmEvent,
    CrmEventType,
    CrmSync,
    CrmSyncStatus,
    Tenant,
    User,
)


def extractive_summary(turns: list[AssistTurn], *, max_chars: int = 8000) -> str:
    """Summarize only observed redacted turns; never invent a call outcome."""
    lines = [f"{turn.speaker}: {turn.text_redacted.strip()}" for turn in turns if turn.text_redacted.strip()]
    return "\n".join(lines)[:max_chars]


async def generate_wrapup(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    agent_user_id: uuid.UUID,
) -> WrapupDraft:
    call = await session.get(Call, call_id)
    agent = await session.get(User, agent_user_id)
    if call is None or call.tenant_id != tenant_id or agent is None or agent.tenant_id != tenant_id:
        raise NotFound("wrap-up scope")
    existing = (
        await session.execute(
            select(WrapupDraft).where(
                WrapupDraft.tenant_id == tenant_id,
                WrapupDraft.call_id == call_id,
                WrapupDraft.agent_user_id == agent_user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "submitted":
        raise Conflict("Wrap-up has already been submitted")
    turns = list(
        (
            await session.execute(
                select(AssistTurn).where(
                    AssistTurn.tenant_id == tenant_id,
                    AssistTurn.call_id == call_id,
                ).order_by(AssistTurn.created_at, AssistTurn.id)
            )
        )
        .scalars()
        .all()
    )
    summary = str(redact(extractive_summary(turns)))
    moment = utcnow()
    if existing is None:
        existing = WrapupDraft(
            tenant_id=tenant_id,
            environment_id=call.environment_id,
            call_id=call_id,
            agent_user_id=agent_user_id,
            status="draft",
            deadline_at=moment + timedelta(seconds=settings.wrapup_default_seconds),
            created_at=moment,
        )
        session.add(existing)
    existing.summary_redacted = summary
    existing.generation_method = "redacted_transcript"
    existing.updated_at = moment
    await session.flush()
    return existing


async def queue_approved_crm_writeback(
    session: AsyncSession, row: WrapupDraft
) -> WrapupDraft:
    """Persist one approved wrap-up in the canonical CRM delivery pipeline.

    Submission is the human approval boundary. Only redacted summary text and
    bounded disposition metadata are admitted; provider delivery remains an
    asynchronous, acknowledged operation rather than a claimed HTTP success.
    """
    if row.status != "submitted" or row.crm_status in {"pending", "written", "failed"}:
        return row
    call = await session.get(Call, row.call_id)
    tenant = await session.get(Tenant, row.tenant_id)
    if (
        call is None
        or call.tenant_id != row.tenant_id
        or call.environment_id != row.environment_id
        or tenant is None
        or tenant.id != row.tenant_id
    ):
        raise NotFound("wrap-up CRM scope")
    discriminator = f"cc2-wrapup:{row.id}"
    payload = crm_events.call_payload(
        call, tenant, include_transcript_reference=False
    )
    payload.update(
        {
            "status": row.disposition,
            "summary": row.summary_redacted,
            "wrapup_id": str(row.id),
            "wrapup_tags": list(row.tags or [])[:20],
            "agent_approved": True,
        }
    )
    event = await crm_events.emit(
        session,
        tenant_id=row.tenant_id,
        event_type=CrmEventType.CALL_COMPLETED,
        entity_id=call.id,
        payload=payload,
        discriminator=discriminator,
    )
    if event is None:
        event = (
            await session.execute(
                select(CrmEvent).where(
                    CrmEvent.tenant_id == row.tenant_id,
                    CrmEvent.idempotency_key == crm_events.idempotency_key(
                        CrmEventType.CALL_COMPLETED, call.id, discriminator=discriminator
                    ),
                )
            )
        ).scalar_one_or_none()
    if event is None:
        raise Conflict("Approved wrap-up CRM event could not be recovered")
    syncs = (
        (
            await session.execute(
                select(CrmSync).where(
                    CrmSync.tenant_id == row.tenant_id,
                    CrmSync.event_id == event.id,
                )
            )
        )
        .scalars()
        .all()
    )
    row.crm_reference = str(event.id)
    row.crm_status = "pending" if syncs else "not_requested"
    row.failure_category = ""
    await session.flush()
    return row


async def reconcile_crm_writebacks(
    session: AsyncSession, *, tenant_id: uuid.UUID | None = None, limit: int = 100
) -> int:
    """Reflect acknowledged canonical CRM sync states on approved wrap-ups."""
    stmt = select(WrapupDraft).where(
        WrapupDraft.status == "submitted",
        WrapupDraft.crm_status.in_(["pending", "failed"]),
        WrapupDraft.crm_reference != "",
    )
    if tenant_id is not None:
        stmt = stmt.where(WrapupDraft.tenant_id == tenant_id)
    rows = (
        (await session.execute(stmt.order_by(WrapupDraft.updated_at).limit(limit)))
        .scalars()
        .all()
    )
    changed = 0
    for row in rows:
        previous = (row.crm_status, row.failure_category)
        try:
            event_id = uuid.UUID(row.crm_reference)
        except ValueError:
            row.crm_status = "failed"
            row.failure_category = "crm_event_reference_invalid"
            changed += 1
            continue
        syncs = (
            (
                await session.execute(
                    select(CrmSync).where(
                        CrmSync.tenant_id == row.tenant_id,
                        CrmSync.event_id == event_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not syncs:
            row.crm_status = "failed"
            row.failure_category = "crm_delivery_missing"
        elif all(
            sync.status is CrmSyncStatus.SYNCED and bool(sync.external_id)
            for sync in syncs
        ):
            row.crm_status = "written"
            row.failure_category = ""
        elif all(
            sync.status in {CrmSyncStatus.SYNCED, CrmSyncStatus.PERMANENT_FAILURE}
            for sync in syncs
        ):
            row.crm_status = "failed"
            row.failure_category = "crm_delivery_failed"
        else:
            continue
        if (row.crm_status, row.failure_category) == previous:
            continue
        row.updated_at = utcnow()
        changed += 1
    if changed:
        await session.flush()
    return changed


async def submit_call_wrapup(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    disposition: str,
    summary_redacted: str = "",
    tags: list[str] | None = None,
) -> WrapupDraft:
    row = await submit_wrapup(
        session,
        tenant_id=tenant_id,
        agent_user_id=agent_user_id,
        call_id=call_id,
        disposition=disposition,
        summary_redacted=str(redact(summary_redacted)),
        tags=tags,
    )
    return await queue_approved_crm_writeback(session, row)


async def pending_wrapups(
    session: AsyncSession, *, tenant_id: uuid.UUID, agent_user_id: uuid.UUID
) -> list[WrapupDraft]:
    return list(
        (
            await session.execute(
                select(WrapupDraft).where(
                    WrapupDraft.tenant_id == tenant_id,
                    WrapupDraft.agent_user_id == agent_user_id,
                    WrapupDraft.status.in_(("draft", "failed")),
                ).order_by(WrapupDraft.deadline_at)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "assert_wrapup_complete",
    "extractive_summary",
    "generate_wrapup",
    "pending_wrapups",
    "queue_approved_crm_writeback",
    "reconcile_crm_writebacks",
    "submit_call_wrapup",
    "submit_wrapup",
]
