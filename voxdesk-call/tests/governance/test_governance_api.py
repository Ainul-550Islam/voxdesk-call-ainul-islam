"""Authentication, authorization, isolation and side-effect API tests."""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_governance_api_requires_authentication(client, tenant_a):
    response = await client.get(f"/api/tenants/{tenant_a.id}/governance/models")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_governance_api_uses_authenticated_tenant_for_bare_paths(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    response = await client.get("/api/governance/posture", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == str(tenant_a.id)
    assert body["physical_residency_proven"] is False
