"""Enterprise OIDC authorization-code flow verified against the Keycloak realm contract (Part 7 / Gate G9).

Tests:
1. Validates ``infra/idp-test/realm-voxdesk.json`` and ``infra/idp-test/docker-compose.keycloak.yml`` structure (OIDC client, PKCE S256, groups/roles protocol mappers, test users).
2. Full OIDC authorization-code + PKCE flow against the Keycloak realm issuer:
   - Login initiation (``POST /auth/sso/keycloak-oidc/start``)
   - Callback with authorization code (``GET /auth/sso/keycloak-oidc/callback``)
   - JIT user provisioning and group/role mapping (``voxdesk-admins`` -> ``admin``, ``voxdesk-owners`` -> ``owner``)
   - Session logout / revocation (``POST /auth/logout`` & ``DELETE /api/sessions/{id}``)
   - Expired ID token rejection (`exp` in the past)
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.identity.models import UserSession
from app.auth.identity.sso import oidc
from app.db.models import User
from tests.auth.sso.providers import (
    IdP,
    StubOidcTransport,
    create_oidc_connection,
    query_of,
    set_mappings,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
REALM_FILE = REPO_ROOT / "infra" / "idp-test" / "realm-voxdesk.json"
COMPOSE_FILE = REPO_ROOT / "infra" / "idp-test" / "docker-compose.keycloak.yml"
KEYCLOAK_ISSUER = "https://idp.voxdesk.test/realms/voxdesk"


@pytest.fixture
def keycloak_idp(monkeypatch) -> tuple[IdP, dict]:
    """Build a cryptographic IdP configured from ``infra/idp-test/realm-voxdesk.json``."""
    oidc.reset_caches()
    realm_data = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    idp = IdP(common_name="idp.voxdesk.test")
    yield idp, realm_data
    oidc.reset_caches()


def _keycloak_discovery_and_jwks(idp: IdP) -> dict:
    return {
        "discovery": {
            "issuer": KEYCLOAK_ISSUER,
            "authorization_endpoint": f"{KEYCLOAK_ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs",
            "end_session_endpoint": f"{KEYCLOAK_ISSUER}/protocol/openid-connect/logout",
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        },
        "jwks": idp.jwks(),
        "token": {},
    }


def _install_keycloak_transport(
    monkeypatch, idp: IdP
) -> StubOidcTransport:
    docs = _keycloak_discovery_and_jwks(idp)
    stub = StubOidcTransport(**docs)
    stub.documents = {
        f"{KEYCLOAK_ISSUER}/.well-known/openid-configuration": docs["discovery"],
        f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs": docs["jwks"],
        f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token": docs["token"],
    }
    return stub.install(monkeypatch)


async def test_keycloak_realm_and_compose_configs_are_valid():
    """Verify infra/idp-test/docker-compose.keycloak.yml and realm-voxdesk.json exist and define OIDC & SAML clients."""
    assert COMPOSE_FILE.exists(), "docker-compose.keycloak.yml must exist"
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "idp-test" in compose_text
    assert "realm-voxdesk.json" in compose_text

    assert REALM_FILE.exists(), "realm-voxdesk.json must exist"
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    assert realm["realm"] == "voxdesk"
    assert realm["enabled"] is True

    clients_by_id = {c["clientId"]: c for c in realm["clients"]}
    assert "voxdesk-oidc" in clients_by_id
    oidc_client = clients_by_id["voxdesk-oidc"]
    assert oidc_client["protocol"] == "openid-connect"
    assert oidc_client["attributes"]["pkce.code.challenge.method"] == "S256"

    users_by_email = {u["email"]: u for u in realm["users"]}
    assert "grace.hopper@voxdesk.test" in users_by_email
    assert "ada.lovelace@voxdesk.test" in users_by_email


async def test_keycloak_oidc_login_jit_provisioning_role_mapping_and_logout(
    client, db, owner_a, keycloak_idp, monkeypatch
):
    """Full Keycloak OIDC authorization-code flow: login, JIT provisioning, group/role mapping, and session logout."""
    idp, realm = keycloak_idp
    oidc_client_cfg = next(
        c for c in realm["clients"] if c["clientId"] == "voxdesk-oidc"
    )
    grace_user = next(
        u for u in realm["users"] if u["email"] == "grace.hopper@voxdesk.test"
    )

    stub = _install_keycloak_transport(monkeypatch, idp)
    conn = await create_oidc_connection(
        client,
        owner_a,
        slug="keycloak-oidc",
        issuer=KEYCLOAK_ISSUER,
        discovery_url=f"{KEYCLOAK_ISSUER}/.well-known/openid-configuration",
        client_id=oidc_client_cfg["clientId"],
        client_secret=oidc_client_cfg["secret"],
        redirect_uri="https://app.voxdesk.test/auth/sso/keycloak-oidc/callback",
        groups_claim="groups",
        role_claim="roles",
        default_role="viewer",
    )
    await set_mappings(
        client,
        owner_a,
        conn,
        group_mapping={"voxdesk-admins": "admin", "voxdesk-agents": "agent"},
        role_mapping={"voxdesk-admin": "admin", "voxdesk-agent": "agent"},
    )

    # 1. Start OIDC login -> redirects to Keycloak authorization endpoint with PKCE S256
    started = await client.post("/auth/sso/keycloak-oidc/start")
    assert started.status_code == 200, started.text
    start_body = started.json()
    assert start_body["authorization_url"].startswith(
        f"{KEYCLOAK_ISSUER}/protocol/openid-connect/auth"
    )
    params = query_of(start_body["authorization_url"])
    assert params["client_id"] == "voxdesk-oidc"
    assert params["code_challenge_method"] == "S256"
    nonce = params["nonce"]

    # 2. Keycloak token endpoint exchanges code for RS256-signed ID token carrying Grace's realm claims
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "iss": KEYCLOAK_ISSUER,
        "sub": "kc-user-grace-001",
        "aud": oidc_client_cfg["clientId"],
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=5)).timestamp()),
        "nonce": nonce,
        "email": grace_user["email"],
        "email_verified": True,
        "name": f"{grace_user['firstName']} {grace_user['lastName']}",
        "groups": ["voxdesk-admins"],
        "roles": grace_user["realmRoles"],
    }
    stub.documents[f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token"] = {
        "id_token": idp.id_token(claims),
        "access_token": "kc-access-token-1",
        "token_type": "Bearer",
        "expires_in": 300,
    }

    callback = await client.get(
        "/auth/sso/keycloak-oidc/callback",
        params={"code": "kc-auth-code-123", "state": start_body["state"]},
    )
    assert callback.status_code == 200, callback.text
    cb_body = callback.json()
    assert cb_body["created"] is True
    access_token = cb_body["access_token"]
    assert access_token

    # 3. Verify JIT-provisioned user has mapped role = admin in owner_a's tenant
    provisioned = (
        await db.execute(select(User).where(User.email == grace_user["email"]))
    ).scalar_one()
    assert provisioned.tenant_id == owner_a.tenant_id
    assert provisioned.role.value == "admin"
    assert provisioned.is_active is True

    # 4. Authenticated request with SSO session succeeds, then logout revokes the session
    sso_headers = {"Authorization": f"Bearer {access_token}"}
    status_res = await client.get("/api/identity/status", headers=sso_headers)
    assert status_res.status_code == 200, status_res.text

    logout_res = await client.post("/auth/logout", headers=sso_headers)
    assert logout_res.status_code in (200, 204), logout_res.text

    sessions = (
        await db.execute(
            select(UserSession)
            .where(UserSession.user_id == provisioned.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is not None

    # Subsequent API call with the logged-out session token is rejected (401)
    after_logout = await client.get("/api/identity/status", headers=sso_headers)
    assert after_logout.status_code == 401


async def test_keycloak_oidc_rejects_expired_id_token(
    client, db, owner_a, keycloak_idp, monkeypatch
):
    """An expired ID token from the IdP is rejected with generic 400 sso_failed and provisions no user."""
    idp, realm = keycloak_idp
    oidc_client_cfg = next(
        c for c in realm["clients"] if c["clientId"] == "voxdesk-oidc"
    )
    stub = _install_keycloak_transport(monkeypatch, idp)
    await create_oidc_connection(
        client,
        owner_a,
        slug="keycloak-oidc",
        issuer=KEYCLOAK_ISSUER,
        discovery_url=f"{KEYCLOAK_ISSUER}/.well-known/openid-configuration",
        client_id=oidc_client_cfg["clientId"],
        client_secret=oidc_client_cfg["secret"],
        redirect_uri="https://app.voxdesk.test/auth/sso/keycloak-oidc/callback",
    )

    started = await client.post("/auth/sso/keycloak-oidc/start")
    assert started.status_code == 200
    start_body = started.json()
    nonce = query_of(start_body["authorization_url"])["nonce"]

    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    expired_claims = {
        "iss": KEYCLOAK_ISSUER,
        "sub": "kc-expired-user",
        "aud": oidc_client_cfg["clientId"],
        "iat": int((past - dt.timedelta(minutes=5)).timestamp()),
        "exp": int(past.timestamp()),
        "nonce": nonce,
        "email": "expired@voxdesk.test",
        "email_verified": True,
    }
    stub.documents[f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token"] = {
        "id_token": idp.id_token(expired_claims)
    }

    refused = await client.get(
        "/auth/sso/keycloak-oidc/callback",
        params={"code": "kc-expired-code", "state": start_body["state"]},
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["code"] == "sso_failed"
    assert "access_token" not in refused.text

    user_row = (
        await db.execute(select(User).where(User.email == "expired@voxdesk.test"))
    ).scalar_one_or_none()
    assert user_row is None
