"""Reviewer comparison. Completing a session does not rewrite a finalized review."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.exceptions import CalibrationConflict, InvalidScore
from app.qa.models import CalibrationScore, CalibrationSession, QAReview
from app.qa.repository import calibration_scores, get_calibration, get_review


async def start(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    created_by: uuid.UUID | None,
    reference_review_id: uuid.UUID | None = None,
) -> CalibrationSession:
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidScore("Calibration name is required")
    if reference_review_id is not None:
        await get_review(session, tenant_id, reference_review_id)
    row = CalibrationSession(
        tenant_id=tenant_id,
        name=cleaned[:80],
        created_by=created_by,
        reference_review_id=reference_review_id,
    )
    session.add(row)
    await session.flush()
    return row


async def compare(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    review_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    reference_score: int,
    reviewer_score: int,
    item_key: str = "overall",
    notes: str = "",
) -> CalibrationScore:
    held = await get_calibration(session, tenant_id, session_id)
    if held.status != "open":
        raise CalibrationConflict("Calibration session is not open")
    review = await get_review(session, tenant_id, review_id)
    if not isinstance(reference_score, int) or not isinstance(reviewer_score, int):
        raise InvalidScore("Calibration scores must be integers")
    if isinstance(reference_score, bool) or isinstance(reviewer_score, bool):
        raise InvalidScore("Calibration scores must be integers")
    row = CalibrationScore(
        tenant_id=tenant_id,
        session_id=held.id,
        review_id=review.id,
        reviewer_id=reviewer_id,
        item_key=(item_key or "overall")[:40],
        reference_score=reference_score,
        reviewer_score=reviewer_score,
        delta=reviewer_score - reference_score,
        notes=(notes or "")[:300],
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise CalibrationConflict("This reviewer already scored this item") from exc
    return row


async def complete(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    notes: str = "",
) -> tuple[CalibrationSession, QAReview | None]:
    held = await get_calibration(session, tenant_id, session_id)
    if held.status == "completed":
        review = None
        if held.reference_review_id is not None:
            review = await get_review(session, tenant_id, held.reference_review_id)
        return held, review
    if held.status != "open":
        raise CalibrationConflict("Calibration session cannot be completed")
    scores = await calibration_scores(session, tenant_id, held.id)
    if not scores:
        raise CalibrationConflict("Calibration needs at least one comparison")
    before = None
    if held.reference_review_id is not None:
        before = await get_review(session, tenant_id, held.reference_review_id)
        snapshot = (before.status, before.overall_score, before.version)
    else:
        snapshot = None
    held.status = "completed"
    held.resolution_notes = (notes or "")[:500]
    from datetime import datetime, timezone

    held.completed_at = datetime.now(timezone.utc)
    await session.flush()
    if before is not None and snapshot is not None:
        await session.refresh(before)
        if (before.status, before.overall_score, before.version) != snapshot:
            raise CalibrationConflict("Calibration changed a historical review")
    return held, before


def disagreement(scores: list[CalibrationScore]) -> dict:
    if not scores:
        return {"count": 0, "mean_abs_delta": None, "items": []}
    total = sum(abs(row.delta) for row in scores)
    return {
        "count": len(scores),
        "mean_abs_delta": total // len(scores),
        "items": [row.as_dict() for row in scores],
    }
