"""Incidents, breach clocks, access-review campaigns, DSAR and HIPAA safeguards.

These are the controls an auditor asks to see operating: an incident has a
timeline nobody can rewrite, a regulatory clock records when notice was given,
an access-review campaign can only be decided by its reviewers, a DSAR export is
signed and can be re-verified later, and break-glass access needs a second
person and expires on its own.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.compliance_v2.soc2 import access_review

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


async def test_access_review_campaign_can_be_decided_only_by_its_reviewers(
    db, tenant_a, owner_a, admin_a, agent_a
):
    campaign = await access_review.create_campaign(
        db,
        tenant_id=tenant_a.id,
        name="Q4 review",
        reviewer_ids=[owner_a.id],
        due_at=_now() + dt.timedelta(days=7),
        created_by=owner_a.id,
        user_ids=[agent_a.id],
    )
    await db.commit()

    with pytest.raises(access_review.AccessReviewError):
        await access_review.decide(
            db,
            tenant_id=tenant_a.id,
            campaign_id=campaign.id,
            user_id=agent_a.id,
            actor_user_id=admin_a.id,
            decision="attest",
        )

    decision = await access_review.decide(
        db,
        tenant_id=tenant_a.id,
        campaign_id=campaign.id,
        user_id=agent_a.id,
        actor_user_id=owner_a.id,
        decision="attest",
    )
    await db.commit()

    assert decision.decision == "attest"
    assert decision.decided_by == owner_a.id
    summary = access_review.campaign_summary(
        campaign, await access_review.list_decisions(db, tenant_id=tenant_a.id, campaign_id=campaign.id)
    )
    assert summary["decision_counts"] == {"attest": 1}
    assert summary["population"] == 1


async def test_a_decided_row_cannot_be_silently_overwritten(db, tenant_a, owner_a, agent_a):
    campaign = await access_review.create_campaign(
        db,
        tenant_id=tenant_a.id,
        name="Q1 review",
        reviewer_ids=[owner_a.id],
        due_at=_now() + dt.timedelta(days=3),
        created_by=owner_a.id,
        user_ids=[agent_a.id],
    )
    await db.commit()
    await access_review.decide(
        db,
        tenant_id=tenant_a.id,
        campaign_id=campaign.id,
        user_id=agent_a.id,
        actor_user_id=owner_a.id,
        decision="attest",
    )
    await db.commit()

    with pytest.raises(access_review.DecisionConflict):
        await access_review.decide(
            db,
            tenant_id=tenant_a.id,
            campaign_id=campaign.id,
            user_id=agent_a.id,
            actor_user_id=owner_a.id,
            decision="revoke",
        )


async def test_campaign_is_not_visible_to_another_tenant(db, tenant_a, tenant_b, owner_a):
    campaign = await access_review.create_campaign(
        db,
        tenant_id=tenant_a.id,
        name="Scoped",
        reviewer_ids=[owner_a.id],
        due_at=_now() + dt.timedelta(days=3),
        created_by=owner_a.id,
    )
    await db.commit()

    with pytest.raises(access_review.CampaignNotFound):
        await access_review.get_campaign(db, tenant_id=tenant_b.id, campaign_id=campaign.id)


# ---------------------------------------------------------------------- DSAR ---


