"""Auto-review suggests. It does not finalize, invent a score, or overwrite a person."""

from __future__ import annotations

import json
import uuid

import pytest

from app.agent.errors import ProviderAuthenticationError, ProviderTimeoutError
from app.qa.auto_review import PROMPT_VERSION, context_for, enqueue, process_run
from app.qa.exceptions import DuplicateAutoReview
from app.qa.repository import review_items
from app.qa.rubrics import create_scorecard
from app.qa.service import assign_review, create_review, finalize_review, save_scores, submit_review
from tests.acd_support import live_call, production


async def _review(db, tenant, actor):
    env = await production(db, tenant)
    call = await live_call(db, tenant, env)
    card = await create_scorecard(
        db,
        tenant_id=tenant.id,
        name=f"auto-{uuid.uuid4().hex[:8]}",
        sections=[
            {
                "name": "Opening",
                "weight": 1,
                "items": [{"name": "Greeting", "weight": 1, "min_score": 0, "max_score": 100, "required": True}],
            }
        ],
    )
    review, _ = await create_review(
        db, tenant_id=tenant.id, call_id=call.id, scorecard_id=card.id, actor_id=actor.id
    )
    await db.commit()
    return review


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_a_missing_executor_is_not_a_score(
    db, tenant_a, owner_a, billing_plans
):
    review = await _review(db, tenant_a, owner_a)
    first, outcome = await enqueue(db, tenant_id=tenant_a.id, review=review)
    second, again = await enqueue(db, tenant_id=tenant_a.id, review=review)
    assert outcome == "queued"
    assert again == "duplicate"
    assert second.id == first.id
    assert first.prompt_version == PROMPT_VERSION
    ctx = await context_for(db, tenant_a.id, owner_a.id)
    failed = await process_run(db, tenant_id=tenant_a.id, run_id=first.id, ctx=ctx, executor=None)
    assert failed.status == "failed"
    assert failed.error_class == "executor_not_attached"
    assert failed.suggestion in ({}, None)
    await db.refresh(review)
    assert review.status == "created"
    assert review.overall_score is None


@pytest.mark.asyncio
async def test_suggestion_does_not_finalize_or_replace_a_human_score(
    db, tenant_a, owner_a, billing_plans
):
    review = await _review(db, tenant_a, owner_a)
    await assign_review(
        db,
        tenant_id=tenant_a.id,
        review_id=review.id,
        assignee_id=owner_a.id,
        actor_id=owner_a.id,
    )
    held = await review_items(db, tenant_a.id, review.id)
    await save_scores(
        db,
        tenant_id=tenant_a.id,
        review_id=review.id,
        actor_id=owner_a.id,
        scores=[{"item_id": held[0].item_id, "score": 70, "override_reason": "heard it"}],
    )
    run, _ = await enqueue(db, tenant_id=tenant_a.id, review=review)
    item_id = held[0].item_id

    async def executor(choice, text, timeout_ms):
        assert "sk-" not in text
        return {
            "text": json.dumps({"items": [{"item_id": str(item_id), "score": 10, "turn_ids": []}]}),
            "tokens": 2,
        }

    ctx = await context_for(db, tenant_a.id, owner_a.id)
    done = await process_run(db, tenant_id=tenant_a.id, run_id=run.id, ctx=ctx, executor=executor)
    assert done.status == "completed"
    assert done.model
    assert done.prompt_version == PROMPT_VERSION
    await db.refresh(held[0])
    await db.refresh(review)
    assert held[0].human_score == 70
    assert held[0].ai_score == 10
    assert held[0].accepted_source == "human"
    assert review.status != "finalized"


@pytest.mark.asyncio
async def test_bad_evidence_and_auth_failures_are_permanent(db, tenant_a, owner_a, billing_plans):
    review = await _review(db, tenant_a, owner_a)
    run, _ = await enqueue(db, tenant_id=tenant_a.id, review=review)
    held = await review_items(db, tenant_a.id, review.id)
    ctx = await context_for(db, tenant_a.id, owner_a.id)

    async def cite_other(choice, text, timeout_ms):
        return {
            "text": json.dumps(
                {
                    "items": [
                        {
                            "item_id": str(held[0].item_id),
                            "score": 10,
                            "turn_ids": [str(uuid.uuid4())],
                        }
                    ]
                }
            ),
            "tokens": 1,
        }

    bad = await process_run(db, tenant_id=tenant_a.id, run_id=run.id, ctx=ctx, executor=cite_other)
    assert bad.status == "permanent_failure"
    assert bad.error_class == "validation"
    again, outcome = await enqueue(db, tenant_id=tenant_a.id, review=review)
    assert outcome == "permanent"
    assert again.id == run.id

    other = await _review(db, tenant_a, owner_a)
    retryable, _ = await enqueue(db, tenant_id=tenant_a.id, review=other)

    async def slow(choice, text, timeout_ms):
        raise ProviderTimeoutError("slow", provider="openai")

    failed = await process_run(db, tenant_id=tenant_a.id, run_id=retryable.id, ctx=ctx, executor=slow)
    assert failed.status == "failed"

    async def denied(choice, text, timeout_ms):
        raise ProviderAuthenticationError("no", provider="openai")

    stopped = await process_run(db, tenant_id=tenant_a.id, run_id=retryable.id, ctx=ctx, executor=denied)
    assert stopped.status == "permanent_failure"


@pytest.mark.asyncio
async def test_finalized_review_is_not_sent_back_to_the_model(db, tenant_a, owner_a):
    review = await _review(db, tenant_a, owner_a)
    await assign_review(
        db,
        tenant_id=tenant_a.id,
        review_id=review.id,
        assignee_id=owner_a.id,
        actor_id=owner_a.id,
    )
    held = await review_items(db, tenant_a.id, review.id)
    await save_scores(
        db,
        tenant_id=tenant_a.id,
        review_id=review.id,
        actor_id=owner_a.id,
        scores=[{"item_id": held[0].item_id, "score": 80}],
    )
    await submit_review(db, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id)
    await finalize_review(
        db, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id
    )
    with pytest.raises(DuplicateAutoReview):
        await enqueue(db, tenant_id=tenant_a.id, review=review)
