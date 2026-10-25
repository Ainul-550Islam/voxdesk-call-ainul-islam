"""After-call work gate shared by pacing and agent-state transitions."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error, NotFound, PermissionDenied
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import DialerAttempt, WrapupDraft


async def ensure_wrapup_draft(
    session: AsyncSession,
    attempt: DialerAttempt,
    *,
    now: datetime | None = None,
) -> WrapupDraft | None:
    """Create the durable ACW obligation after a connected dialer call ends."""
    if attempt.agent_user_id is None or attempt.agent_connected_at is None or attempt.call_id is None:
        return None
    if attempt.outcome != "completed":
        return None
    existing = (
        await session.execute(
            select(WrapupDraft).where(
                WrapupDraft.tenant_id == attempt.tenant_id,
                WrapupDraft.call_id == attempt.call_id,
                WrapupDraft.agent_user_id == attempt.agent_user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    moment = now or utcnow()
    row = WrapupDraft(
        tenant_id=attempt.tenant_id,
        environment_id=attempt.environment_id,
        call_id=attempt.call_id,
        agent_user_id=attempt.agent_user_id,
        status="draft",
        deadline_at=moment + timedelta(seconds=settings.wrapup_default_seconds),
        created_at=moment,
        updated_at=moment,
    )
    session.add(row)
    await session.flush()
    return row


async def assert_wrapup_complete(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_user_id: uuid.UUID,
) -> None:
    """Refuse available/pacing work while an agent has unsubmitted CC2 ACW."""
    pending = (
        await session.execute(
            select(WrapupDraft).where(
                WrapupDraft.tenant_id == tenant_id,
                WrapupDraft.agent_user_id == agent_user_id,
                WrapupDraft.status.in_(("draft", "failed")),
            )
        )
    ).scalars().first()
    if pending is not None:
        raise ContactCenterV2Error(
            "wrapup_required",
            "Submit the required after-call wrap-up before becoming available",
            detail={"call_id": str(pending.call_id), "deadline_at": pending.deadline_at.isoformat()},
            status_code=409,
        )


async def submit_wrapup(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    call_id: uuid.UUID,
    disposition: str,
    summary_redacted: str = "",
    tags: list[str] | None = None,
) -> WrapupDraft:
    row = (
        await session.execute(
            select(WrapupDraft).where(
                WrapupDraft.tenant_id == tenant_id,
                WrapupDraft.call_id == call_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("wrap-up draft")
    if row.agent_user_id != agent_user_id:
        raise PermissionDenied("This wrap-up belongs to another agent")
    clean_disposition = disposition.strip()
    if not clean_disposition or len(clean_disposition) > 80:
        raise ContactCenterV2Error("wrapup_disposition_required", "A bounded wrap-up disposition is required", status_code=422)
    if row.status == "submitted":
        if row.disposition != clean_disposition or row.summary_redacted != summary_redacted.strip():
            raise ContactCenterV2Error("wrapup_already_submitted", "A submitted wrap-up cannot be changed", status_code=409)
        return row
    row.disposition = clean_disposition
    row.summary_redacted = summary_redacted.strip()[:8000]
    row.tags = [str(tag).strip()[:60] for tag in (tags or []) if str(tag).strip()][:20]
    row.status = "submitted"
    row.submitted_at = utcnow()
    row.updated_at = row.submitted_at
    await session.flush()
    return row


__all__ = ["assert_wrapup_complete", "ensure_wrapup_draft", "submit_wrapup"]
