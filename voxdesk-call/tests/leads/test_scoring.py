"""Explainable score. Repeated calculation matches. DNC is not cleared."""

from __future__ import annotations

import pytest

from app.db.models import LeadStatus
from app.leads import scoring, service


@pytest.mark.asyncio
async def test_score_is_stable_and_versioned(db, tenant_a):
    lead, _created = await service.create_lead(
        db,
        tenant_id=tenant_a.id,
        phone="+15554001001",
        email="ada@example.com",
        company="Analytical Engines",
        name="Ada",
    )
    first = await scoring.explain(db, lead)
    second = await scoring.explain(db, lead)
    assert first == second
    assert first["version"] == "lead-score-v1"
    assert first["score"] == sum(item["points"] for item in first["factors"])
    assert {item["code"] for item in first["factors"]} >= {
        "phone_present", "email_present", "company_present", "do_not_call"
    }


@pytest.mark.asyncio
async def test_scoring_does_not_clear_do_not_call(db, tenant_a):
    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15554001002")
    await service.change_status(
        db, tenant_id=tenant_a.id, lead_id=lead.id, target="do_not_call", reason="stop"
    )
    body = await service.score_lead(db, tenant_id=tenant_a.id, lead_id=lead.id)
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    assert body["status"] == "do_not_call"
    assert body["version"] == scoring.SCORE_VERSION
    explained = await scoring.explain(db, lead)
    assert explained["blocks_calling"] is True
