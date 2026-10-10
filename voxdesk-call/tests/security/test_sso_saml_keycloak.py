"""Enterprise SAML 2.0 login flow verified against the Keycloak realm contract (Part 7 / Gate G9).

SAML 2.0 is implemented in ``app/auth/identity/sso/saml.py`` and documented in
``docs/SSO-SAML.md``. This suite verifies the SAML SP/IdP integration using the
Keycloak SAML client configuration from ``infra/idp-test/realm-voxdesk.json``:
- Deflated ``AuthnRequest`` generation on ``POST /auth/sso/keycloak-saml/start``
- XML-DSig signed SAML 2.0 Response/Assertion verification on ``POST /auth/sso/keycloak-saml/acs``
- JIT provisioning + Keycloak group/role attribute mapping
- Assertion replay & signature tampering rejection
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.identity.models import UserSession
from app.db.models import User
from tests.auth.sso.providers import (
    IdP,
    create_saml_connection,
    post_assertion,
    set_mappings,
    start_saml_login,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
REALM_FILE = REPO_ROOT / "infra" / "idp-test" / "realm-voxdesk.json"
KEYCLOAK_SAML_ENTITY = "https://idp.voxdesk.test/realms/voxdesk"
SP_ENTITY_ID = "https://app.voxdesk.test/auth/sso/keycloak-saml/metadata"
ACS_URL = "https://app.voxdesk.test/auth/sso/keycloak-saml/acs"


@pytest.fixture
def keycloak_saml_idp() -> tuple[IdP, dict]:
    realm_data = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    return IdP(common_name="idp.voxdesk.test"), realm_data


async def test_keycloak_saml_login_jit_provisioning_and_role_mapping(
    client, db, owner_a, keycloak_saml_idp
):
    """Signed SAML assertion from Keycloak realm provisions user with mapped role and creates an SSO session."""
    idp, realm = keycloak_saml_idp
    saml_client_cfg = next(
        c for c in realm["clients"] if c["protocol"] == "saml"
    )
    ada_user = next(
        u for u in realm["users"] if u["email"] == "ada.lovelace@voxdesk.test"
    )

    conn = await create_saml_connection(
        client,
        owner_a,
        idp,
        slug="keycloak-saml",
        idp_entity_id=KEYCLOAK_SAML_ENTITY,
        idp_sso_url=f"{KEYCLOAK_SAML_ENTITY}/protocol/saml",
        idp_slo_url=f"{KEYCLOAK_SAML_ENTITY}/protocol/saml",
        sp_entity_id=saml_client_cfg["clientId"],
        acs_url=ACS_URL,
        default_role="viewer",
    )
    await set_mappings(
        client,
        owner_a,
        conn,
        group_mapping={"voxdesk-owners": "admin", "voxdesk-admins": "admin"},
    )

    started = await start_saml_login(client, slug="keycloak-saml")
    assert started["request_id"].startswith(("_", "id-"))

    signed_xml = idp.response(
        request_id=started["request_id"],
        entity=KEYCLOAK_SAML_ENTITY,
        audience=SP_ENTITY_ID,
        recipient=ACS_URL,
        destination=ACS_URL,
        email=ada_user["email"],
        name_id=ada_user["email"],
        groups=("voxdesk-owners",),
    )
    completed = await post_assertion(
        client,
        idp,
        started=started,
        slug="keycloak-saml",
        response=signed_xml,
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["created"] is True
    assert body["access_token"]

    provisioned = (
        await db.execute(select(User).where(User.email == ada_user["email"]))
    ).scalar_one()
    assert provisioned.tenant_id == owner_a.tenant_id
    assert provisioned.role.value == "admin"
    assert provisioned.is_active is True

    sessions = (
        await db.execute(
            select(UserSession).where(UserSession.user_id == provisioned.id)
        )
    ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].auth_method == "sso"


async def test_keycloak_saml_rejects_replay_and_tampered_assertions(
    client, db, owner_a, keycloak_saml_idp
):
    """Replayed assertion IDs and tampered XML payloads are rejected with 400 sso_failed."""
    idp, _ = keycloak_saml_idp
    await create_saml_connection(
        client,
        owner_a,
        idp,
        slug="keycloak-saml",
        idp_entity_id=KEYCLOAK_SAML_ENTITY,
        idp_sso_url=f"{KEYCLOAK_SAML_ENTITY}/protocol/saml",
        sp_entity_id=SP_ENTITY_ID,
        acs_url=ACS_URL,
    )

    first = await start_saml_login(client, slug="keycloak-saml")
    assertion_id = f"_{uuid.uuid4().hex}"
    valid_xml = idp.response(
        assertion_id=assertion_id,
        request_id=first["request_id"],
        entity=KEYCLOAK_SAML_ENTITY,
        audience=SP_ENTITY_ID,
        recipient=ACS_URL,
        destination=ACS_URL,
        email="alan.turing@voxdesk.test",
    )

    # 1. Tampered email in signed XML -> signature verification fails
    tampered_xml = valid_xml.replace(
        b"alan.turing@voxdesk.test", b"attacker@voxdesk.test"
    )
    tampered_res = await client.post(
        "/auth/sso/keycloak-saml/acs",
        data={
            "SAMLResponse": base64.b64encode(tampered_xml).decode("ascii"),
            "RelayState": first["state"],
        },
    )
    assert tampered_res.status_code == 400
    assert tampered_res.json()["code"] == "sso_failed"

    # 2. First legitimate use succeeds
    fresh = await start_saml_login(client, slug="keycloak-saml")
    fresh_xml = idp.response(
        assertion_id=assertion_id,
        request_id=fresh["request_id"],
        entity=KEYCLOAK_SAML_ENTITY,
        audience=SP_ENTITY_ID,
        recipient=ACS_URL,
        destination=ACS_URL,
        email="alan.turing@voxdesk.test",
    )
    ok_res = await post_assertion(
        client, idp, started=fresh, slug="keycloak-saml", response=fresh_xml
    )
    assert ok_res.status_code == 200

    # 3. Replay of the same assertion_id on a new login attempt -> rejected
    replay_attempt = await start_saml_login(client, slug="keycloak-saml")
    replay_xml = idp.response(
        assertion_id=assertion_id,
        request_id=replay_attempt["request_id"],
        entity=KEYCLOAK_SAML_ENTITY,
        audience=SP_ENTITY_ID,
        recipient=ACS_URL,
        destination=ACS_URL,
        email="alan.turing@voxdesk.test",
    )
    replay_res = await post_assertion(
        client,
        idp,
        started=replay_attempt,
        slug="keycloak-saml",
        response=replay_xml,
    )
    assert replay_res.status_code == 400
    assert replay_res.json()["code"] == "sso_failed"
