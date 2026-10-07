"""Status changes on the existing ``LeadStatus`` values.

Enterprise labels such as contacted, engaged and converted are aliases for
values the ``leads.status`` column already stores. They are not new enum
members, and a historical row is never rewritten to use them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus
from app.integrations.crm import hooks as crm_hooks
from app.leads.exceptions import ClaimConflict, InvalidTransition, LeadError, LeadNotFound
from app.leads.models import LeadActivity, LeadStatusHistory
from app.leads.repository import get_identity, next_history_sequence, touch

# Stored values. Do not rename these; outbound and CRM already persist them.
STORED = {
    "new": LeadStatus.NEW,
    "queued": LeadStatus.QUEUED,
    "called": LeadStatus.CALLED,
    "qualified": LeadStatus.QUALIFIED,
    "unqualified": LeadStatus.UNQUALIFIED,
    "failed": LeadStatus.FAILED,
    "do_not_call": LeadStatus.DNC,
}

# Request aliases. The row stores the right-hand value, never the alias.
ALIASES = {
    "contacted": "called",
    "engaged": "called",
    "appointment_set": "qualified",
    "converted": "qualified",
    "lost": "unqualified",
    "nurture": "new",
    "dnc": "do_not_call",
}

_ALLOWED: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.NEW: frozenset({
        LeadStatus.QUEUED, LeadStatus.CALLED, LeadStatus.QUALIFIED,
        LeadStatus.UNQUALIFIED, LeadStatus.FAILED, LeadStatus.DNC,
    }),
    LeadStatus.QUEUED: frozenset({
        LeadStatus.CALLED, LeadStatus.QUALIFIED, LeadStatus.UNQUALIFIED,
        LeadStatus.FAILED, LeadStatus.DNC, LeadStatus.NEW,
    }),
    LeadStatus.CALLED: frozenset({
        LeadStatus.QUALIFIED, LeadStatus.UNQUALIFIED, LeadStatus.FAILED,
        LeadStatus.QUEUED, LeadStatus.DNC,
    }),
    LeadStatus.QUALIFIED: frozenset({
        LeadStatus.UNQUALIFIED, LeadStatus.DNC, LeadStatus.CALLED,
    }),
    LeadStatus.UNQUALIFIED: frozenset({
        LeadStatus.QUEUED, LeadStatus.QUALIFIED, LeadStatus.DNC,
    }),
    LeadStatus.FAILED: frozenset({
        LeadStatus.QUEUED, LeadStatus.DNC, LeadStatus.UNQUALIFIED,
    }),
    # Terminal for calling. Nothing here clears it.
    LeadStatus.DNC: frozenset(),
}


def status_value(status: LeadStatus | str) -> str:
    return status.value if isinstance(status, LeadStatus) else str(status)


def resolve_status(raw: str) -> tuple[LeadStatus, str | None]:
    """Return the stored status and the alias that was requested, if any."""
    key = (raw or "").strip().lower()
    alias = key if key in ALIASES else None
    stored_name = ALIASES.get(key, key)
    found = STORED.get(stored_name)
    if found is None:
        raise InvalidTransition(f"Unknown lead status {raw!r}")
    return found, alias


def assert_allowed(current: LeadStatus, target: LeadStatus) -> None:
    if current is LeadStatus.DNC:
        raise InvalidTransition("Do-not-call is terminal for calling")
    if current is target or target not in _ALLOWED[current]:
        raise InvalidTransition(
            f"Cannot move a lead from {current.value} to {target.value}"
        )


async def append_history(
    session: AsyncSession,
    lead: Lead,
    *,
    from_status: str | None,
    to_status: str,
    reason: str,
    source: str,
    actor_id: uuid.UUID | None = None,
) -> LeadStatusHistory:
    if lead.environment_id is None:
        raise LeadError("Lead has no environment", code="missing_environment")
    sequence = await next_history_sequence(
        session, lead.tenant_id, lead.id, lead.environment_id
    )
    row = LeadStatusHistory(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        sequence=sequence,
        from_status=from_status,
        to_status=to_status,
        reason=reason[:200],
        actor_id=actor_id,
        source=source[:32],
    )
    session.add(row)
    await session.flush()
    return row


async def transition(
    session: AsyncSession,
    lead: Lead,
    target_raw: str,
    *,
    reason: str,
    source: str,
    actor_id: uuid.UUID | None = None,
    expected: LeadStatus | None = None,
    environment_id: uuid.UUID | None = None,
) -> Lead:
    """Compare-and-set the status. A stale reader loses instead of overwriting.

    This is the only legitimate lead-status state machine. The lead is
    mutated inside its own tenant/environment scope: the guarded UPDATE
    matches on ``tenant_id`` and ``environment_id`` as well as the believed
    current status, and callers that hold an explicit environment can pass
    ``environment_id`` to assert the lead was loaded from that same
    environment before anything is written.
    """
    if lead.environment_id is None:
        raise LeadError("Lead has no environment", code="missing_environment")
    if environment_id is not None and lead.environment_id != environment_id:
        raise LeadNotFound()
    target, alias = resolve_status(target_raw)
    believed = expected if expected is not None else lead.status
    assert_allowed(believed, target)
    why = reason.strip() or "status_change"
    if alias:
        why = f"{why}; alias:{alias}"[:200]
    result = await session.execute(
        update(Lead)
        .where(
            Lead.id == lead.id,
            Lead.tenant_id == lead.tenant_id,
            Lead.environment_id == lead.environment_id,
            Lead.status == believed,
        )
        .values(status=target)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ClaimConflict("The lead status changed concurrently")
    lead.status = target
    await append_history(
        session,
        lead,
        from_status=status_value(believed),
        to_status=target.value,
        reason=why,
        source=source,
        actor_id=actor_id,
    )
    identity = await get_identity(session, lead.tenant_id, lead.id, lead.environment_id)
    if identity is not None:
        touch(identity)
    await crm_hooks.on_lead_updated(
        session, lead, reason=f"status:{target.value}:{why}"[:120]
    )
    return lead


async def record_created(
    session: AsyncSession,
    lead: Lead,
    *,
    source: str,
    actor_id: uuid.UUID | None = None,
) -> None:
    await append_history(
        session,
        lead,
        from_status=None,
        to_status=status_value(lead.status),
        reason="created",
        source=source,
        actor_id=actor_id,
    )


async def record_dial_claim(
    session: AsyncSession,
    lead: Lead,
    *,
    campaign_id: uuid.UUID,
) -> None:
    """Audit an outbound claim that the dialer has already committed.

    The dialer owns the attempt update. This only appends history for the
    claim it already won. It does not dial, and it does not clear do-not-call.
    """
    if lead.environment_id is None:
        return
    previous = status_value(lead.status)
    if previous != LeadStatus.QUEUED.value:
        await append_history(
            session,
            lead,
            from_status=previous,
            to_status=LeadStatus.QUEUED.value,
            reason=f"outbound_claim:{campaign_id}",
            source="outbound",
        )
    session.add(
        LeadActivity(
            lead_id=lead.id,
            tenant_id=lead.tenant_id,
            environment_id=lead.environment_id,
            kind="outbound_claim",
            summary=f"Dial attempt claimed for campaign {campaign_id}",
        )
    )
    await session.flush()
