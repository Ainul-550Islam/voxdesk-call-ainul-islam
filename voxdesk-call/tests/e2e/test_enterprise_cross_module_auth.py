from __future__ import annotations

import pytest

import app.db.enterprise_models  # noqa: F401 — register integration models before test schema creation
from app.auth.identity import api_keys as key_service
from app.auth.permissions import Permission
from app.db.models import UsageMetric
from tests.conftest import add_usage, auth_headers, make_call, subscribe
from tests.e2e._support import create_published_voice_agent


@pytest.mark.asyncio
async def test_tenant_environment_rbac_api_key_and_module_isolation(
    client,
    db,
    tenant_a,
    tenant_b,
    owner_a,
    owner_b,
    viewer_a,
    billing_plans,
):
    owner_a_headers = await auth_headers(client, owner_a)
    owner_b_headers = await auth_headers(client, owner_b)
    viewer_a_headers = await auth_headers(client, viewer_a)

    environments_a = await client.get(
        f"/api/tenants/{tenant_a.id}/environments", headers=owner_a_headers
    )
    assert environments_a.status_code == 200, environments_a.text
    production = next(row for row in environments_a.json() if row["kind"] == "production")
    current = await client.get(
        f"/api/tenants/{tenant_a.id}/access/current", headers=owner_a_headers
    )
    assert current.status_code == 200, current.text
    assert current.json()["id"] == production["id"]

    agent_a, published_a, _version_a = await create_published_voice_agent(
        client,
        owner_a_headers,
        name="Cross-module production agent A",
        environment_id=production["id"],
        environment="production",
    )
    agent_a_id = agent_a["agent_id"]
    assert published_a["version"] == 1

    # Tenant B cannot read or mutate Tenant A's agent by knowing its identifier.
    foreign_agent = await client.get(
        f"/api/v1/agents/{agent_a_id}/builder", headers=owner_b_headers
    )
    assert foreign_agent.status_code == 404, foreign_agent.text
    viewer_write = await client.post(
        "/api/v1/agents",
        headers=viewer_a_headers,
        json={"name": "Viewer must not create", "agent_type": "voice"},
    )
    assert viewer_write.status_code == 403, viewer_write.text

    simulated_call = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=owner_a_headers,
        json={
            "to_number": "+14155550176",
            "from_number": "+14155550105",
            "agent_id": agent_a_id,
            "agent_version_number": published_a["version"],
            "provider": "SIMULATED",
            "is_simulation": True,
            "environment_id": production["id"],
            "idempotency_key": "prompt8-cross-module-call-a",
        },
    )
    assert simulated_call.status_code == 201, simulated_call.text
    telephony_call_id = simulated_call.json()["id"]
    assert simulated_call.json()["is_simulation"] is True
    assert simulated_call.json()["environment_id"] == production["id"]
    assert simulated_call.json()["agent_version_number"] == 1

    own_telephony_call = await client.get(
        f"/api/v1/telephony/calls/{telephony_call_id}", headers=owner_a_headers
    )
    foreign_telephony_call = await client.get(
        f"/api/v1/telephony/calls/{telephony_call_id}", headers=owner_b_headers
    )
    assert own_telephony_call.status_code == 200, own_telephony_call.text
    assert foreign_telephony_call.status_code == 404, foreign_telephony_call.text

    viewer_call_write = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=viewer_a_headers,
        json={
            "to_number": "+14155550177",
            "from_number": "+14155550106",
            "agent_id": agent_a_id,
            "agent_version_number": 1,
            "provider": "SIMULATED",
            "is_simulation": True,
            "environment_id": production["id"],
            "idempotency_key": "prompt8-cross-module-viewer-denied",
        },
    )
    assert viewer_call_write.status_code == 403, viewer_call_write.text

    # The established legacy Call/analytics/billing read models are tenant
    # scoped independently of the versioned telephony-session model above.
    legacy_call_a = await make_call(db, tenant_a)
    legacy_call_b = await make_call(db, tenant_b)
    legacy_a_visible_to_b = await client.get(
        f"/api/calls/{legacy_call_a.id}", headers=owner_b_headers
    )
    assert legacy_a_visible_to_b.status_code == 404, legacy_a_visible_to_b.text
    own_legacy_call = await client.get(
        f"/api/calls/{legacy_call_a.id}", headers=owner_a_headers
    )
    assert own_legacy_call.status_code == 200, own_legacy_call.text

    foreign_search = await client.post(
        "/api/calls/search",
        headers=owner_b_headers,
        json={"search": legacy_call_a.call_sid, "limit": 10},
    )
    own_search = await client.post(
        "/api/calls/search",
        headers=owner_a_headers,
        json={"search": legacy_call_a.call_sid, "limit": 10},
    )
    assert foreign_search.status_code == 200, foreign_search.text
    assert foreign_search.json()["total"] == 0
    assert own_search.status_code == 200, own_search.text
    assert own_search.json()["total"] == 1

    analytics_a = await client.get(
        "/api/analytics/overview?range=last_30_days", headers=owner_a_headers
    )
    analytics_b = await client.get(
        "/api/analytics/overview?range=last_30_days", headers=owner_b_headers
    )
    assert analytics_a.status_code == 200, analytics_a.text
    assert analytics_b.status_code == 200, analytics_b.text
    assert analytics_a.json()["calls"]["total"] == 1
    assert analytics_b.json()["calls"]["total"] == 1

    await subscribe(db, tenant_a, "starter")
    await subscribe(db, tenant_b, "starter")
    await add_usage(db, tenant_a, UsageMetric.VOICE_MINUTE, 60)
    await add_usage(db, tenant_b, UsageMetric.VOICE_MINUTE, 120)
    billing_a = await client.get("/api/billing/usage", headers=owner_a_headers)
    billing_b = await client.get("/api/billing/usage", headers=owner_b_headers)
    assert billing_a.status_code == 200, billing_a.text
    assert billing_b.status_code == 200, billing_b.text
    voice_a = next(row for row in billing_a.json()["metrics"] if row["metric"] == "voice_minute")
    voice_b = next(row for row in billing_b.json()["metrics"] if row["metric"] == "voice_minute")
    assert voice_a["used"] == 1.0
    assert voice_b["used"] == 2.0

    integrations_a = await client.get(
        "/api/v1/parity/integrations", headers=owner_a_headers
    )
    integrations_b = await client.get(
        "/api/v1/parity/integrations", headers=owner_b_headers
    )
    assert integrations_a.status_code == 200, integrations_a.text
    assert integrations_b.status_code == 200, integrations_b.text
    salesforce_a = next(
        row for row in integrations_a.json()["items"]
        if row["integration_type"] == "crm" and row["provider"] == "salesforce"
    )
    salesforce_b = next(
        row for row in integrations_b.json()["items"]
        if row["integration_type"] == "crm" and row["provider"] == "salesforce"
    )
    assert salesforce_a["status"] == "NOT_CONFIGURED"
    assert salesforce_b["status"] == "NOT_CONFIGURED"
    assert salesforce_a["credentials_present"] is False
    assert salesforce_b["credentials_present"] is False

    key_scopes = key_service.scopes_from_permissions(
        [Permission.CALL_READ, Permission.ANALYTICS_READ]
    )
    key_response = await client.post(
        "/api/api-keys",
        headers=owner_a_headers,
        json={"name": "Prompt 8 scoped cross-module reader", "scopes": key_scopes},
    )
    assert key_response.status_code == 201, key_response.text
    key_headers = {
        "Authorization": f"Bearer {key_response.json()['secret']}",
        "X-Tenant-ID": str(tenant_b.id),
    }
    own_call_with_key = await client.get(
        f"/api/calls/{legacy_call_a.id}", headers=key_headers
    )
    foreign_call_with_key = await client.get(
        f"/api/calls/{legacy_call_b.id}", headers=key_headers
    )
    assert own_call_with_key.status_code == 200, own_call_with_key.text
    assert own_call_with_key.json()["id"] == str(legacy_call_a.id)
    assert foreign_call_with_key.status_code == 404, foreign_call_with_key.text

    staging = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        headers=owner_a_headers,
        json={"name": "Prompt 8 Staging", "slug": "prompt8-staging", "kind": "staging"},
    )
    assert staging.status_code == 201, staging.text
    staging_id = staging.json()["id"]
    agent_staging, published_staging, _version_staging = await create_published_voice_agent(
        client,
        owner_a_headers,
        name="Cross-module staging agent A",
        environment_id=staging_id,
        environment="staging",
    )
    agent_staging_id = agent_staging["agent_id"]
    assert published_staging["version"] == 1

    production_inspection = await client.get(
        "/api/v1/parity/e2e/inspect",
        headers=owner_a_headers,
        params={"agent_id": agent_a_id},
    )
    assert production_inspection.status_code == 200, production_inspection.text
    assert production_inspection.json()["environment_id"] == production["id"]
    assert any(
        step["key"] == "environment_boundary" and step["status"] == "PASS"
        for step in production_inspection.json()["steps"]
    )

    select_staging = await client.post(
        f"/api/tenants/{tenant_a.id}/access/current",
        headers=owner_a_headers,
        json={"environment_id": staging_id},
    )
    assert select_staging.status_code == 200, select_staging.text
    selected_inspection = await client.get(
        "/api/v1/parity/e2e/inspect",
        headers=owner_a_headers,
        params={"agent_id": agent_staging_id},
    )
    assert selected_inspection.status_code == 200, selected_inspection.text
    assert selected_inspection.json()["environment_id"] == staging_id
    assert any(
        step["key"] == "environment_boundary" and step["status"] == "PASS"
        for step in selected_inspection.json()["steps"]
    )

    production_agent_from_staging = await client.get(
        "/api/v1/parity/e2e/inspect",
        headers=owner_a_headers,
        params={"agent_id": agent_a_id},
    )
    assert production_agent_from_staging.status_code == 404

    audit_a = await client.get(
        "/api/v1/audit/events", headers=owner_a_headers, params={"limit": 200}
    )
    assert audit_a.status_code == 200, audit_a.text
    assert any(
        item["action"] == "environment_selected"
        and item["detail"].get("environment_id") == staging_id
        for item in audit_a.json()["items"]
    )
    audit_b = await client.get(
        "/api/v1/audit/events", headers=owner_b_headers, params={"limit": 200}
    )
    assert audit_b.status_code == 200, audit_b.text
    assert all(
        item["detail"].get("environment_id") != staging_id
        for item in audit_b.json()["items"]
    )
