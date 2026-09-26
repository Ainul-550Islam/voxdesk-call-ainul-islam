"""Sentiment is a classification, not a clinical claim."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.evidence import load_call, load_turn
from app.qa.exceptions import InvalidScore
from app.qa.models import SentimentResult
from app.qa.repository import sentiment_for_call

LABELS = {"positive", "neutral", "negative", "mixed", "unknown"}


def _confidence(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100:
        raise InvalidScore("Sentiment confidence must be an integer from 0 to 100")
    return value


def _label(value: str) -> str:
    label = (value or "").strip().lower()
    if label not in LABELS:
        raise InvalidScore("Unknown sentiment label")
    return label


async def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    label: str,
    confidence: int,
    scope: str = "conversation",
    turn_id: uuid.UUID | None = None,
    provider: str = "",
    model: str = "",
    prompt_version: str = "",
    evidence_id: uuid.UUID | None = None,
) -> SentimentResult:
    if scope not in {"conversation", "turn"}:
        raise InvalidScore("Sentiment scope must be conversation or turn")
    if scope == "turn" and turn_id is None:
        raise InvalidScore("Turn sentiment needs a turn id")
    if scope == "conversation" and turn_id is not None:
        raise InvalidScore("Conversation sentiment cannot name a turn")
    await load_call(session, tenant_id, call_id)
    if turn_id is not None:
        await load_turn(session, tenant_id, call_id, turn_id)
    held = _label(label)
    score = _confidence(confidence)
    model_name = (model or "unspecified")[:80]
    scope_key = f"{call_id}:{turn_id or 'conversation'}:{model_name}"
    row = SentimentResult(
        tenant_id=tenant_id,
        call_id=call_id,
        turn_id=turn_id,
        scope=scope,
        scope_key=scope_key[:120],
        label=held,
        confidence=score,
        provider=(provider or "")[:32],
        model=model_name,
        prompt_version=(prompt_version or "")[:40],
        evidence_id=evidence_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        from sqlalchemy import select

        found = (
            await session.execute(
                select(SentimentResult).where(
                    SentimentResult.tenant_id == tenant_id,
                    SentimentResult.scope_key == row.scope_key,
                )
            )
        ).scalar_one()
        return found
    return row


def aggregate(rows: list[SentimentResult]) -> dict:
    turns = [row for row in rows if row.scope == "turn"]
    if not turns:
        stored = next((row for row in rows if row.scope == "conversation"), None)
        if stored is None:
            return {"label": "unknown", "confidence": 0, "source": "none", "certainty": "classification"}
        return {
            "label": stored.label,
            "confidence": stored.confidence,
            "source": "stored",
            "certainty": "classification",
        }
    counts: dict[str, int] = {}
    for row in turns:
        counts[row.label] = counts.get(row.label, 0) + 1
    label = sorted(counts, key=lambda key: (-counts[key], key))[0]
    confidence = sum(row.confidence for row in turns) // len(turns)
    return {
        "label": label,
        "confidence": confidence,
        "source": "turn_majority",
        "certainty": "classification",
        "turn_count": len(turns),
    }


async def summary(session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID) -> dict:
    await load_call(session, tenant_id, call_id)
    rows = await sentiment_for_call(session, tenant_id, call_id)
    body = aggregate(rows)
    body["results"] = [row.as_dict() for row in rows]
    return body
