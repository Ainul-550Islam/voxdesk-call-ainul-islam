"""Grounded suggestion generation and durable agent feedback."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.contact_center_v2_models import AssistSuggestion
from .assist_audit import feedback_history, record_feedback
from .service import (
    _parse_suggestions,
    alerts_for_call,
    assist_counts,
    process_turn,
    publish_suggestion_notices,
    suggestions_for_call,
)


def parse_provider_suggestions(text: str) -> tuple[str, str]:
    return _parse_suggestions(text)


async def generate_suggestions(
    session: AsyncSession, *, tenant_id: uuid.UUID, turn_id: uuid.UUID, executor: Any = None
) -> list[AssistSuggestion]:
    rows = await process_turn(session, tenant_id=tenant_id, turn_id=turn_id, executor=executor)
    if rows:
        call_id = rows[0].call_id
        await publish_suggestion_notices(rows, tenant_id=tenant_id, call_id=call_id)
    return rows




__all__ = [
    "alerts_for_call",
    "assist_counts",
    "feedback_history",
    "generate_suggestions",
    "parse_provider_suggestions",
    "process_turn",
    "record_feedback",
    "suggestions_for_call",
]
