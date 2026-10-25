"""Two-second human-answer fallback with an audible safe-harbor message."""
from __future__ import annotations

from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.occupancy import release_agent
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.twiml import safe_harbor_twiml
from app.db.contact_center_v2_models import AgentReservation, DialerAttempt, DialerPolicy


async def handle_abandoned_call(
    session: AsyncSession,
    *,
    attempt: DialerAttempt,
    policy: DialerPolicy,
) -> PlainTextResponse:
    """Count a human abandonment, release its reservation, and play safe harbor.

    Machine answers never reach this function. The caller invokes it only after
    the bounded agent-join deadline or when no eligible agent/device remains.
    Committing before returning provider instructions makes the accounting and
    the audible fallback one durable decision; a silent hangup is never emitted.
    """
    moment = utcnow()
    attempt.answered_at = attempt.answered_at or moment
    attempt.abandoned_at = attempt.abandoned_at or moment
    attempt.abandonment_counted = True
    attempt.safe_harbor_requested_at = attempt.safe_harbor_requested_at or moment
    attempt.outcome = "answered"
    attempt.updated_at = moment
    reservation = (
        await session.execute(
            select(AgentReservation).where(
                AgentReservation.tenant_id == attempt.tenant_id,
                AgentReservation.kind == "dialer",
                AgentReservation.resource_id == attempt.id,
                AgentReservation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if reservation is not None:
        await release_agent(session, reservation, wrap_up=False, now=moment)
    from app.contact_center_v2.dialer.abandon_tracker import AbandonTracker

    # Persist the numerator/denominator and safe-harbor ledger in the same
    # transaction as the attempt transition; a later pacing decision must not
    # depend on a best-effort projection or a second worker noticing the row.
    await AbandonTracker(session).reconcile_attempt(attempt)
    await session.commit()
    return PlainTextResponse(
        safe_harbor_twiml(policy.safe_harbor_message),
        media_type="application/xml",
    )


__all__ = ["handle_abandoned_call"]
