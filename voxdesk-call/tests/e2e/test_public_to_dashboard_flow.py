from __future__ import annotations

import uuid

import pytest

from tests.conftest import TEST_PASSWORD


@pytest.mark.asyncio
async def test_public_catalog_signup_environment_and_authenticated_dashboard_api(client):
    # Public routes are usable without an Authorization header and expose no
    # tenant-specific readiness claims.
    manifest = await client.get("/api/v1/public/site/manifest")
    pricing = await client.get("/api/v1/public/site/pricing")
    status = await client.get("/api/v1/public/site/status")
    catalog = await client.get("/api/v1/public/use-cases?page=1&page_size=5")
    assert manifest.status_code == 200, manifest.text
    assert pricing.status_code == 200, pricing.text
    assert status.status_code == 200, status.text
    assert catalog.status_code == 200, catalog.text
    catalog_items = catalog.json()["data"]["items"]
    assert catalog_items
    assert all(item["supported"] is None for item in catalog_items)

    slug = catalog_items[0]["slug"]
    detail_response = await client.get(f"/api/v1/public/use-cases/{slug}")
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()["data"]
    assert detail["supported"] is None
    assert detail["meta"]["content_basis"] == "static_catalog_example"
    assert detail["meta"]["tenant_readiness_verified"] is False
    assert detail["meta"]["provider_connectivity_verified"] is False
    assert all(capability["enabled"] is None for capability in detail["capabilities"])
    assert all(integration["verified"] is None for integration in detail["integrations"])

    email = f"prompt8-{uuid.uuid4().hex[:12]}@example.com"
    signup = await client.post(
        "/auth/signup",
        json={
            "organization_name": f"Prompt 8 {uuid.uuid4().hex[:8]}",
            "full_name": "Prompt 8 E2E Owner",
            "email": email,
            "password": TEST_PASSWORD,
            "industry": "technology",
        },
    )
    assert signup.status_code == 201, signup.text
    auth = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    tenant_id = signup.json()["tenant_id"]

    me = await client.get("/auth/me", headers=auth)
    assert me.status_code == 200, me.text
    assert me.json()["tenant"]["id"] == tenant_id

    environments = await client.get(
        f"/api/tenants/{tenant_id}/environments", headers=auth
    )
    assert environments.status_code == 200, environments.text
    assert any(row["kind"] == "production" and row["status"] == "active" for row in environments.json())

    anonymous_inventory = await client.get("/api/v1/parity/capabilities")
    assert anonymous_inventory.status_code in {401, 403}
    inventory = await client.get("/api/v1/parity/capabilities", headers=auth)
    assert inventory.status_code == 200, inventory.text
    evidence = inventory.json()
    assert evidence["registered_api_operations"] > 0
    assert evidence["capabilities"]
    assert all(row["evidence_basis"] == "registered_routes_only" for row in evidence["capabilities"])
    assert all(row["status"] not in {"VERIFIED", "PRODUCTION_READY"} for row in evidence["capabilities"])

    agents = await client.get("/api/v1/agents", headers=auth)
    assert agents.status_code == 200, agents.text
    assert agents.json() == []
