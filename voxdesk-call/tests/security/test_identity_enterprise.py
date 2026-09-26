"""Enterprise identity adversarial coverage.

These tests reject cross-tenant use of SSO, SCIM, API keys, service accounts,
domains, MFA and sessions. They also reject unmapped privilege claims and
assert that known secrets do not survive the audit scrubber or a captured log.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.auth.identity import api_keys as key_service
from app.auth.identity import events as identity_events
from app.auth.identity.models import APIKey
from app.auth.identity.sso.claims import NormalizedClaims, decide_role
from app.auth.permissions import Permission
from app.core.logging import log
from app.db.models import AuditAction, AuditLog, UserRole
from tests.auth.sso.providers import create_saml_connection, post_assertion, start_saml_login
from tests.conftest import auth_headers, enroll_totp, make_user
from tests.harness import ScimClient

pytestmark = pytest.mark.asyncio

ANALYTICS = "/api/analytics/overview"
SENTINEL_PASSWORD = "sentinel-password-not-for-logs"
SENTINEL_TOKEN = "sentinel-access-token-not-for-logs"
SENTINEL_SECRET = "sentinel-client-secret-not-for-logs"
SENTINEL_ASSERTION = "sentinel-saml-assertion-not-for-logs"


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _api_key(client, owner, **payload):
    headers = await auth_headers(client, owner)
    body = {
        "name": "enterprise probe",
        "scopes": key_service.scopes_from_permissions([Permission.ANALYTICS_READ]),
    }
    body.update(payload)
    response = await client.post("/api/api-keys", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _service_credential(client, owner) -> tuple[dict, str]:
    headers = await auth_headers(client, owner)
    created = await client.post(
        "/api/service-accounts",
        json={"name": "enterprise bot", "scopes": [Permission.ANALYTICS_READ.value]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/service-accounts/{created.json()['id']}/credentials",
        json={},
        headers=headers,
    )
    assert issued.status_code == 201, issued.text
    return created.json(), issued.json()["secret"]


async def test_tenant_a_cannot_read_or_change_tenant_b_sso(client, owner_a, owner_b, idp):
    connection = await create_saml_connection(client, owner_a, idp, slug="alpha-sso")
    foreign = await auth_headers(client, owner_b)
    listed = await client.get("/api/sso/connections", headers=foreign)
    assert listed.status_code == 200
    assert all(item["id"] != connection["id"] for item in listed.json())
    fetched = await client.get(f"/api/sso/connections/{connection['id']}", headers=foreign)
    updated = await client.patch(
        f"/api/sso/connections/{connection['id']}",
        json={"display_name": "hijacked", "protocol": "saml"},
        headers=foreign,
    )
    removed = await client.delete(f"/api/sso/connections/{connection['id']}", headers=foreign)
    assert fetched.status_code == 404, fetched.text
    assert updated.status_code in {404, 422}, updated.text
    assert removed.status_code == 404, removed.text
    owner = await client.get(
        f"/api/sso/connections/{connection['id']}",
        headers=await auth_headers(client, owner_a),
    )
    assert owner.status_code == 200
    assert owner.json()["id"] == connection["id"]


async def test_tenant_a_scim_credential_cannot_provision_or_deactivate_tenant_b(
    client, db, tenant_b, owner_b, idp, scim_token, scim_connection
):
    foreign = await create_saml_connection(client, owner_b, idp, slug="beta-scim")
    attacker = ScimClient(client, foreign["id"], scim_token)
    created = await attacker.post(
        "/Users",
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "intruder@beta.test",
            "active": True,
        },
    )
    assert created.status_code in {401, 403, 404}, created.text
    victim = await make_user(db, tenant_b, UserRole.AGENT, email="victim@beta.test")
    deactivated = await attacker.patch(
        f"/Users/{victim.id}",
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    assert deactivated.status_code in {401, 403, 404}, deactivated.text
    own = ScimClient(client, scim_connection["id"], scim_token)
    listed = await own.get("/Users")
    assert listed.status_code == 200, listed.text
    assert "victim@beta.test" not in listed.text


async def test_tenant_a_api_key_and_service_account_cannot_reach_tenant_b(client, owner_a, owner_b):
    key = await _api_key(client, owner_a)
    _account, secret = await _service_credential(client, owner_a)
    foreign_domain = await client.post(
        "/api/domains",
        json={"domain": "beta-only.example"},
        headers=await auth_headers(client, owner_b),
    )
    assert foreign_domain.status_code == 201, foreign_domain.text
    domain_id = foreign_domain.json()["id"]
    for headers in (_bearer(key["secret"]), _bearer(secret)):
        viewed = await client.get(f"/api/domains/{domain_id}", headers=headers)
        assert viewed.status_code in {401, 403, 404}, viewed.text
        assert "beta-only.example" not in viewed.text
        own = await client.get(ANALYTICS, headers=headers)
        assert own.status_code == 200, own.text
        assert "beta-only.example" not in own.text


async def test_tenant_a_domain_cannot_be_attached_to_tenant_b(client, owner_a, owner_b):
    created = await client.post(
        "/api/domains",
        json={"domain": "alpha-owned.example"},
        headers=await auth_headers(client, owner_a),
    )
    assert created.status_code == 201, created.text
    foreign = await auth_headers(client, owner_b)
    stolen = await client.patch(
        f"/api/domains/{created.json()['id']}",
        json={"enforcement": "require_sso"},
        headers=foreign,
    )
    assert stolen.status_code == 404, stolen.text
    still = await client.get(
        f"/api/domains/{created.json()['id']}",
        headers=await auth_headers(client, owner_a),
    )
    assert still.status_code == 200
    assert still.json()["enforcement"] == "off"


async def test_tenant_a_sso_callback_cannot_authenticate_tenant_b(client, owner_a, owner_b, idp):
    await create_saml_connection(client, owner_a, idp, slug="alpha-login")
    await create_saml_connection(client, owner_b, idp, slug="beta-login")
    started = await start_saml_login(client, slug="alpha-login")
    crossed = await post_assertion(
        client,
        idp,
        started=started,
        slug="beta-login",
        response=idp.response(
            request_id=started["request_id"],
            email="crossed@alpha.test",
            name_id="subject-crossed",
        ),
    )
    assert crossed.status_code >= 400, crossed.text
    assert "access_token" not in crossed.text


async def test_tenant_a_mfa_code_cannot_satisfy_tenant_b(client, db, owner_a, owner_b):
    secret = await enroll_totp(db, owner_a)
    from app.auth.identity import totp

    code = totp.totp(secret, time.time())
    foreign = await auth_headers(client, owner_b)
    refused = await client.post("/api/identity/reauth", json={"code": code}, headers=foreign)
    assert refused.status_code in {400, 401, 403}, refused.text
    assert secret not in refused.text


async def test_tenant_a_session_cannot_manage_tenant_b(client, owner_a, owner_b):
    foreign_headers = await auth_headers(client, owner_b)
    foreign_sessions = await client.get("/api/sessions", headers=foreign_headers)
    assert foreign_sessions.status_code == 200, foreign_sessions.text
    foreign_id = foreign_sessions.json()["sessions"][0]["id"]
    attacker = await auth_headers(client, owner_a)
    revoked = await client.delete(f"/api/sessions/{foreign_id}", headers=attacker)
    renamed = await client.patch(
        f"/api/sessions/{foreign_id}",
        json={"label": "stolen"},
        headers=attacker,
    )
    assert revoked.status_code == 404, revoked.text
    assert renamed.status_code == 404, renamed.text
    still = await client.get("/api/sessions", headers=foreign_headers)
    assert any(item["id"] == foreign_id for item in still.json()["sessions"])
    events = await client.get("/api/security/events", headers=attacker)
    assert events.status_code == 200, events.text
    assert foreign_id not in events.text
    assert str(owner_b.id) not in events.text


def test_unmapped_external_admin_claims_do_not_grant_privilege():
    connection = SimpleNamespace(
        role_mapping={},
        group_mapping={},
        default_role="viewer",
        deny_unmapped_roles=False,
    )
    role_claim = NormalizedClaims(
        subject="sub",
        email="person@example.com",
        email_verified=True,
        display_name="Person",
        groups=[],
        role_values=["admin"],
    )
    group_claim = NormalizedClaims(
        subject="sub",
        email="person@example.com",
        email_verified=True,
        display_name="Person",
        groups=["admins"],
        role_values=[],
    )
    assert decide_role(connection, role_claim, current_role=None).role is UserRole.VIEWER
    assert decide_role(connection, group_claim, current_role=None).role is UserRole.VIEWER
    kept = decide_role(connection, role_claim, current_role=UserRole.AGENT)
    assert kept.role is UserRole.AGENT


async def test_scim_cannot_set_a_privileged_role_directly(client, scim, owner_a):
    created = await scim.post(
        "/Users",
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "promoted@acme.test",
            "active": True,
            "roles": [{"value": "owner"}],
        },
    )
    assert created.status_code in {201, 400, 403}, created.text
    if created.status_code == 201:
        assert created.json().get("roles", [{}])[0].get("value", "agent") not in {"owner", "admin"}


async def test_api_key_cannot_widen_its_own_scope_and_dead_keys_fail(client, db, owner_a):
    created = await _api_key(client, owner_a)
    widened = await client.post(
        "/api/api-keys",
        json={
            "name": "wider",
            "scopes": key_service.scopes_from_permissions(
                [Permission.ANALYTICS_READ, Permission.BILLING_WRITE]
            ),
        },
        headers=_bearer(created["secret"]),
    )
    assert widened.status_code == 403, widened.text
    headers = await auth_headers(client, owner_a)
    revoked = await client.delete(f"/api/api-keys/{created['id']}", headers=headers)
    assert revoked.status_code == 204, revoked.text
    assert (await client.get(ANALYTICS, headers=_bearer(created["secret"]))).status_code == 401

    expired = await _api_key(client, owner_a, expires_in_days=1)
    row = (
        await db.execute(
            select(APIKey)
            .where(APIKey.id == uuid.UUID(expired["id"]))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    await db.commit()
    assert (await client.get(ANALYTICS, headers=_bearer(expired["secret"]))).status_code == 401


async def test_disabled_service_account_and_unverified_domain_are_refused(client, owner_a):
    account, secret = await _service_credential(client, owner_a)
    headers = await auth_headers(client, owner_a)
    disabled = await client.post(
        f"/api/service-accounts/{account['id']}/disable", json={}, headers=headers
    )
    assert disabled.status_code == 200, disabled.text
    assert (await client.get(ANALYTICS, headers=_bearer(secret))).status_code in {401, 403}

    domain = await client.post(
        "/api/domains", json={"domain": "unverified.example"}, headers=headers
    )
    assert domain.status_code == 201, domain.text
    enforced = await client.patch(
        f"/api/domains/{domain.json()['id']}",
        json={"enforcement": "require_sso"},
        headers=headers,
    )
    assert enforced.status_code in {400, 409, 422}, enforced.text


async def test_secrets_are_scrubbed_from_audit_and_logs(db, tenant_a, owner_a, monkeypatch):
    captured: list[str] = []

    def info(event, **kwargs):
        captured.append(event + " " + str(kwargs))

    monkeypatch.setattr(log, "info", info)
    await identity_events.emit(
        db,
        AuditAction.LOGIN_FAILURE,
        tenant_id=tenant_a.id,
        actor_user_id=owner_a.id,
        actor_email=owner_a.email,
        detail={
            "password": SENTINEL_PASSWORD,
            "access_token": SENTINEL_TOKEN,
            "client_secret": SENTINEL_SECRET,
            "saml_assertion": SENTINEL_ASSERTION,
            "authorization": "Bearer " + SENTINEL_TOKEN,
            "reason": "bad_password",
        },
        commit=True,
    )
    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_a.id)))
        .scalars()
        .all()
    )
    blob = " ".join(str(row.detail) for row in rows) + " ".join(captured)
    for secret in (SENTINEL_PASSWORD, SENTINEL_TOKEN, SENTINEL_SECRET, SENTINEL_ASSERTION):
        assert secret not in blob
    assert any(row.detail.get("reason") == "bad_password" for row in rows)


async def test_duplicate_scim_create_does_not_make_two_users(client, scim):
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "once@acme.test",
        "externalId": "ext-once",
        "active": True,
    }
    first = await scim.post("/Users", body)
    second = await scim.post("/Users", body)
    assert first.status_code == 201, first.text
    assert second.status_code in {201, 409}, second.text
    listed = await scim.get("/Users")
    matches = [
        item
        for item in listed.json().get("Resources", [])
        if item.get("userName") == "once@acme.test"
    ]
    assert len(matches) == 1
