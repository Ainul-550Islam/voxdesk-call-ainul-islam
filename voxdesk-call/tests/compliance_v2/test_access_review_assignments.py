"""Who is assigned to a review campaign, and what that assignment may do.

The campaign snapshot and the reviewer list are the two halves of the control:
the population is frozen when the campaign opens, so an account created (or
promoted) afterwards cannot slip through a review, and only a named reviewer may
record a decision. Both are enforced in the service layer, which is where an
operator's UI and the API both land.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.compliance_v2.soc2 import access_review
from app.db.models import AuditLog, User, UserRole

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


async def _campaign(db, tenant, *, reviewers, population, **kwargs):
    campaign = await access_review.create_campaign(
        db,
        tenant_id=tenant.id,
        name=kwargs.pop("name", "Q4 review"),
        reviewer_ids=[user.id for user in reviewers],
        due_at=kwargs.pop("due_at", _now() + dt.timedelta(days=7)),
        created_by=kwargs.pop("created_by", reviewers[0].id),
        user_ids=[user.id for user in population],
        **kwargs,
    )
    await db.flush()
    return campaign


async def test_a_campaign_without_a_reviewer_is_refused(db, tenant_a, owner_a):
    with pytest.raises(access_review.AccessReviewError):
        await access_review.create_campaign(
            db,
            tenant_id=tenant_a.id,
            name="No reviewer",
            reviewer_ids=[],
            due_at=_now() + dt.timedelta(days=7),
            created_by=owner_a.id,
        )


async def test_the_deadline_must_be_in_the_future(db, tenant_a, owner_a):
    with pytest.raises(access_review.AccessReviewError):
        await access_review.create_campaign(
            db,
            tenant_id=tenant_a.id,
            name="Yesterday",
            reviewer_ids=[owner_a.id],
            due_at=_now() - dt.timedelta(minutes=1),
            created_by=owner_a.id,
        )


async def test_opening_a_campaign_snapshots_the_population(
    db, tenant_a, owner_a, admin_a, agent_a
):
    campaign = await _campaign(
        db, tenant_a, reviewers=[owner_a], population=[owner_a, admin_a, agent_a]
    )
    await db.commit()

    decisions = await access_review.list_decisions(
        db, tenant_id=tenant_a.id, campaign_id=campaign.id
    )

    assert {row.user_id for row in decisions} == {owner_a.id, admin_a.id, agent_a.id}
    assert all(row.decision == access_review.DECISION_PENDING for row in decisions)
    assert all(row.role_at_review for row in decisions)


async def test_an_account_created_after_the_campaign_opens_is_not_in_it(
    db, tenant_a, owner_a, agent_a
):
    campaign = await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a])
    await db.commit()

    late = User(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        email=f"late-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=agent_a.password_hash,
        full_name="Late Joiner",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(late)
    await db.commit()

    decisions = await access_review.list_decisions(
        db, tenant_id=tenant_a.id, campaign_id=campaign.id
    )

    assert late.id not in {row.user_id for row in decisions}


async def test_a_second_active_campaign_for_the_same_scope_is_refused(
    db, tenant_a, owner_a, agent_a
):
    await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a])
    await db.commit()

    with pytest.raises(access_review.DecisionConflict):
        await _campaign(
            db, tenant_a, reviewers=[owner_a], population=[agent_a], name="Duplicate"
        )


async def test_a_different_scope_may_have_its_own_campaign(
    db, tenant_a, owner_a, agent_a
):
    await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a], scope_key="a")
    await db.commit()

    second = await _campaign(
        db, tenant_a, reviewers=[owner_a], population=[agent_a], scope_key="b"
    )
    await db.commit()

    assert second.scope_key == "b"


async def test_a_named_reviewer_may_decide(db, tenant_a, owner_a, admin_a, agent_a):
    campaign = await _campaign(
        db, tenant_a, reviewers=[owner_a, admin_a], population=[agent_a]
    )
    await db.commit()

    row = await access_review.decide(
        db,
        tenant_id=tenant_a.id,
        campaign_id=campaign.id,
        user_id=agent_a.id,
        actor_user_id=admin_a.id,
        decision=access_review.DECISION_ATTEST,
        note="manager confirmed",
    )
    await db.commit()

    assert row.decision == access_review.DECISION_ATTEST
    assert row.decided_by == admin_a.id
    assert row.note == "manager confirmed"


async def test_a_user_outside_the_reviewer_list_cannot_decide(
    db, tenant_a, owner_a, agent_a
):
    campaign = await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a])
    await db.commit()

    with pytest.raises(access_review.AccessReviewError):
        await access_review.decide(
            db,
            tenant_id=tenant_a.id,
            campaign_id=campaign.id,
            user_id=agent_a.id,
            actor_user_id=agent_a.id,
            decision=access_review.DECISION_ATTEST,
        )


async def test_a_decision_outside_the_population_is_refused(
    db, tenant_a, owner_a, admin_a, agent_a
):
    campaign = await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a])
    await db.commit()

    with pytest.raises(access_review.AccessReviewError):
        await access_review.decide(
            db,
            tenant_id=tenant_a.id,
            campaign_id=campaign.id,
            user_id=admin_a.id,
            actor_user_id=owner_a.id,
            decision=access_review.DECISION_ATTEST,
        )


async def test_a_revocation_deactivates_the_account_and_is_audited(
    db, tenant_a, owner_a, agent_a
):
    campaign = await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a])
    await db.commit()

    await access_review.decide(
        db,
        tenant_id=tenant_a.id,
        campaign_id=campaign.id,
        user_id=agent_a.id,
        actor_user_id=owner_a.id,
        decision=access_review.DECISION_REVOKE,
        note="left the team",
    )
    await db.commit()

    refreshed = await db.get(User, agent_a.id)
    assert refreshed is not None
    assert refreshed.is_active is False

    events = list(
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                    AuditLog.event_type == "access_review_decided",
                )
            )
        ).scalars()
    )
    assert any(event.target_user_id == agent_a.id for event in events)


async def test_the_decision_is_recorded_against_the_reviewer_not_the_target(
    db, tenant_a, owner_a, agent_a
):
    campaign = await _campaign(db, tenant_a, reviewers=[owner_a], population=[agent_a])
    await db.commit()

    row = await access_review.decide(
        db,
        tenant_id=tenant_a.id,
        campaign_id=campaign.id,
        user_id=agent_a.id,
        actor_user_id=owner_a.id,
        decision=access_review.DECISION_NOT_APPLICABLE,
    )
    await db.commit()

    assert row.decided_by == owner_a.id
    assert row.user_id == agent_a.id
