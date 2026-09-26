"""Sentiment is a classification. It is not a clinical claim."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import Speaker, Turn
from app.qa.exceptions import InvalidScore
from app.qa.models import SentimentResult
from app.qa.sentiment import aggregate, record, summary
from app.tenancy.isolation import NotFound
from tests.acd_support import live_call, production


@pytest.mark.asyncio
async def test_labels_confidence_and_duplicate_scope(db, tenant_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    row = await record(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        label="Positive",
        confidence=80,
        model="classifier-a",
    )
    again = await record(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        label="negative",
        confidence=10,
        model="classifier-a",
    )
    assert again.id == row.id
    assert again.label == "positive"
    count = await db.scalar(
        select(func.count()).select_from(SentimentResult).where(SentimentResult.call_id == call.id)
    )
    assert int(count) == 1
    with pytest.raises(InvalidScore):
        await record(
            db, tenant_id=tenant_a.id, call_id=call.id, label="delighted", confidence=1, model="other"
        )
    with pytest.raises(InvalidScore):
        await record(
            db, tenant_id=tenant_a.id, call_id=call.id, label="neutral", confidence=101, model="other"
        )
    body = await summary(db, tenant_a.id, call.id)
    assert body["certainty"] == "classification"
    assert body["label"] == "positive"
    assert body["source"] == "stored"


@pytest.mark.asyncio
async def test_turn_majority_beats_a_stored_conversation_label(db, tenant_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    turns = []
    for text in ("no", "no", "yes"):
        turn = Turn(call_id=call.id, speaker=Speaker.USER, text=text)
        db.add(turn)
        turns.append(turn)
    await db.flush()
    await record(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        label="positive",
        confidence=99,
        scope="conversation",
        model="conversation",
    )
    for turn, label in zip(turns, ("negative", "negative", "positive")):
        await record(
            db,
            tenant_id=tenant_a.id,
            call_id=call.id,
            label=label,
            confidence=60,
            scope="turn",
            turn_id=turn.id,
            model="turn-model",
        )
    body = aggregate(await db.scalars(select(SentimentResult).where(SentimentResult.call_id == call.id)))
    assert body["label"] == "negative"
    assert body["source"] == "turn_majority"
    assert body["certainty"] == "classification"
    assert "diagnosis" not in body


@pytest.mark.asyncio
async def test_unknown_when_nothing_is_stored_and_foreign_call_is_missing(db, tenant_a, tenant_b):
    assert aggregate([])["label"] == "unknown"
    env = await production(db, tenant_b)
    foreign = await live_call(db, tenant_b, env)
    with pytest.raises(NotFound):
        await summary(db, tenant_a.id, foreign.id)
    with pytest.raises(NotFound):
        await summary(db, tenant_a.id, uuid.uuid4())
