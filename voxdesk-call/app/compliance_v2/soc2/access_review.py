"""Access-review campaigns: create, attest, revoke, time out.

A campaign is a snapshot of who has access plus a deadline by which a named
reviewer must confirm or revoke each account. The rules:

* the population is fixed at creation — a user who joins mid-campaign belongs to
  the next one, which is what makes the campaign a reviewable artifact;
* only a reviewer on the campaign, or someone holding
  ``governance:approve``, may decide (the route layer enforces the permission;
  this module enforces the reviewer list);
* ``attest`` records the decision; ``revoke`` deactivates the account, revokes
  its live sessions and marks the decision — all in one transaction;
* when the deadline passes, undecided accounts are **auto-revoked** if the
  campaign says so, and the campaign completes. A campaign that quietly expires
  without acting is not an access review.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.db.compliance_v2_models import AccessReviewCampaign, AccessReviewDecision
from app.db.models import User

DECISION_PENDING = "pending"
DECISION_ATTEST = "attest"
DECISION_REVOKE = "revoke"
DECISION_NOT_APPLICABLE = "not_applicable"
DECISIONS = (DECISION_PENDING, DECISION_ATTEST, DECISION_REVOKE, DECISION_NOT_APPLICABLE)

CAMPAIGN_ACTIVE = "active"
CAMPAIGN_COMPLETED = "completed"
CAMPAIGN_CANCELLED = "cancelled"


class AccessReviewError(RuntimeError):
    """The campaign or decision cannot be applied as requested."""

    code = "access_review_error"


class CampaignNotFound(AccessReviewError):
    code = "campaign_not_found"


class DecisionConflict(AccessReviewError):
    code = "decision_conflict"


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


async def create_campaign(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    reviewer_ids: list[uuid.UUID],
    due_at: dt.datetime,
    created_by: uuid.UUID | None,
    scope_key: str = "all",
    auto_revoke: bool = True,
    user_ids: list[uuid.UUID] | None = None,
) -> AccessReviewCampaign:
    """Open a campaign and snapshot its population. Flushes."""
    if not reviewer_ids:
        raise AccessReviewError("a campaign needs at least one reviewer")
    if _utcnow(due_at) <= _utcnow():
        raise AccessReviewError("the review deadline must be in the future")
    active = await session.scalar(
        select(AccessReviewCampaign).where(
            AccessReviewCampaign.tenant_id == tenant_id,
            AccessReviewCampaign.scope_key == scope_key,
            AccessReviewCampaign.status == CAMPAIGN_ACTIVE,
        )
    )
    if active is not None:
        raise DecisionConflict("an active campaign already covers this scope")

    campaign = AccessReviewCampaign(
        tenant_id=tenant_id,
        name=name[:200],
        scope_key=scope_key[:120],
        reviewer_ids=[str(item) for item in reviewer_ids],
        status=CAMPAIGN_ACTIVE,
        auto_revoke=bool(auto_revoke),
        due_at=_utcnow(due_at),
        created_by=created_by,
    )
    session.add(campaign)
    await session.flush()

    query = select(User).where(User.tenant_id == tenant_id)
    if user_ids:
        query = query.where(User.id.in_(user_ids))
    population = list((await session.execute(query.order_by(User.created_at))).scalars())
    for user in population:
        session.add(
            AccessReviewDecision(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                user_id=user.id,
                role_at_review=str(getattr(user.role, "value", user.role)),
                decision=DECISION_PENDING,
            )
        )
    await session.flush()

    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=created_by,
        event_type="access_review_campaign_created",
        resource_type="access_review_campaign",
        resource_id=campaign.id,
        detail={
            "scope": campaign.scope_key,
            "reviewers": len(reviewer_ids),
            "population": len(population),
            "due_at": _utcnow(due_at).isoformat(),
        },
    )
    return campaign


async def get_campaign(
    session: AsyncSession, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> AccessReviewCampaign:
    row = await session.scalar(
        select(AccessReviewCampaign).where(
            AccessReviewCampaign.id == campaign_id,
            AccessReviewCampaign.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise CampaignNotFound("campaign not found")
    return row


async def list_decisions(
    session: AsyncSession, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> list[AccessReviewDecision]:
    return list(
        (
            await session.execute(
                select(AccessReviewDecision)
                .where(
                    AccessReviewDecision.tenant_id == tenant_id,
                    AccessReviewDecision.campaign_id == campaign_id,
                )
                .order_by(AccessReviewDecision.role_at_review, AccessReviewDecision.user_id)
            )
        ).scalars()
    )


def _is_reviewer(campaign: AccessReviewCampaign, actor: uuid.UUID | None) -> bool:
    return actor is not None and str(actor) in {str(item) for item in campaign.reviewer_ids or []}


async def _revoke_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User,
    actor: uuid.UUID | None,
    reason: str,
) -> None:
    """Deactivate an account and kill its live sessions."""
    user.is_active = False
    user.token_version = int(getattr(user, "token_version", 0) or 0) + 1

    from app.auth.identity.models import UserSession

    sessions = list(
        (
            await session.execute(
                select(UserSession).where(
                    UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
                )
            )
        ).scalars()
    )
    moment = _utcnow()
    for row in sessions:
        row.revoked_at = moment
        row.revoked_reason = "access_review_revoke"

    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor,
        target_user_id=user.id,
        event_type="access_review_revoked",
        resource_type="user",
        resource_id=user.id,
        detail={"reason": reason[:120], "sessions_revoked": len(sessions)},
    )


async def decide(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    decision: str,
    note: str = "",
    allow_non_reviewer: bool = False,
) -> AccessReviewDecision:
    """Record one decision, applying a revocation when requested. Flushes."""
    if decision not in (DECISION_ATTEST, DECISION_REVOKE, DECISION_NOT_APPLICABLE):
        raise AccessReviewError("decision must be attest, revoke or not_applicable")
    campaign = await get_campaign(session, tenant_id=tenant_id, campaign_id=campaign_id)
    if campaign.status != CAMPAIGN_ACTIVE:
        raise DecisionConflict("the campaign is no longer active")
    if not allow_non_reviewer and not _is_reviewer(campaign, actor_user_id):
        raise AccessReviewError("only a named reviewer may decide this campaign")

    row = await session.scalar(
        select(AccessReviewDecision).where(
            AccessReviewDecision.campaign_id == campaign.id,
            AccessReviewDecision.user_id == user_id,
            AccessReviewDecision.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise AccessReviewError("this user is not part of the campaign population")
    if row.decision != DECISION_PENDING:
        raise DecisionConflict(f"a decision ({row.decision}) is already recorded")

    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise AccessReviewError("user does not belong to this tenant")

    row.decision = decision
    row.decided_by = actor_user_id
    row.decided_at = _utcnow()
    row.note = (note or "")[:500]

    if decision == DECISION_REVOKE:
        await _revoke_user(
            session, tenant_id=tenant_id, user=user, actor=actor_user_id, reason="access_review"
        )

    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_user_id=user_id,
        event_type="access_review_decided",
        resource_type="access_review_campaign",
        resource_id=campaign.id,
        detail={"user_id": str(user_id), "decision": decision},
    )
    await session.flush()
    return row


async def tick(
    session: AsyncSession, *, now: dt.datetime | None = None, limit: int = 50
) -> dict:
    """Apply deadlines: auto-revoke undecided accounts, complete the campaign.

    Idempotent: a campaign that is already completed is not selected, and an
    account that was already auto-revoked is marked once.
    """
    moment = _utcnow(now)
    due = list(
        (
            await session.execute(
                select(AccessReviewCampaign)
                .where(
                    AccessReviewCampaign.status == CAMPAIGN_ACTIVE,
                    AccessReviewCampaign.due_at <= moment,
                )
                .order_by(AccessReviewCampaign.due_at)
                .limit(max(1, limit))
            )
        ).scalars()
    )
    revoked = 0
    completed = 0
    for campaign in due:
        pending = list(
            (
                await session.execute(
                    select(AccessReviewDecision).where(
                        AccessReviewDecision.campaign_id == campaign.id,
                        AccessReviewDecision.decision == DECISION_PENDING,
                    )
                )
            ).scalars()
        )
        for row in pending:
            user = await session.get(User, row.user_id)
            if user is None:
                row.decision = DECISION_NOT_APPLICABLE
                row.decided_at = moment
                row.note = "account no longer exists"
                continue
            if campaign.auto_revoke:
                await _revoke_user(
                    session,
                    tenant_id=campaign.tenant_id,
                    user=user,
                    actor=None,
                    reason="access_review_timeout",
                )
                row.decision = DECISION_REVOKE
                row.auto_revoked = True
                revoked += 1
            else:
                row.decision = DECISION_PENDING
            row.decided_at = moment
        campaign.status = CAMPAIGN_COMPLETED
        campaign.completed_at = moment
        completed += 1

        await record_event(
            session,
            tenant_id=campaign.tenant_id,
            actor_user_id=None,
            event_type="access_review_completed",
            resource_type="access_review_campaign",
            resource_id=campaign.id,
            detail={
                "auto_revoked": sum(1 for row in pending if row.auto_revoked),
                "due_at": _aware(campaign.due_at).isoformat() if campaign.due_at else "",
            },
        )
    await session.flush()
    return {"campaigns_completed": completed, "auto_revoked": revoked, "checked": len(due)}


def campaign_summary(
    campaign: AccessReviewCampaign, decisions: list[AccessReviewDecision]
) -> dict:
    counts: dict[str, int] = {}
    for row in decisions:
        counts[row.decision] = counts.get(row.decision, 0) + 1
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "scope": campaign.scope_key,
        "status": campaign.status,
        "auto_revoke": campaign.auto_revoke,
        "due_at": _aware(campaign.due_at).isoformat() if campaign.due_at else "",
        "reviewers": [str(item) for item in campaign.reviewer_ids or []],
        "population": len(decisions),
        "decision_counts": dict(sorted(counts.items())),
        "completed_at": _aware(campaign.completed_at).isoformat() if campaign.completed_at else "",
    }


__all__ = [
    "CAMPAIGN_ACTIVE",
    "CAMPAIGN_CANCELLED",
    "CAMPAIGN_COMPLETED",
    "DECISIONS",
    "DECISION_ATTEST",
    "DECISION_NOT_APPLICABLE",
    "DECISION_PENDING",
    "DECISION_REVOKE",
    "AccessReviewError",
    "CampaignNotFound",
    "DecisionConflict",
    "campaign_summary",
    "create_campaign",
    "decide",
    "get_campaign",
    "list_decisions",
    "tick",
]
