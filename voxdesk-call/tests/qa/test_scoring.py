"""Deterministic weighted scoring and a human override that stays auditable."""

from __future__ import annotations

import uuid

import pytest

from app.qa.exceptions import InvalidScore
from app.qa.repository import review_items
from app.qa.rubrics import create_scorecard
from app.qa.scoring import FORMULA, ItemInput, calculate, reproduce
from app.qa.service import apply_ai_suggestion, assign_review, create_review, save_scores, submit_review
from tests.acd_support import live_call, production


def _item(**overrides) -> ItemInput:
    base = dict(
        item_id="opening",
        section_id="greeting",
        section_weight=1,
        weight=1,
        min_score=0,
        max_score=100,
        required=True,
        value=80,
        not_applicable=False,
    )
    base.update(overrides)
    return ItemInput(**base)


def test_same_inputs_always_produce_the_same_integers():
    items = [
        _item(item_id="a", weight=1, value=0),
        _item(item_id="b", weight=3, value=100),
    ]
    first = calculate(items, pass_threshold=7000)
    second = calculate(items, pass_threshold=7000)
    assert first.overall == 7500
    assert first.passed is True
    assert first.as_dict() == second.as_dict()
    assert first.formula == FORMULA
    again = reproduce(
        {
            "pass_threshold": 7000,
            "items": [
                {
                    "item_id": item.item_id,
                    "section_id": item.section_id,
                    "section_weight": item.section_weight,
                    "weight": item.weight,
                    "min_score": item.min_score,
                    "max_score": item.max_score,
                    "required": item.required,
                    "value": item.value,
                    "not_applicable": item.not_applicable,
                }
                for item in items
            ],
        }
    )
    assert again.overall == first.overall
    assert again.passed is first.passed


def test_na_is_excluded_and_a_missing_required_item_fails():
    scored = calculate(
        [
            _item(value=0, weight=1),
            _item(item_id="skip", not_applicable=True, value=None, required=False),
        ],
        pass_threshold=1,
    )
    assert scored.overall == 0
    assert scored.passed is False
    missing = calculate(
        [_item(value=100), _item(item_id="required-blank", value=None)],
        pass_threshold=1,
    )
    assert missing.missing_required == ["required-blank"]
    assert missing.passed is False
    assert missing.overall == 10000


def test_non_integer_score_is_rejected():
    with pytest.raises(InvalidScore):
        calculate([_item(value=True)], pass_threshold=1)


@pytest.mark.asyncio
async def test_human_override_keeps_the_ai_value_and_records_the_actor(db, tenant_a, manager_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    card = await create_scorecard(
        db,
        tenant_id=tenant_a.id,
        name=f"card-{uuid.uuid4().hex[:8]}",
        sections=[
            {
                "name": "Opening",
                "weight": 2,
                "items": [
                    {
                        "name": "Greeting",
                        "weight": 1,
                        "min_score": 0,
                        "max_score": 100,
                        "required": True,
                    }
                ],
            }
        ],
        pass_threshold=7000,
    )
    review, outcome = await create_review(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        scorecard_id=card.id,
        actor_id=manager_a.id,
    )
    assert outcome == "created"
    await assign_review(
        db,
        tenant_id=tenant_a.id,
        review_id=review.id,
        assignee_id=manager_a.id,
        actor_id=manager_a.id,
    )
    held = await review_items(db, tenant_a.id, review.id)
    held[0].ai_score = 40
    await db.flush()
    with pytest.raises(InvalidScore):
        await save_scores(
            db,
            tenant_id=tenant_a.id,
            review_id=review.id,
            actor_id=manager_a.id,
            scores=[{"item_id": held[0].item_id, "score": 90}],
        )
    await save_scores(
        db,
        tenant_id=tenant_a.id,
        review_id=review.id,
        actor_id=manager_a.id,
        scores=[{"item_id": held[0].item_id, "score": 90, "override_reason": "heard the greeting"}],
    )
    await db.refresh(held[0])
    assert held[0].human_score == 90
    assert held[0].ai_score == 40
    assert held[0].accepted_source == "human"
    assert held[0].actor_id == manager_a.id
    assert held[0].scored_at is not None
    assert held[0].override_reason == "heard the greeting"
    with pytest.raises(Exception):
        await apply_ai_suggestion(
            db,
            tenant_id=tenant_a.id,
            review_id=review.id,
            item_id=held[0].item_id,
            actor_id=manager_a.id,
        )
    submitted = await submit_review(
        db, tenant_id=tenant_a.id, review_id=review.id, actor_id=manager_a.id
    )
    assert submitted.status == "calculated"
    assert submitted.overall_score == 9000
    assert submitted.passed is True
    assert submitted.calculation_snapshot["formula"] == FORMULA
    replay = reproduce(submitted.calculation_snapshot)
    assert replay.overall == submitted.overall_score
    assert replay.passed is submitted.passed
