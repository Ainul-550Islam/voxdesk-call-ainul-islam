"""Live campaign dialing must check billing before claiming a lead or calling a provider."""

from __future__ import annotations

from datetime import time
import uuid

import pytest

from app.billing import hooks as billing_hooks
from app.db.models import LeadStatus
from app.telephony import outbound
from tests.conftest import auth_headers, make_lead


@pytest.mark.asyncio
async def test_live_campaign_run_is_blocked_before_lead_claim_and_provider_call(
    client, db, tenant_a, manager_a, monkeypatch
):
    tenant_a.outbound_enabled = True
    tenant_a.outbound_window_open = time(0, 0)
    tenant_a.outbound_window_close = time(23, 59, 59)
    await db.commit()

    headers = await auth_headers(client, manager_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns",
        json={"name": "Billing-gated campaign", "is_active": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    campaign_id = uuid.UUID(created.json()["id"])
    lead = await make_lead(
        db,
        tenant_a,
        phone="+15550209991",
        campaign_id=campaign_id,
    )

    billing_checks: list[uuid.UUID] = []

    async def deny_by_entitlement(session, tenant):
        billing_checks.append(tenant.id)
        return False, "over_included_no_overage"

    def unexpected_provider_call():
        raise AssertionError("The carrier client must not be constructed after a billing denial.")

    monkeypatch.setattr(billing_hooks, "may_place_outbound_call", deny_by_entitlement)
    monkeypatch.setattr(outbound, "_twilio_client", unexpected_provider_call)

    response = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns/{campaign_id}/run?dry_run=false",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dialed"] == 0
    assert body["results"] == [
        {
            "ok": False,
            "reason": "billing_entitlement_denied",
            "billing_reason": "over_included_no_overage",
        }
    ]
    assert billing_checks == [tenant_a.id]

    await db.refresh(lead)
    assert lead.attempts == 0
    assert lead.status is LeadStatus.NEW
