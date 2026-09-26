"""Removing a federated subject must not delete the account, or lock it out.

Unlink is an operator action on one connection. The rules under test:

* a just-in-time account whose password nobody was given cannot lose its only
  sign-in method;
* an account that already had a password keeps that password, that role, and
  every session that was not opened through the connection being unlinked;
* a link id from another workspace is the same answer as a link that does not
  exist, and the mapping is left where it was;
* a viewer and a machine credential cannot do it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth.identity import api_keys as key_service
from app.auth.identity.models import IdentityMapping
from app.auth.permissions import Permission
from app.db.models import AuditAction, AuditLog, User, UserRole
from tests.auth.sso.providers import create_saml_connection, post_assertion, start_saml_login
from tests.conftest import TEST_PASSWORD, auth_headers, login, make_user

pytestmark = pytest.mark.asyncio


async def _login(client, idp, *, slug: str = "acme", **assertion):
    started = await start_saml_login(client, slug=slug)
    return await post_assertion(
        client,
        idp,
        started=started,
        slug=slug,
        response=idp.response(request_id=started["request_id"], **assertion),
    )


async def _mapping(db, email: str) -> IdentityMapping | None:
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        return None
    return (
        await db.execute(
            select(IdentityMapping)
            .where(IdentityMapping.user_id == user.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def test_a_jit_accounts_only_sign_in_method_cannot_be_removed(
    client, db, owner_a, idp
):
    connection = await create_saml_connection(client, owner_a, idp, slug="acme")
    completed = await _login(client, idp, email="jit-only@acme.test", name_id="subject-jit-only")
    assert completed.status_code == 200, completed.text

    headers = await auth_headers(client, owner_a)
    listed = await client.get(
        f"/api/sso/connections/{connection['id']}/links", headers=headers
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert len(body) == 1
    assert body[0]["can_unlink"] is False
    assert body[0]["block_reason"] == "no_known_password"
    assert "subject-jit-only" not in listed.text
    assert "external_subject" not in body[0]

    refused = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{body[0]['id']}",
        headers=headers,
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["reason"] == "no_known_password"

    user = (
        await db.execute(select(User).where(User.email == "jit-only@acme.test"))
    ).scalar_one()
    assert user.is_active is True
    mapping = await _mapping(db, "jit-only@acme.test")
    assert mapping is not None
    assert mapping.external_subject == "subject-jit-only"


async def test_a_password_account_can_be_unlinked_without_losing_the_account(
    client, db, tenant_a, owner_a, idp
):
    existing = await make_user(db, tenant_a, UserRole.MANAGER, email="linked@acme.example")
    password_login = await login(client, existing.email, TEST_PASSWORD)
    assert password_login.status_code == 200, password_login.text
    password_token = password_login.json()["access_token"]

    connection = await create_saml_connection(
        client, owner_a, idp, slug="acme", allow_account_linking=True
    )
    federated = await _login(
        client, idp, email="linked@acme.example", name_id="subject-linked"
    )
    assert federated.status_code == 200, federated.text
    assert federated.json()["linked"] is True
    sso_token = federated.json()["access_token"]

    headers = await auth_headers(client, owner_a)
    listed = await client.get(
        f"/api/sso/connections/{connection['id']}/links", headers=headers
    )
    assert listed.status_code == 200, listed.text
    row = listed.json()[0]
    assert row["can_unlink"] is True
    assert row["email"] == "linked@acme.example"
    assert "subject-linked" not in listed.text

    removed = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{row['id']}",
        headers=headers,
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["unlinked"] is True
    assert removed.json()["revoked_sessions"] >= 1
    assert "subject-linked" not in removed.text

    stored = (
        await db.execute(
            select(User).where(User.id == existing.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert stored.role is UserRole.MANAGER
    assert stored.is_active is True
    assert await _mapping(db, "linked@acme.test") is None

    sso_dead = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {sso_token}"}
    )
    assert sso_dead.status_code == 401, sso_dead.text

    password_live = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {password_token}"}
    )
    assert password_live.status_code == 200, password_live.text

    again = await login(client, existing.email, TEST_PASSWORD)
    assert again.status_code == 200, again.text

    second = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{row['id']}",
        headers=headers,
    )
    assert second.status_code == 404, second.text

    events = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.SSO_ACCOUNT_UNLINKED)
        )
    ).scalars().all()
    assert events
    detail = events[-1].detail if isinstance(events[-1].detail, dict) else {}
    assert detail.get("created_via") == "linked"
    assert "subject-linked" not in str(detail)
    assert detail.get("subject_hint")


async def test_a_link_from_another_workspace_is_not_found(
    client, db, tenant_a, owner_a, owner_b, idp
):
    await make_user(db, tenant_a, UserRole.AGENT, email="ours@acme.test")
    connection = await create_saml_connection(
        client, owner_a, idp, slug="acme", allow_account_linking=True
    )
    completed = await _login(client, idp, email="ours@acme.test", name_id="subject-ours")
    assert completed.status_code == 200, completed.text
    mapping = await _mapping(db, "ours@acme.test")
    assert mapping is not None

    foreign = await auth_headers(client, owner_b)
    listed = await client.get(
        f"/api/sso/connections/{connection['id']}/links", headers=foreign
    )
    assert listed.status_code == 404, listed.text
    deleted = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{mapping.id}",
        headers=foreign,
    )
    assert deleted.status_code == 404, deleted.text

    still = await _mapping(db, "ours@acme.test")
    assert still is not None
    assert still.id == mapping.id


async def test_a_viewer_and_a_machine_credential_cannot_unlink(
    client, db, tenant_a, owner_a, viewer_a, idp
):
    await make_user(db, tenant_a, UserRole.AGENT, email="staff@acme.test")
    connection = await create_saml_connection(
        client, owner_a, idp, slug="acme", allow_account_linking=True
    )
    assert (await _login(client, idp, email="staff@acme.test")).status_code == 200
    mapping = await _mapping(db, "staff@acme.test")
    assert mapping is not None

    viewer = await auth_headers(client, viewer_a)
    refused = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{mapping.id}",
        headers=viewer,
    )
    assert refused.status_code == 403, refused.text

    owner_headers = await auth_headers(client, owner_a)
    created = await client.post(
        "/api/api-keys",
        json={
            "name": "unlink-bot",
            "scopes": key_service.scopes_from_permissions([Permission.IDENTITY_WRITE]),
        },
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    machine = {"Authorization": f"Bearer {created.json()['secret']}"}
    machine_refused = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{mapping.id}",
        headers=machine,
    )
    assert machine_refused.status_code == 403, machine_refused.text
    assert machine_refused.json()["detail"]["code"] == "human_session_required"
    assert await _mapping(db, "staff@acme.test") is not None


async def test_an_unknown_link_id_is_not_found(client, owner_a, idp):
    connection = await create_saml_connection(client, owner_a, idp, slug="acme")
    headers = await auth_headers(client, owner_a)
    missing = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{uuid.uuid4()}",
        headers=headers,
    )
    assert missing.status_code == 404, missing.text
