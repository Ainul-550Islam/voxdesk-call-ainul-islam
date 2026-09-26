"""Assignment, finalization, sampling, coaching and auto-review do not double-apply."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, UserRole
from app.qa.auto_review import enqueue
from app.qa.coaching import create_signal, transition
from app.qa.exceptions import InvalidTransition, StaleReview
from app.qa.models import AutoReviewRun, QAReview, SampleSelection
from app.qa.repository import bump_review, get_review, review_items
from app.qa.rubrics import create_scorecard
from app.qa.sampling import create_rule, run_rule
from app.qa.service import assign_review, create_review, finalize_review, reopen_review, save_scores, submit_review
from tests.acd_support import live_call, production
from tests.conftest import make_tenant, make_user


SECTIONS = [
    {
        "name": "Opening",
        "weight": 1,
        "items": [{"name": "Greeting", "weight": 1, "min_score": 0, "max_score": 100, "required": True}],
    }
]


async def _calculated(db, tenant, actor):
    env = await production(db, tenant)
    call = await live_call(db, tenant, env)
    card = await create_scorecard(
        db, tenant_id=tenant.id, name=f"race-{uuid.uuid4().hex[:8]}", sections=SECTIONS
    )
    review, _ = await create_review(
        db, tenant_id=tenant.id, call_id=call.id, scorecard_id=card.id, actor_id=actor.id
    )
    await assign_review(
        db,
        tenant_id=tenant.id,
        review_id=review.id,
        assignee_id=actor.id,
        actor_id=actor.id,
    )
    held = await review_items(db, tenant.id, review.id)
    await save_scores(
        db,
        tenant_id=tenant.id,
        review_id=review.id,
        actor_id=actor.id,
        scores=[{"item_id": held[0].item_id, "score": 80}],
    )
    await submit_review(db, tenant_id=tenant.id, review_id=review.id, actor_id=actor.id)
    await db.commit()
    return review


@pytest.mark.asyncio
async def test_two_sessions_cannot_both_finalize_or_both_assign():
    path = Path("/tmp/qa-cas.sqlite")
    if path.exists():
        path.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        tenant = await make_tenant(db, "CAS")
        owner = await make_user(db, tenant, UserRole.OWNER)
        manager = await make_user(db, tenant, UserRole.MANAGER)
        review = await _calculated(db, tenant, owner)
        env = await production(db, tenant)
        call = await live_call(db, tenant, env)
        card = await create_scorecard(
            db, tenant_id=tenant.id, name=f"assign-{uuid.uuid4().hex[:6]}", sections=SECTIONS
        )
        fresh, _ = await create_review(
            db, tenant_id=tenant.id, call_id=call.id, scorecard_id=card.id, actor_id=owner.id
        )
        await db.commit()
        review_id, fresh_id = review.id, fresh.id
        tenant_id, owner_id, manager_id = tenant.id, owner.id, manager.id
    async with maker() as left, maker() as right:
        await get_review(left, tenant_id, review_id)
        stale = await get_review(right, tenant_id, review_id)
        expected = stale.version
        _, outcome = await finalize_review(
            left, tenant_id=tenant_id, review_id=review_id, actor_id=owner_id
        )
        await left.commit()
        assert outcome == "applied"
        with pytest.raises(StaleReview):
            await bump_review(
                right,
                stale,
                expected,
                {"status": "finalized", "open_key": None, "finalized_by": manager_id},
            )
    async with maker() as left, maker() as right:
        await get_review(left, tenant_id, fresh_id)
        stale = await get_review(right, tenant_id, fresh_id)
        expected = stale.version
        _, assigned = await assign_review(
            left,
            tenant_id=tenant_id,
            review_id=fresh_id,
            assignee_id=manager_id,
            actor_id=owner_id,
        )
        await left.commit()
        assert assigned == "applied"
        with pytest.raises(StaleReview):
            await bump_review(
                right,
                stale,
                expected,
                {"status": "assigned", "assignee_id": owner_id},
            )
    async with maker() as db:
        stored = await db.get(QAReview, review_id)
        assigned_row = await db.get(QAReview, fresh_id)
        assert stored.status == "finalized"
        assert stored.finalized_by == owner_id
        assert assigned_row.assignee_id == manager_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_reopen_loses_to_a_finalize_that_already_committed(db, tenant_a, owner_a):
    review = await _calculated(db, tenant_a, owner_a)
    maker = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    async with maker() as finisher, maker() as reopener:
        await get_review(finisher, tenant_a.id, review.id)
        loaded = await get_review(reopener, tenant_a.id, review.id)
        assert loaded.status == "calculated"
        await finalize_review(
            finisher, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id
        )
        await finisher.commit()
        with pytest.raises((StaleReview, InvalidTransition)):
            await reopen_review(
                reopener, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id
            )
    await db.refresh(review)
    assert review.status == "finalized"
    reopened, outcome = await reopen_review(
        db, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id
    )
    assert outcome == "applied"
    duplicate, again = await reopen_review(
        db, tenant_id=tenant_a.id, review_id=review.id, actor_id=owner_a.id
    )
    assert again == "duplicate"
    assert duplicate.version == reopened.version
    assert reopened.prior_snapshots


@pytest.mark.asyncio
async def test_sampling_and_auto_review_inserts_are_unique(db, tenant_a, owner_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    rule = await create_rule(
        db, tenant_id=tenant_a.id, name=f"once-{uuid.uuid4().hex[:6]}", kind="percentage", percent=100
    )
    review, _ = await create_review(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        scorecard_id=(
            await create_scorecard(db, tenant_id=tenant_a.id, name=f"s-{uuid.uuid4().hex[:6]}", sections=SECTIONS)
        ).id,
        actor_id=owner_a.id,
    )
    await db.commit()
    await run_rule(db, tenant_id=tenant_a.id, rule_id=rule.id, window_key="w", call_ids=[call.id])
    await run_rule(db, tenant_id=tenant_a.id, rule_id=rule.id, window_key="w", call_ids=[call.id])
    await db.commit()
    count = await db.scalar(
        select(func.count()).select_from(SampleSelection).where(SampleSelection.rule_id == rule.id)
    )
    assert int(count) == 1
    first, queued = await enqueue(db, tenant_id=tenant_a.id, review=review)
    second, duplicate = await enqueue(db, tenant_id=tenant_a.id, review=review)
    await db.commit()
    assert queued == "queued"
    assert duplicate == "duplicate"
    assert second.id == first.id
    runs = await db.scalar(
        select(func.count()).select_from(AutoReviewRun).where(AutoReviewRun.review_id == review.id)
    )
    assert int(runs) == 1


@pytest.mark.asyncio
async def test_concurrent_coaching_acknowledgement_and_finalize():
    path = Path("/tmp/qa-race.sqlite")
    if path.exists():
        path.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        tenant = await make_tenant(db, "Race QA")
        owner = await make_user(db, tenant, UserRole.OWNER)
        agent = await make_user(db, tenant, UserRole.AGENT)
        env = await production(db, tenant)
        call = await live_call(db, tenant, env)
        signal = await create_signal(
            db,
            tenant_id=tenant.id,
            call_id=call.id,
            agent_user_id=agent.id,
            category="opening",
            recommendation="Pause.",
        )
        await transition(db, signal, "assigned", actor_id=owner.id, supervisor=True)
        review = await _calculated(db, tenant, owner)
        await db.commit()
        signal_id = signal.id
        tenant_id = tenant.id
        agent_id = agent.id
        review_id = review.id
        owner_id = owner.id

    async def ack():
        async with maker() as session:
            row = await session.get(type(signal), signal_id)
            try:
                _, outcome = await transition(session, row, "acknowledged", actor_id=agent_id)
                await session.commit()
                return outcome
            except StaleReview:
                await session.rollback()
                return "stale"

    async def finish():
        async with maker() as session:
            try:
                _, outcome = await finalize_review(
                    session, tenant_id=tenant_id, review_id=review_id, actor_id=owner_id
                )
                await session.commit()
                return outcome
            except (StaleReview, InvalidTransition):
                await session.rollback()
                return "stale"

    ack_outcomes = await asyncio.gather(ack(), ack())
    finish_outcomes = await asyncio.gather(finish(), finish())
    assert ack_outcomes.count("applied") == 1
    assert finish_outcomes.count("applied") == 1
    async with maker() as db:
        stored = await db.get(QAReview, review_id)
        assert stored.status == "finalized"
    await engine.dispose()
