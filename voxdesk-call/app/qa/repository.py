"""Tenant-scoped QA reads and writes.

A row whose tenant does not match the server context is missing. The client
never supplies the tenant used in these filters.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.exceptions import StaleReview
from app.qa.models import (
    AutoReviewRun,
    CalibrationScore,
    CalibrationSession,
    CoachingSignal,
    ComplianceFinding,
    CompliancePolicy,
    QAEvidence,
    QAFinding,
    QAReview,
    QAReviewItem,
    SampleSelection,
    SamplingRule,
    Scorecard,
    ScorecardItem,
    ScorecardSection,
    SentimentResult,
    TopicResult,
)
from app.tenancy.isolation import NotFound


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_scorecard(session: AsyncSession, tenant_id: uuid.UUID, scorecard_id: uuid.UUID) -> Scorecard:
    row = await session.get(Scorecard, scorecard_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def list_scorecards(session: AsyncSession, tenant_id: uuid.UUID) -> list[Scorecard]:
    return list(
        (
            await session.execute(
                select(Scorecard).where(Scorecard.tenant_id == tenant_id).order_by(Scorecard.name)
            )
        ).scalars()
    )


async def sections_for(session: AsyncSession, tenant_id: uuid.UUID, scorecard_id: uuid.UUID) -> list[ScorecardSection]:
    return list(
        (
            await session.execute(
                select(ScorecardSection)
                .where(
                    ScorecardSection.tenant_id == tenant_id,
                    ScorecardSection.scorecard_id == scorecard_id,
                )
                .order_by(ScorecardSection.position)
            )
        ).scalars()
    )


async def items_for(session: AsyncSession, tenant_id: uuid.UUID, scorecard_id: uuid.UUID) -> list[ScorecardItem]:
    return list(
        (
            await session.execute(
                select(ScorecardItem)
                .where(
                    ScorecardItem.tenant_id == tenant_id,
                    ScorecardItem.scorecard_id == scorecard_id,
                )
                .order_by(ScorecardItem.position)
            )
        ).scalars()
    )


async def get_review(session: AsyncSession, tenant_id: uuid.UUID, review_id: uuid.UUID) -> QAReview:
    row = await session.get(QAReview, review_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def list_reviews(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[QAReview]:
    bounded = min(max(limit, 1), 100)
    return list(
        (
            await session.execute(
                select(QAReview)
                .where(QAReview.tenant_id == tenant_id)
                .order_by(QAReview.created_at.desc())
                .limit(bounded)
                .offset(max(offset, 0))
            )
        ).scalars()
    )


async def review_items(session: AsyncSession, tenant_id: uuid.UUID, review_id: uuid.UUID) -> list[QAReviewItem]:
    return list(
        (
            await session.execute(
                select(QAReviewItem).where(
                    QAReviewItem.tenant_id == tenant_id,
                    QAReviewItem.review_id == review_id,
                )
            )
        ).scalars()
    )


async def bump_review(session: AsyncSession, review: QAReview, expected: int, values: dict) -> QAReview:
    values = dict(values)
    values["version"] = expected + 1
    values["updated_at"] = _now()
    result = await session.execute(
        update(QAReview)
        .where(QAReview.id == review.id, QAReview.version == expected, QAReview.tenant_id == review.tenant_id)
        .values(**values)
    )
    if result.rowcount != 1:
        raise StaleReview("Review version changed")
    await session.flush()
    await session.refresh(review)
    return review


async def evidence_for_review(
    session: AsyncSession, tenant_id: uuid.UUID, review_id: uuid.UUID
) -> list[QAEvidence]:
    return list(
        (
            await session.execute(
                select(QAEvidence).where(
                    QAEvidence.tenant_id == tenant_id,
                    QAEvidence.review_id == review_id,
                )
            )
        ).scalars()
    )


async def evidence_for_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[QAEvidence]:
    return list(
        (
            await session.execute(
                select(QAEvidence).where(
                    QAEvidence.tenant_id == tenant_id,
                    QAEvidence.call_id == call_id,
                )
            )
        ).scalars()
    )


async def get_rule(session: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> SamplingRule:
    row = await session.get(SamplingRule, rule_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def selections_for(
    session: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID, window_key: str
) -> list[SampleSelection]:
    return list(
        (
            await session.execute(
                select(SampleSelection).where(
                    SampleSelection.tenant_id == tenant_id,
                    SampleSelection.rule_id == rule_id,
                    SampleSelection.window_key == window_key,
                )
            )
        ).scalars()
    )


async def get_run(session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID) -> AutoReviewRun:
    row = await session.get(AutoReviewRun, run_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def run_for_key(
    session: AsyncSession, tenant_id: uuid.UUID, key: str
) -> AutoReviewRun | None:
    return (
        await session.execute(
            select(AutoReviewRun).where(
                AutoReviewRun.tenant_id == tenant_id,
                AutoReviewRun.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


async def sentiment_for_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[SentimentResult]:
    return list(
        (
            await session.execute(
                select(SentimentResult).where(
                    SentimentResult.tenant_id == tenant_id,
                    SentimentResult.call_id == call_id,
                )
            )
        ).scalars()
    )


async def topics_for_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[TopicResult]:
    return list(
        (
            await session.execute(
                select(TopicResult).where(
                    TopicResult.tenant_id == tenant_id,
                    TopicResult.call_id == call_id,
                )
            )
        ).scalars()
    )


async def compliance_for_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[ComplianceFinding]:
    return list(
        (
            await session.execute(
                select(ComplianceFinding).where(
                    ComplianceFinding.tenant_id == tenant_id,
                    ComplianceFinding.call_id == call_id,
                )
            )
        ).scalars()
    )


async def get_finding(
    session: AsyncSession, tenant_id: uuid.UUID, finding_id: uuid.UUID
) -> ComplianceFinding:
    row = await session.get(ComplianceFinding, finding_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def get_policy(session: AsyncSession, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> CompliancePolicy:
    row = await session.get(CompliancePolicy, policy_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def coaching_for_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[CoachingSignal]:
    return list(
        (
            await session.execute(
                select(CoachingSignal).where(
                    CoachingSignal.tenant_id == tenant_id,
                    CoachingSignal.call_id == call_id,
                )
            )
        ).scalars()
    )


async def get_coaching(session: AsyncSession, tenant_id: uuid.UUID, signal_id: uuid.UUID) -> CoachingSignal:
    row = await session.get(CoachingSignal, signal_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def get_calibration(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> CalibrationSession:
    row = await session.get(CalibrationSession, session_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def calibration_scores(
    session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> list[CalibrationScore]:
    return list(
        (
            await session.execute(
                select(CalibrationScore).where(
                    CalibrationScore.tenant_id == tenant_id,
                    CalibrationScore.session_id == session_id,
                )
            )
        ).scalars()
    )


async def count_finalized(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.count()).select_from(QAReview).where(
            QAReview.tenant_id == tenant_id,
            QAReview.status == "finalized",
        )
    )
    return int(value or 0)


async def findings_for_review(
    session: AsyncSession, tenant_id: uuid.UUID, review_id: uuid.UUID
) -> list[QAFinding]:
    return list(
        (
            await session.execute(
                select(QAFinding).where(
                    QAFinding.tenant_id == tenant_id,
                    QAFinding.review_id == review_id,
                )
            )
        ).scalars()
    )
