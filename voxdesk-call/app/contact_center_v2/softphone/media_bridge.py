"""Conference media and redacted AI warm-transfer briefing handoff."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.twiml import conference_twiml, rejection_twiml
from app.db.contact_center_v2_models import SoftphoneCall
from app.db.models import Call, Lead

TERMINAL_CALL_STATES = frozenset({"completed", "failed", "cancelled"})


def conference_media(call: SoftphoneCall, *, customer: bool) -> str:
    """Build media only for a durable, nonterminal call with a conference name."""
    if call.status in TERMINAL_CALL_STATES or not call.conference_name:
        return rejection_twiml()
    return conference_twiml(call.conference_name, customer=customer)


def warm_transfer_briefing(call: Call | None) -> dict[str, Any] | None:
    """Return the existing AI briefing without destination numbers or TwiML.

    The canonical transfer service persists this metadata when it bridges the
    AI leg to a human. CC2 consumes that same payload; it does not invent a
    second briefing or claim a warm transfer when no such metadata exists.
    """
    if call is None:
        return None
    context = dict(call.transfer_context or {})
    warm = dict(context.get("warm_transfer") or {})
    text = str(warm.get("briefing_text") or "").strip()
    if not text:
        return None
    from app.ai.guardrails.pii import redact

    return {
        "text_redacted": str(redact(text))[:1000],
        "status": str(warm.get("status") or ""),
        "human_briefed": warm.get("human_briefed") is True,
        "ai_detached": warm.get("ai_detached") is True,
        "returned_to_ai": warm.get("returned_to_ai") is True,
    }


async def customer_context(
    session: AsyncSession,
    call: SoftphoneCall,
    canonical_call: Call | None,
) -> dict[str, Any]:
    """Load bounded CRM/lead context and prior-call metadata for this call."""
    if canonical_call is None or canonical_call.tenant_id != call.tenant_id:
        return {"lead": None, "prior_calls": [], "crm": {}}
    lead = await session.get(Lead, canonical_call.lead_id) if canonical_call.lead_id else None
    if lead is not None and lead.tenant_id != call.tenant_id:
        lead = None
    prior: list[Call] = []
    if lead is not None:
        prior = list(
            (
                await session.execute(
                    select(Call).where(
                        Call.tenant_id == call.tenant_id,
                        Call.lead_id == lead.id,
                        Call.id != canonical_call.id,
                    ).order_by(Call.started_at.desc(), Call.id.desc()).limit(5)
                )
            ).scalars().all()
        )
    from app.ai.guardrails.pii import redact

    stored = dict((canonical_call.transfer_context or {}).get("crm_context") or {})
    allowed_crm = {
        key: stored[key]
        for key in ("lead_name", "lead_company", "lead_score", "ticket_id", "account_id")
        if key in stored and isinstance(stored[key], (str, int, float, bool))
    }
    return {
        "lead": None if lead is None else {
            "id": str(lead.id), "name": lead.name, "company": lead.company,
            "score": lead.score, "status": str(lead.status),
        },
        "prior_calls": [
            {
                "id": str(item.id), "status": str(item.status),
                "started_at": item.started_at.isoformat(),
                "duration_seconds": item.duration_seconds,
                "summary_redacted": str(redact(item.summary or ""))[:1000],
            }
            for item in prior
        ],
        "crm": allowed_crm,
        "crm_synced": canonical_call.crm_synced,
    }


def media_handoff(call: SoftphoneCall, *, customer: bool, canonical_call: Call | None) -> dict[str, Any]:
    """Combine authoritative conference instructions and optional AI briefing."""
    return {
        "twiml": conference_media(call, customer=customer),
        "warm_transfer_briefing": warm_transfer_briefing(canonical_call),
    }


__all__ = [
    "TERMINAL_CALL_STATES",
    "conference_media",
    "customer_context",
    "media_handoff",
    "warm_transfer_briefing",
]
