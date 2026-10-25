import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import PermissionDenied
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import ContactCenterV2Settings
from app.contact_center_v2.softphone.service import expire_sessions
from app.contact_center_v2.softphone.signaling import consume_websocket_ticket, issue_websocket_ticket
from app.contact_center_v2.softphone.token_service import (
    mint_token_bundle,
    revoke_softphone_sessions,
)
from app.db.contact_center_v2_models import SoftphoneSession


def jwt_payload(token: str) -> dict:
    encoded = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


async def test_voice_token_is_short_lived_and_carries_only_the_configured_voice_grant(
    db: AsyncSession,
    softphone_registration: SoftphoneSession,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.contact_center_v2.softphone.service.core_settings.twilio_account_sid", "AC-test"
    )
    config = ContactCenterV2Settings(
        softphone_twilio_api_key_sid="SK-test",
        softphone_twilio_api_key_secret="test-api-secret",
        softphone_twilio_twiml_app_sid="AP-test",
        softphone_token_ttl_seconds=300,
        stun_url="stun:stun.example.test",
    )
    bundle = mint_token_bundle(softphone_registration, config)
    payload = jwt_payload(bundle.access_token)
    assert payload["iss"] == "SK-test"
    assert payload["sub"] == "AC-test"
    assert payload["grants"]["identity"] == softphone_registration.client_identity
    assert payload["grants"]["voice"]["outgoing"]["application_sid"] == "AP-test"
    assert payload["grants"]["voice"]["incoming"]["allow"] is True
    assert 0 < payload["exp"] - payload["nbf"] <= 300
    assert bundle.identity == softphone_registration.client_identity
    assert bundle.ice_servers == [{"urls": ["stun:stun.example.test"]}]


async def test_logout_revocation_clears_registration_and_one_time_signaling_credential(
    db: AsyncSession,
    cc2_scope,
    softphone_registration: SoftphoneSession,
):
    ticket = await issue_websocket_ticket(db, softphone_registration)
    softphone_registration.active_device_key = "device-active"
    await db.commit()
    revoked = await revoke_softphone_sessions(
        db, tenant_id=cc2_scope.tenant.id, user_id=softphone_registration.user_id
    )
    await db.commit()
    assert revoked == 1
    await db.refresh(softphone_registration)
    assert softphone_registration.status == "revoked"
    assert softphone_registration.revoked_at is not None
    assert softphone_registration.active_device_key is None
    assert softphone_registration.ws_ticket_hash == ""
    with pytest.raises(PermissionDenied):
        await consume_websocket_ticket(db, ticket, tenant_id=cc2_scope.tenant.id)
    assert await revoke_softphone_sessions(
        db, tenant_id=cc2_scope.tenant.id, user_id=softphone_registration.user_id
    ) == 0


async def test_session_expiry_releases_the_single_active_device_key(
    db: AsyncSession, softphone_registration: SoftphoneSession,
):
    softphone_registration.active_device_key = "expired-device"
    softphone_registration.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    assert await expire_sessions(db) == 1
    await db.commit()
    await db.refresh(softphone_registration)
    assert softphone_registration.status == "expired"
    assert softphone_registration.active_device_key is None


async def test_inactive_or_expired_registration_cannot_mint_new_credentials(
    db: AsyncSession, softphone_registration: SoftphoneSession,
):
    softphone_registration.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()
    with pytest.raises(ValueError, match="inactive"):
        mint_token_bundle(softphone_registration)


async def test_canonical_logout_and_logout_all_revoke_cc2_in_the_same_transaction(
    db: AsyncSession, cc2_scope, agent_a, softphone_registration: SoftphoneSession,
):
    from app.auth.identity import policies
    from app.auth.identity import sessions as identity_sessions

    policy = await policies.load_policy(db, cc2_scope.tenant.id)
    auth_session = await identity_sessions.create_session(
        db, agent_a, policy=policy, commit=True
    )
    await identity_sessions.revoke_session(db, auth_session, reason="logout", commit=True)
    await db.refresh(softphone_registration)
    assert softphone_registration.status == "revoked"

    # The SCIM/logout-all path can have no remaining UserSession rows. Its
    # canonical empty-row branch still revokes a stranded softphone credential.
    softphone_registration.status = "active"
    softphone_registration.revoked_at = None
    softphone_registration.expires_at = utcnow() + timedelta(minutes=5)
    await db.commit()
    await identity_sessions.revoke_all_for_user(
        db, user_id=agent_a.id, reason="scim_deactivated", commit=True
    )
    await db.refresh(softphone_registration)
    assert softphone_registration.status == "revoked"
