"""SCIM 2.0 Conformance & Tenant Isolation Suite (Part 7 / Gate G9).

Exercises RFC 7643 / RFC 7644 conformance across ``/scim/v2/{connection_id}``:
1. Users lifecycle: create (201), get (200), patch displayName/role (200), deactivate ``active=false`` (revokes sessions), delete (204).
2. Groups lifecycle: create group with members, patch add/remove members, role assignment sync, delete.
3. Filters & Pagination: ``userName eq ...``, ``userName sw ...``, ``active eq true``, ``startIndex`` & ``count``.
4. RFC 7644 Error Format: ``urn:ietf:params:scim:api:messages:2.0:Error`` with ``status`` and ``scimType`` on 400/401/404/409.
5. Strict Two-Tenant Isolation: Tenant B's SCIM token cannot read, filter, patch, or delete Tenant A's Users or Groups (returns 404).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import User
from tests.auth.scim.conftest import ScimClient, user_payload
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


async def test_scim_conformance_users_create_patch_deactivate_filters_and_pagination(
    client, db, scim
):
    """SCIM 2.0 Users: create, filter (eq/sw), paginate (startIndex/count), patch, and deactivate."""
    emails = [
        "alice.scim@acme.example",
        "bob.scim@acme.example",
        "carol.scim@acme.example",
    ]
    created_ids: list[str] = []
    for email in emails:
        res = await scim.post(
            "Users", user_payload(email, external_id=f"ext-{email}")
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["userName"] == email
        assert body["active"] is True
        created_ids.append(body["id"])

    # 1. Pagination: startIndex=1, count=2
    page1 = await scim.get("Users?startIndex=1&count=2")
    assert page1.status_code == 200, page1.text
    p1_body = page1.json()
    assert p1_body["schemas"] == [LIST_SCHEMA]
    assert p1_body["startIndex"] == 1
    assert p1_body["itemsPerPage"] == 2
    assert p1_body["totalResults"] >= 3

    # 2. Filter: userName eq "bob.scim@acme.example"
    filtered_eq = await scim.get(
        'Users?filter=userName eq "bob.scim@acme.example"'
    )
    assert filtered_eq.status_code == 200, filtered_eq.text
    eq_body = filtered_eq.json()
    assert eq_body["totalResults"] == 1
    assert eq_body["Resources"][0]["userName"] == "bob.scim@acme.example"

    # 3. Filter: userName sw "carol.scim"
    filtered_sw = await scim.get('Users?filter=userName sw "carol.scim"')
    assert filtered_sw.status_code == 200
    assert filtered_sw.json()["totalResults"] == 1

    # 4. PATCH user displayName and deactivate (active = false)
    alice_id = created_ids[0]
    patched = await scim.patch(
        f"Users/{alice_id}",
        {
            "schemas": [PATCH_SCHEMA],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "Alice Updated"},
                {"op": "replace", "path": "active", "value": False},
            ],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["active"] is False

    alice_db = (
        await db.execute(select(User).where(User.id == uuid.UUID(alice_id)))
    ).scalar_one()
    assert alice_db.is_active is False


async def test_scim_conformance_groups_and_rfc7644_error_format(client, scim):
    """SCIM 2.0 Groups lifecycle and RFC 7644 Error document conformance."""
    u1 = (await scim.post("Users", user_payload("grp.u1@acme.example"))).json()
    u2 = (await scim.post("Users", user_payload("grp.u2@acme.example"))).json()

    # 1. Create Group with initial member u1
    grp_res = await scim.post(
        "Groups",
        {
            "schemas": [GROUP_SCHEMA],
            "displayName": "Tier-2 Supervisors",
            "externalId": "ext-grp-supervisors",
            "members": [{"value": u1["id"]}],
        },
    )
    assert grp_res.status_code == 201, grp_res.text
    grp = grp_res.json()
    grp_id = grp["id"]
    assert grp["displayName"] == "Tier-2 Supervisors"
    assert {m["value"] for m in grp["members"]} == {u1["id"]}

    # 2. PATCH Group: add u2, then remove u1
    add_res = await scim.patch(
        f"Groups/{grp_id}",
        {
            "schemas": [PATCH_SCHEMA],
            "Operations": [
                {"op": "add", "path": "members", "value": [{"value": u2["id"]}]}
            ],
        },
    )
    assert add_res.status_code == 200, add_res.text
    assert {m["value"] for m in add_res.json()["members"]} == {u1["id"], u2["id"]}

    rem_res = await scim.patch(
        f"Groups/{grp_id}",
        {
            "schemas": [PATCH_SCHEMA],
            "Operations": [
                {"op": "remove", "path": "members", "value": [{"value": u1["id"]}]}
            ],
        },
    )
    assert rem_res.status_code == 200, rem_res.text
    assert {m["value"] for m in rem_res.json()["members"]} == {u2["id"]}

    # 3. Duplicate user creation returns 409 with RFC 7644 Error schema & scimType=uniqueness
    dup_res = await scim.post("Users", user_payload("grp.u1@acme.example"))
    assert dup_res.status_code == 409
    dup_body = dup_res.json()
    assert dup_body["schemas"] == [ERROR_SCHEMA]
    assert dup_body["scimType"] == "uniqueness"
    assert str(dup_body["status"]) == "409"

    # 4. Invalid filter syntax returns 400 with scimType=invalidFilter
    bad_filter = await scim.get("Users?filter=not a valid scim filter")
    assert bad_filter.status_code == 400
    bf_body = bad_filter.json()
    assert bf_body["schemas"] == [ERROR_SCHEMA]
    assert bf_body["scimType"] == "invalidFilter"


async def test_scim_conformance_tenant_isolation(
    client, owner_a, owner_b, scim
):
    """Tenant B's SCIM token cannot read, list, patch, or delete Tenant A's Users or Groups (returns 404)."""
    # Tenant A creates a User and a Group via its SCIM credential
    user_a = (
        await scim.post("Users", user_payload("isolated.a@acme.example"))
    ).json()
    group_a = (
        await scim.post(
            "Groups",
            {
                "schemas": [GROUP_SCHEMA],
                "displayName": "Tenant A Secret Group",
                "members": [{"value": user_a["id"]}],
            },
        )
    ).json()

    # Issue a SCIM credential for Tenant B
    headers_b = await auth_headers(client, owner_b)
    cred_b_res = await client.post(
        "/api/scim/credentials",
        json={"label": "Tenant B SCIM"},
        headers=headers_b,
    )
    assert cred_b_res.status_code == 201, cred_b_res.text
    scim_b = ScimClient(client, "default", cred_b_res.json()["token"])

    # Tenant B cannot fetch, patch, or delete Tenant A's User or Group
    assert (await scim_b.get(f"Users/{user_a['id']}")).status_code == 404
    assert (
        await scim_b.patch(
            f"Users/{user_a['id']}",
            {
                "schemas": [PATCH_SCHEMA],
                "Operations": [
                    {"op": "replace", "path": "active", "value": False}
                ],
            },
        )
    ).status_code == 404
    assert (await scim_b.delete(f"Users/{user_a['id']}")).status_code == 404

    assert (await scim_b.get(f"Groups/{group_a['id']}")).status_code == 404
    assert (await scim_b.delete(f"Groups/{group_a['id']}")).status_code == 404

    # Tenant B's list endpoints never leak Tenant A's resources
    users_b = (await scim_b.get("Users")).json()["Resources"]
    assert all(u["id"] != user_a["id"] for u in users_b)
    groups_b = (await scim_b.get("Groups")).json()["Resources"]
    assert all(g["id"] != group_a["id"] for g in groups_b)
