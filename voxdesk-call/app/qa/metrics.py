"""Tenant-scoped QA aggregates.

Most queries stay in SQL and are bounded. Review turnaround is averaged in
Python from the stored timestamps so the same code runs on SQLite and
PostgreSQL. ``julianday`` and ``extract(epoch)`` are not used.
"""

from __future__ import annotations

import uuid
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.models import (
    AutoReviewRun,
    CalibrationScore,
    CoachingSignal,
    ComplianceFinding,
    QAReview,
    QAReviewItem,
)


def _int(value) -> int:
    return int(value or 0)


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _turnaround_seconds(session: AsyncSession, tenant_id: uuid.UUID) -> int | None:
    """Mean finalized-minus-created seconds. None when nothing is finalized."""
    rows = await session.execute(
        select(QAReview.created_at, QAReview.finalized_at).where(
            QAReview.tenant_id == tenant_id,
            QAReview.status == "finalized",
            QAReview.finalized_at.is_not(None),
        )
    )
    samples: list[float] = []
    for created, finalized in rows.all():
        start = _aware(created)
        end = _aware(finalized)
        if start is None or end is None:
            continue
        samples.append((end - start).total_seconds())
    if not samples:
        return None
    return int(sum(samples) / len(samples))


async def snapshot(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
    review_count = _int(
        await session.scalar(select(func.count()).select_from(QAReview).where(QAReview.tenant_id == tenant_id))
    )
    completed = _int(
        await session.scalar(
            select(func.count()).select_from(QAReview).where(
                QAReview.tenant_id == tenant_id,
                QAReview.status == "finalized",
            )
        )
    )
    passed = _int(
        await session.scalar(
            select(func.count()).select_from(QAReview).where(
                QAReview.tenant_id == tenant_id,
                QAReview.status == "finalized",
                QAReview.passed.is_(True),
            )
        )
    )
    average = await session.scalar(
        select(func.avg(QAReview.overall_score)).where(
            QAReview.tenant_id == tenant_id,
            QAReview.status == "finalized",
            QAReview.overall_score.is_not(None),
        )
    )
    buckets = {"0-2000": 0, "2000-4000": 0, "4000-6000": 0, "6000-8000": 0, "8000-10000": 0}
    rows = (
        await session.execute(
            select(QAReview.overall_score).where(
                QAReview.tenant_id == tenant_id,
                QAReview.status == "finalized",
                QAReview.overall_score.is_not(None),
            ).limit(5000)
        )
    ).all()
    for (score,) in rows:
        if score < 2000:
            buckets["0-2000"] += 1
        elif score < 4000:
            buckets["2000-4000"] += 1
        elif score < 6000:
            buckets["4000-6000"] += 1
        elif score < 8000:
            buckets["6000-8000"] += 1
        else:
            buckets["8000-10000"] += 1
    agents = (
        await session.execute(
            select(QAReview.assignee_id, func.count())
            .where(
                QAReview.tenant_id == tenant_id,
                QAReview.status == "finalized",
                QAReview.assignee_id.is_not(None),
            )
            .group_by(QAReview.assignee_id)
            .limit(100)
        )
    ).all()
    queues = (
        await session.execute(
            select(QAReview.queue_id, func.count())
            .where(
                QAReview.tenant_id == tenant_id,
                QAReview.status == "finalized",
                QAReview.queue_id.is_not(None),
            )
            .group_by(QAReview.queue_id)
            .limit(100)
        )
    ).all()
    findings = _int(
        await session.scalar(
            select(func.count()).select_from(ComplianceFinding).where(ComplianceFinding.tenant_id == tenant_id)
        )
    )
    high = _int(
        await session.scalar(
            select(func.count()).select_from(ComplianceFinding).where(
                ComplianceFinding.tenant_id == tenant_id,
                ComplianceFinding.severity.in_(("high", "critical")),
            )
        )
    )
    coaching = _int(
        await session.scalar(
            select(func.count()).select_from(CoachingSignal).where(CoachingSignal.tenant_id == tenant_id)
        )
    )
    auto = _int(
        await session.scalar(
            select(func.count()).select_from(AutoReviewRun).where(AutoReviewRun.tenant_id == tenant_id)
        )
    )
    overrides = _int(
        await session.scalar(
            select(func.count()).select_from(QAReviewItem).where(
                QAReviewItem.tenant_id == tenant_id,
                QAReviewItem.accepted_source == "human",
                QAReviewItem.ai_score.is_not(None),
                QAReviewItem.override_reason != "",
            )
        )
    )
    disagreement = await session.scalar(
        select(func.avg(func.abs(CalibrationScore.delta))).where(CalibrationScore.tenant_id == tenant_id)
    )
    seconds = await _turnaround_seconds(session, tenant_id)
    return {
        "review_count": review_count,
        "completed_review_count": completed,
        "average_qa_score": None if average is None else int(average),
        "pass_rate": None if completed == 0 else passed * 10000 // completed,
        "score_distribution": buckets,
        "agent_review_coverage": [
            {"assignee_id": str(agent_id), "reviews": int(count)} for agent_id, count in agents
        ],
        "queue_review_coverage": [
            {"queue_id": str(queue_id), "reviews": int(count)} for queue_id, count in queues
        ],
        "compliance_finding_count": findings,
        "high_severity_finding_count": high,
        "coaching_signal_count": coaching,
        "ai_auto_review_count": auto,
        "human_override_count": overrides,
        "calibration_disagreement": None if disagreement is None else int(disagreement),
        "review_turnaround_seconds": seconds,
    }
