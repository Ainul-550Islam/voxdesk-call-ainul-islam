"""Topic labels are tenant-supplied. This is not a second knowledge base."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.evidence import load_call, load_turn
from app.qa.exceptions import InvalidScore
from app.qa.models import TopicResult
from app.qa.repository import topics_for_call

_RANKS = {"primary", "secondary"}


async def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    label: str,
    confidence: int,
    taxonomy_version: str,
    rank: str = "secondary",
    turn_ids: list[uuid.UUID] | None = None,
    provider: str = "",
    model: str = "",
) -> TopicResult:
    await load_call(session, tenant_id, call_id)
    cleaned = (label or "").strip().lower()
    if not cleaned or len(cleaned) > 64:
        raise InvalidScore("Topic label is required")
    version = (taxonomy_version or "").strip()
    if not version or len(version) > 40:
        raise InvalidScore("Topic taxonomy version is required")
    if rank not in _RANKS:
        raise InvalidScore("Topic rank must be primary or secondary")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or confidence < 0 or confidence > 100:
        raise InvalidScore("Topic confidence must be an integer from 0 to 100")
    references: list[str] = []
    for turn_id in turn_ids or []:
        await load_turn(session, tenant_id, call_id, turn_id)
        references.append(str(turn_id))
    row = TopicResult(
        tenant_id=tenant_id,
        call_id=call_id,
        taxonomy_version=version,
        label=cleaned,
        confidence=confidence,
        rank=rank,
        provider=(provider or "")[:32],
        model=(model or "")[:80],
        evidence_turn_ids=references,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(TopicResult).where(
                    TopicResult.tenant_id == tenant_id,
                    TopicResult.call_id == call_id,
                    TopicResult.taxonomy_version == version,
                    TopicResult.label == cleaned,
                )
            )
        ).scalar_one()
        return found
    return row


async def for_call(session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID) -> list[TopicResult]:
    await load_call(session, tenant_id, call_id)
    return await topics_for_call(session, tenant_id, call_id)
