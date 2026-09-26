"""Two workers cannot both claim or both transition the same lead from one seen state."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.db.models import Lead, LeadStatus
from app.leads.exceptions import ClaimConflict
from app.leads.lifecycle import transition
from app.telephony import outbound
from tests.conftest import make_lead


@pytest.mark.asyncio
async def test_two_sessions_cannot_both_claim(db, sessionmaker_, tenant_a):
    tenant_a.outbound_enabled = True
    tenant_a.max_call_attempts = 3
    await db.commit()
    lead = await make_lead(db, tenant_a, phone="+15558001001")
    first_session = sessionmaker_()
    second_session = sessionmaker_()
    try:
        seen_first = await first_session.get(Lead, lead.id)
        seen_second = await second_session.get(Lead, lead.id)
        now = datetime.utcnow()
        won = await outbound._claim_attempt(first_session, seen_first, tenant_a, now=now)
        lost = await outbound._claim_attempt(second_session, seen_second, tenant_a, now=now)
        assert won is True
        assert lost is False
    finally:
        await first_session.close()
        await second_session.close()
    await db.refresh(lead)
    assert lead.attempts == 1
    assert lead.status is LeadStatus.QUEUED


@pytest.mark.asyncio
async def test_stale_status_transition_loses(db, sessionmaker_, tenant_a):
    from app.leads import service

    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15558001002")
    await db.commit()
    first_session = sessionmaker_()
    second_session = sessionmaker_()
    try:
        seen_first = await first_session.get(Lead, lead.id)
        seen_second = await second_session.get(Lead, lead.id)
        await transition(
            first_session,
            seen_first,
            "qualified",
            reason="winner",
            source="test",
            expected=seen_first.status,
        )
        await first_session.commit()
        with pytest.raises(ClaimConflict):
            await transition(
                second_session,
                seen_second,
                "unqualified",
                reason="loser",
                source="test",
                expected=seen_second.status,
            )
    finally:
        await first_session.close()
        await second_session.close()
    await db.refresh(lead)
    assert lead.status is LeadStatus.QUALIFIED
