"""HTTP rules for organizations. Cross-organization access is 404, not 403."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from app.auth.identity import api_keys as key_service
from app.auth.permissions import Permission
from tests.conftest import auth_headers, failure_detail

pytestmark = pytest.mark.asyncio


def _claims(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


async def test_unauthenticated_and_malformed_requests_are_rejected(client, owner_a):
    assert (await client.get("/api/organizations")).status_code == 401
    headers = await auth_headers(client, owner_a)
    malformed = await client.get("/api/organizations/not-a-uuid", headers=headers)
    assert malformed.status_code == 422


async def test_admin_can_rename_their_organization_and_a_viewer_cannot_suspend(
    client, tenant_a, admin_a, viewer_a
):
    admin = await auth_headers(client, admin_a)
    renamed = await client.patch(
        f"/api/organizations/{tenant_a.organization_id}",
        json={"name": "Admin Rename", "organization_id": str(uuid.uuid4())},
        headers=admin,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Admin Rename"
    assert renamed.json()["id"] == str(tenant_a.organization_id)

    viewer = await auth_headers(client, viewer_a)
    refused = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/suspend",
        headers=viewer,
    )
    assert refused.status_code == 403, refused.text
    assert failure_detail(refused)


async def test_create_organization_stays_platform_denied(client, owner_a):
    refused = await client.post(
        "/api/organizations",
        json={"name": "Not For Customers"},
        headers=await auth_headers(client, owner_a),
    )
    assert refused.status_code == 403, refused.text


async def test_cross_organization_access_matches_a_missing_row(client, tenant_a, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)
    foreign = await client.get(
        f"/api/organizations/{tenant_b.organization_id}",
        headers=headers,
    )
    missing = await client.get(f"/api/organizations/{uuid.uuid4()}", headers=headers)
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": {"code": "not_found", "message": "Not found"}}

    listed = await client.get(
        f"/api/organizations?organization_id={tenant_b.organization_id}",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [str(tenant_a.organization_id)]


async def test_login_token_has_no_organization_claim(client, owner_a, tenant_a):
    headers = await auth_headers(client, owner_a)
    token = headers["Authorization"].split()[1]
    claims = _claims(token)
    assert "organization_id" not in claims
    assert "environment_id" not in claims
    # The access token names the tenant as ``tid``. It must not grow a client
    # organization claim.
    assert claims["tid"] == str(tenant_a.id)
    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text


async def test_a_machine_credential_cannot_read_another_organization(
    client, owner_a, tenant_b
):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        "/api/api-keys",
        json={
            "name": "org reader",
            "scopes": key_service.scopes_from_permissions([Permission.TENANT_READ]),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    foreign = await client.get(
        f"/api/organizations/{tenant_b.organization_id}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert foreign.status_code == 404, foreign.text
    assert secret not in foreign.text
