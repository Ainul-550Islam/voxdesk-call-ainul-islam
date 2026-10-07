from __future__ import annotations

import pytest

from app.db.models import UsageMetric
from tests.conftest import add_usage, auth_headers, subscribe


@pytest.mark.asyncio
async def test_settings_security_and_billing_read_write_state_are_persisted(
    client, db, tenant_a, owner_a, viewer_a, billing_plans
):
    await subscribe(db, tenant_a, "starter")
    await add_usage(db, tenant_a, UsageMetric.VOICE_MINUTE, 120)
    owner_headers = await auth_headers(client, owner_a)
    viewer_headers = await auth_headers(client, viewer_a)

    settings_response = await client.get(
        f"/api/tenants/{tenant_a.id}/settings", headers=owner_headers
    )
    assert settings_response.status_code == 200, settings_response.text
    assert settings_response.json()["name"] == tenant_a.name
    assert "tenant_id" not in settings_response.json()
    assert "crm_api_key" not in settings_response.json()

    posture = await client.get(
        f"/api/tenants/{tenant_a.id}/security/posture", headers=owner_headers
    )
    assert posture.status_code == 200, posture.text
    assert posture.json()["tenant_id"] == str(tenant_a.id)
    assert posture.json()["client_tenant_override"] is False

    summary = await client.get("/api/billing", headers=owner_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["plan_code"] == "starter"
    assert summary.json()["provider"] == "manual"

    plans = await client.get("/api/billing/plans", headers=owner_headers)
    assert plans.status_code == 200, plans.text
    assert any(plan["code"] == "pro" for plan in plans.json())
    usage = await client.get("/api/billing/usage", headers=owner_headers)
    assert usage.status_code == 200, usage.text
    voice_metric = next(
        metric for metric in usage.json()["metrics"] if metric["metric"] == "voice_minute"
    )
    assert voice_metric["used"] == 2.0
    invoices = await client.get("/api/billing/invoices", headers=owner_headers)
    assert invoices.status_code == 200, invoices.text
    assert invoices.json() == []

    changed = await client.post(
        "/api/billing/change-plan",
        headers=owner_headers,
        json={"plan_code": "pro"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["plan_code"] == "pro"
    reloaded = await client.get("/api/billing", headers=owner_headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["plan_code"] == "pro"

    forbidden = await client.get("/api/billing", headers=viewer_headers)
    assert forbidden.status_code == 403
