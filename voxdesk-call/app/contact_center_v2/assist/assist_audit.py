"""PII-redacted, idempotent audit of suggestion feedback and acceptance."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import Conflict, Validation
from app.contact_center_v2.repository import owned, utcnow
from app.db.contact_center_v2_models import AssistFeedback, AssistSuggestion


async def record_feedback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    feedback: str,
    note: str = "",
) -> AssistSuggestion:
    """Record one agent's decision without storing raw transcript or PII."""
    if feedback not in {"accepted", "dismissed", "helpful", "not_helpful"}:
        raise Validation("Unknown suggestion feedback")
    from app.ai.guardrails.pii import redact

    note = str(redact(note))[:500]
    row = await owned(session, AssistSuggestion, suggestion_id, tenant_id, for_update=True)
    if row.feedback and (row.feedback != feedback or row.feedback_note != note):
        raise Conflict("Suggestion feedback is already recorded")
    row.feedback = feedback
    row.feedback_note = note
    row.feedback_by = actor_user_id
    row.feedback_at = row.feedback_at or utcnow()
    feedback_row = (
        await session.execute(
            select(AssistFeedback).where(
                AssistFeedback.tenant_id == tenant_id,
                AssistFeedback.suggestion_id == row.id,
                AssistFeedback.agent_user_id == actor_user_id,
            )
        )
    ).scalar_one_or_none()
    if feedback_row is None:
        feedback_row = AssistFeedback(
            tenant_id=tenant_id,
            environment_id=row.environment_id,
            suggestion_id=row.id,
            call_id=row.call_id,
            agent_user_id=actor_user_id,
            action=feedback,
            note_redacted=note,
            shown_at=row.created_at,
            responded_at=row.feedback_at,
            response_latency_ms=max(
                0,
                int((row.feedback_at - row.created_at).total_seconds() * 1000),
            ),
            created_at=row.feedback_at,
        )
        session.add(feedback_row)
    else:
        if feedback_row.action != feedback or feedback_row.note_redacted != note:
            raise Conflict("Suggestion feedback is already recorded")
        feedback_row.responded_at = row.feedback_at
    await session.flush()
    return row


async def feedback_history(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    suggestion_id: uuid.UUID,
) -> list[AssistFeedback]:
    """Return only feedback belonging to the caller's tenant and suggestion."""
    from app.contact_center_v2.errors import NotFound

    suggestion = await session.get(AssistSuggestion, suggestion_id)
    if suggestion is None or suggestion.tenant_id != tenant_id:
        raise NotFound("assist suggestion")
    return list(
        (
            await session.execute(
                select(AssistFeedback).where(
                    AssistFeedback.tenant_id == tenant_id,
                    AssistFeedback.suggestion_id == suggestion_id,
                )
            )
        ).scalars().all()
    )


__all__ = ["feedback_history", "record_feedback"]
