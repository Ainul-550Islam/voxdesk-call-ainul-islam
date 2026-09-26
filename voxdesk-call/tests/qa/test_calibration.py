"""Calibration records disagreement. It does not rewrite a finalized review."""

from __future__ import annotations

import uuid

import pytest

from app.qa.calibration import compare, complete, disagreement, start
from app.qa.exceptions import CalibrationConflict
from app.qa.repository import review_items
from app.qa.rubrics import create_scorecard
from app.qa.service import assign_review, create_review, finalize_review, save_scores, submit_review
from tests.acd_support import live_call, production


@pytest.mark.asyncio
async def test_completing_calibration_leaves_the_finalized_review_alone(db, tenant_a, owner_a, manager_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    card = await create_scorecard(
        db,
        tenant_id=tenant_a.id,
        name=f"cal-{uuid.uuid4().hex[:8]}",
        sections=[
            {
                "name": "Opening",
                "weight": 1,
                "items": [{"name": "Greeting", "weight": 1, "min_score": 0, "max_score": 100, "required": True}],
            }
        ],
    )
    review, _ = await create_review(
        db, tenant_id=tenant_a.id, call_id=call.id, scorecard_id=card.id, actor_id=owner_a.id
    )
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
    finalized, _ = await finalize_review(
        db, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id
    )
    before = (finalized.status, finalized.overall_score, finalized.version, finalized.calculation_snapshot)
    session = await start(
        db,
        tenant_id=tenant_a.id,
        name="Monday calibration",
        created_by=owner_a.id,
        reference_review_id=review.id,
    )
    score = await compare(
        db,
        tenant_id=tenant_a.id,
        session_id=session.id,
        review_id=review.id,
        reviewer_id=manager_a.id,
        reference_score=8000,
        reviewer_score=6000,
        notes="stricter on the greeting",
    )
    assert score.delta == -2000
    with pytest.raises(CalibrationConflict):
        await compare(
            db,
            tenant_id=tenant_a.id,
            session_id=session.id,
            review_id=review.id,
            reviewer_id=manager_a.id,
            reference_score=8000,
            reviewer_score=6000,
        )
    completed, linked = await complete(
        db, tenant_id=tenant_a.id, session_id=session.id, notes="leave history"
    )
    assert completed.status == "completed"
    assert linked is not None
    await db.refresh(finalized)
    assert (finalized.status, finalized.overall_score, finalized.version, finalized.calculation_snapshot) == before
    summary = disagreement([score])
    assert summary["count"] == 1
    assert summary["mean_abs_delta"] == 2000
    again, _ = await complete(db, tenant_id=tenant_a.id, session_id=session.id)
    assert again.status == "completed"
    with pytest.raises(CalibrationConflict):
        await compare(
            db,
            tenant_id=tenant_a.id,
            session_id=session.id,
            review_id=review.id,
            reviewer_id=owner_a.id,
            reference_score=8000,
            reviewer_score=8000,
        )
