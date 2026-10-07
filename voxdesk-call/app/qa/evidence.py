"""Evidence references an existing turn. It does not copy the transcript body."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Call, Turn
from app.qa.exceptions import InvalidEvidence
from app.qa.models import QAEvidence, QAReview
from app.tenancy.isolation import NotFound

_TYPES = {"scorecard", "compliance", "sentiment", "topic", "coaching", "note"}


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def load_call(session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID) -> Call:
    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != tenant_id:
        raise NotFound()
    return call


async def load_turn(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID, turn_id: uuid.UUID
) -> Turn:
    turn = await session.get(Turn, turn_id)
    if turn is None or turn.call_id != call_id:
        raise NotFound()
    call = await load_call(session, tenant_id, call_id)
    if turn.call_id != call.id:
        raise InvalidEvidence("Turn does not belong to this call")
    return turn


async def add_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    evidence_type: str,
    review: QAReview | None = None,
    turn_id: uuid.UUID | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    target_kind: str = "",
    target_id: uuid.UUID | None = None,
) -> QAEvidence:
    if evidence_type not in _TYPES:
        raise InvalidEvidence("Unknown evidence type")
    call = await load_call(session, tenant_id, call_id)
    if review is not None:
        if review.tenant_id != tenant_id or review.call_id != call.id:
            raise InvalidEvidence("Evidence call does not match the review")
    speaker = ""
    text_hash = ""
    if turn_id is not None:
        turn = await load_turn(session, tenant_id, call.id, turn_id)
        speaker = turn.speaker.value if hasattr(turn.speaker, "value") else str(turn.speaker)
        text_hash = hash_text(turn.text or "")
    if start_ms is not None and start_ms < 0:
        raise InvalidEvidence("start_ms cannot be negative")
    if end_ms is not None and (end_ms < 0 or (start_ms is not None and end_ms < start_ms)):
        raise InvalidEvidence("end_ms is before start_ms")
    row = QAEvidence(
        tenant_id=tenant_id,
        review_id=None if review is None else review.id,
        call_id=call.id,
        turn_id=turn_id,
        speaker=speaker[:16],
        start_ms=start_ms,
        end_ms=end_ms,
        text_hash=text_hash,
        evidence_type=evidence_type,
        target_kind=target_kind[:32],
        target_id=target_id,
    )
    session.add(row)
    await session.flush()
    return row


async def turns_for_call(session: AsyncSession, call_id: uuid.UUID) -> list[Turn]:
    return list(
        (await session.execute(select(Turn).where(Turn.call_id == call_id).order_by(Turn.created_at))).scalars()
    )
