"""Rolling caller-sentiment alerts for agents and supervisors."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import AssistAlert, AssistTurn


async def record_negative_sentiment_alert(
    session: AsyncSession,
    *,
    turn: AssistTurn,
    score: float,
) -> AssistAlert | None:
    """Persist a cooldown-bound alert using the redacted turn's measured proxy.

    The evidence names the lexical method so a score is never presented as a
    diagnosis. Realtime fan-out uses the same tenant/call-bound alert query as
    other assist events, and repeated turns cannot flood the supervisor.
    """
    if score > settings.assist_negative_threshold:
        return None
    cutoff = utcnow() - timedelta(seconds=settings.assist_alert_cooldown_seconds)
    recent = (
        await session.execute(
            select(AssistAlert.id).where(
                AssistAlert.tenant_id == turn.tenant_id,
                AssistAlert.call_id == turn.call_id,
                AssistAlert.kind == "negative_sentiment",
                AssistAlert.created_at >= cutoff,
                AssistAlert.status == "open",
            )
        )
    ).first()
    if recent is not None:
        return None
    row = AssistAlert(
        tenant_id=turn.tenant_id,
        environment_id=turn.environment_id,
        call_id=turn.call_id,
        turn_id=turn.id,
        kind="negative_sentiment",
        severity="warning",
        message="Caller language crossed the configured negative-sentiment proxy threshold.",
        evidence={
            "score": score,
            "threshold": settings.assist_negative_threshold,
            "method": "lexical_proxy_v1",
        },
    )
    session.add(row)
    await session.flush()
    return row


__all__ = ["record_negative_sentiment_alert"]
