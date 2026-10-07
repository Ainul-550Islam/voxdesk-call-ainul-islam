"""Explainable, versioned lead score. Deterministic. Does not change status."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Appointment, Lead, LeadStatus
from app.leads.models import LeadScoreSnapshot

SCORE_VERSION = "lead-score-v1"
_MAX = 100


async def explain(session: AsyncSession, lead: Lead) -> dict:
    """Same lead state always yields the same score. No clock and no randomness."""
    factors: list[dict] = []

    def add(code: str, points: int, detail: str) -> None:
        factors.append({"code": code, "points": points, "detail": detail})

    add("phone_present", 10 if (lead.phone or "").strip() else 0, "A dialable phone is present")
    add("email_present", 5 if (lead.email or "").strip() else 0, "An email is present")
    add("company_present", 5 if (lead.company or "").strip() else 0, "A company is present")
    attempt_points = min(int(lead.attempts or 0), 5) * 3
    add("attempts", attempt_points, "Bounded engagement from recorded attempts")
    status = lead.status
    add(
        "qualified",
        25 if status is LeadStatus.QUALIFIED else 0,
        "Stored status is qualified",
    )
    add(
        "called",
        10 if status is LeadStatus.CALLED else 0,
        "Stored status is called",
    )
    appointment_count = (
        await session.execute(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.tenant_id == lead.tenant_id,
                Appointment.environment_id == lead.environment_id,
                Appointment.customer_phone == lead.phone,
            )
        )
    ).scalar_one()
    add(
        "appointment",
        20 if int(appointment_count or 0) else 0,
        "An appointment exists for this phone in the same environment",
    )
    blocked = status is LeadStatus.DNC
    add(
        "do_not_call",
        0,
        "Do-not-call blocks calling; the score does not clear it" if blocked else "Not do-not-call",
    )
    total = max(0, min(_MAX, sum(item["points"] for item in factors)))
    return {
        "version": SCORE_VERSION,
        "score": total,
        "factors": factors,
        "blocks_calling": blocked,
    }


async def snapshot(session: AsyncSession, lead: Lead) -> LeadScoreSnapshot:
    """Persist an explanation and mirror the total onto ``Lead.score``.

    Status, including do-not-call, is not written here.
    """
    explained = await explain(session, lead)
    lead.score = explained["score"]
    row = LeadScoreSnapshot(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        version=explained["version"],
        score=explained["score"],
        factors=explained["factors"],
    )
    session.add(row)
    await session.flush()
    return row
