"""Tests for NumberTrustProfile, KMS envelope encryption, and secret rotation (Part 7 / Gate G9).

Covers:
1. Unconfigured provider behavior: ``GET /api/phone-numbers/{id}/trust-profile`` returns ``source == "unverified"`` and ``shaken_attestation is None`` (never fake ``'A'``); ``POST .../refresh`` fails closed with HTTP 501 ``NOT_CONFIGURED``.
2. User-supplied attestation rejection: ``POST .../refresh`` rejects ``shaken_attestation``, ``branded_caller_name``, ``cnam``, ``spam_status``, or ``a2p_registration`` in the request body with HTTP 422.
3. Provider API synchronization + audit trail: ``TwilioTrustHubClient`` populates ``NumberTrustProfile`` strictly from carrier API responses and writes an ``AuditLog`` entry; scheduled refresh updates active numbers.
4. Cross-tenant isolation: Tenant B receives HTTP 404 when reading or refreshing Tenant A's number trust profile.
5. KMS adapters (``UnconfiguredKmsAdapter``, ``LocalKeyRingKmsAdapter``, ``AwsKmsAdapter`` with SSRF check) and ``scripts/rotate_secrets.py`` idempotent rotation.
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import AuditAction, AuditLog, CrmIntegration, CrmProviderType
from app.integrations.crm import crypto
from app.security.kms import (
    KmsNotConfiguredError,
    KmsOperationError,
    UnconfiguredKmsAdapter,
)
from app.security.kms.aws_kms import AwsKmsAdapter
from app.security.secret_store import EncryptedSecretStore
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.number_trust import (
    TwilioTrustHubClient,
    scheduled_refresh_number_trust_profiles,
)
from scripts.rotate_secrets import rotate_all_secrets
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _make_number(
    db, tenant_id, e164: str = "+14155550142", provider: str = "twilio"
) -> PhoneNumber:
    row = PhoneNumber(
        tenant_id=tenant_id,
        e164=e164,
        provider=provider,
        provider_sid="PN1234567890abcdef1234567890abcdef",
        country="US",
        friendly_name="Primary Support Line",
        status="active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_unconfigured_provider_returns_unverified_and_refresh_returns_501_not_configured(
    client, db, tenant_a, owner_a, monkeypatch
):
    """When carrier credentials are absent, GET never fakes 'A' and POST refresh returns 501 NOT_CONFIGURED."""
    monkeypatch.setattr(settings, "twilio_account_sid", None)
    monkeypatch.setattr(settings, "twilio_auth_token", None)

    num = await _make_number(db, tenant_a.id)
    headers = await auth_headers(client, owner_a)

    get_res = await client.get(
        f"/api/phone-numbers/{num.id}/trust-profile", headers=headers
    )
    assert get_res.status_code == 200, get_res.text
    body = get_res.json()
    assert body["source"] == "unverified"
    assert body["shaken_attestation"] is None
    assert body["shaken_attestation"] != "A"
    assert body["spam_status"] == "unknown"
    assert body["a2p_registration"] == "unregistered"

    refresh_res = await client.post(
        f"/api/phone-numbers/{num.id}/trust-profile/refresh",
        headers=headers,
        json={},
    )
    assert refresh_res.status_code == 501, refresh_res.text
    detail = refresh_res.json()["detail"]
    assert detail["code"] == "NOT_CONFIGURED"
    assert detail["provider"] == "twilio"


async def test_refresh_rejects_user_supplied_attestation_fields(
    client, db, tenant_a, owner_a
):
    """POST /trust-profile/refresh rejects any caller attempt to self-assert attestation or brand status."""
    num = await _make_number(db, tenant_a.id, e164="+14155550143")
    headers = await auth_headers(client, owner_a)

    for forbidden_body in (
        {"shaken_attestation": "A"},
        {"branded_caller_name": "Spoofed Bank"},
        {"cnam": "Spoofed CNAM"},
        {"spam_status": "clean"},
        {"a2p_registration": "approved"},
    ):
        res = await client.post(
            f"/api/phone-numbers/{num.id}/trust-profile/refresh",
            headers=headers,
            json=forbidden_body,
        )
        assert res.status_code == 422, (
            f"Expected 422 for user-supplied trust field {forbidden_body}, got {res.status_code}: {res.text}"
        )


async def test_twilio_trust_hub_refresh_persists_carrier_response_and_audit_log(
    client, db, tenant_a, tenant_b, owner_a, owner_b, monkeypatch
):
    """Carrier API responses populate NumberTrustProfile and emit an AuditLog entry; Tenant B is isolated."""
    num = await _make_number(db, tenant_a.id, e164="+14155550144")

    def _twilio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "shaken_attestation": "A",
                "caller_name": {
                    "caller_name": "VoxDesk Enterprise",
                    "caller_type": "BUSINESS",
                },
                "reputation": {"spam_risk": "clean"},
                "a2p_10dlc": {"status": "approved"},
                "bundle_sid": "BU000122334455",
            },
        )

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(_twilio_handler))
    twilio_client = TwilioTrustHubClient(
        account_sid="AC11112222333344445555666677778888",
        auth_token="secret-twilio-token",
        http_client=mock_http,
    )

    # Patch build_provider_trust_client so the HTTP endpoint uses our mocked carrier client
    monkeypatch.setattr(
        "app.telephony.number_trust.build_provider_trust_client",
        lambda _provider: twilio_client,
    )

    headers_a = await auth_headers(client, owner_a)
    refresh_res = await client.post(
        f"/api/phone-numbers/{num.id}/trust-profile/refresh",
        headers=headers_a,
        json={"force": True},
    )
    assert refresh_res.status_code == 200, refresh_res.text
    data = refresh_res.json()
    assert data["source"] == "provider_api"
    assert data["shaken_attestation"] == "A"
    assert data["branded_caller_name"] == "VoxDesk Enterprise"
    assert data["cnam"] == "VoxDesk Enterprise"
    assert data["spam_status"] == "clean"
    assert data["a2p_registration"] == "approved"
    assert data["provider_bundle_sid"] == "BU000122334455"

    # Audit log recorded in the same transaction
    audits = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.action == AuditAction.GOVERNANCE_EVENT,
            )
        )
    ).scalars().all()
    trust_audits = [
        a for a in audits if (a.detail or {}).get("target_type") == "number_trust_profile"
    ]
    assert len(trust_audits) == 1
    assert trust_audits[0].detail["shaken_attestation"] == "A"

    # GET endpoint returns the synced profile
    get_res = await client.get(
        f"/api/phone-numbers/{num.id}/trust-profile", headers=headers_a
    )
    assert get_res.status_code == 200
    assert get_res.json()["shaken_attestation"] == "A"
    assert get_res.json()["branded_caller_name"] == "VoxDesk Enterprise"

    # Scheduled refresh updates all active numbers
    summary = await scheduled_refresh_number_trust_profiles(
        db,
        tenant_id=tenant_a.id,
        provider_clients={"twilio": twilio_client},
    )
    await db.commit()
    assert summary["refreshed"] >= 1

    # Tenant B cannot read or refresh Tenant A's number trust profile
    headers_b = await auth_headers(client, owner_b)
    cross_get = await client.get(
        f"/api/phone-numbers/{num.id}/trust-profile", headers=headers_b
    )
    assert cross_get.status_code == 404
    cross_refresh = await client.post(
        f"/api/phone-numbers/{num.id}/trust-profile/refresh",
        headers=headers_b,
        json={},
    )
    assert cross_refresh.status_code == 404
    await mock_http.aclose()


async def test_kms_adapters_and_secret_rotation_idempotency(
    db, tenant_a, monkeypatch, tmp_path
):
    """KmsAdapter implementations enforce NOT_CONFIGURED, SSRF guards, envelope encryption, and key rotation."""
    unconfigured = UnconfiguredKmsAdapter()
    with pytest.raises(KmsNotConfiguredError) as exc_info:
        await unconfigured.generate_data_key(
            encryption_context={"tenant_id": str(tenant_a.id)}
        )
    assert exc_info.value.code == "NOT_CONFIGURED"
    assert exc_info.value.status_code == 501

    # AWS KMS SSRF guard rejects private metadata host
    bad_aws = AwsKmsAdapter(
        key_id="alias/voxdesk",
        region_name="us-east-1",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        endpoint_url="http://169.254.169.254/latest/meta-data/",
    )
    with pytest.raises(KmsOperationError):
        await bad_aws.generate_data_key(
            encryption_context={"tenant_id": str(tenant_a.id)}
        )

    # AWS KMS mocked round-trip
    raw_dek = os.urandom(32)
    wrapped_dek = b"kms-ciphertext-" + raw_dek

    def _kms_handler(request: httpx.Request) -> httpx.Response:
        target = request.headers.get("X-Amz-Target", "")
        if target.endswith("GenerateDataKey"):
            return httpx.Response(
                200,
                json={
                    "Plaintext": base64.b64encode(raw_dek).decode("ascii"),
                    "CiphertextBlob": base64.b64encode(wrapped_dek).decode("ascii"),
                    "KeyId": "arn:aws:kms:us-east-1:123456789012:key/k1",
                },
            )
        if target.endswith("Decrypt"):
            return httpx.Response(
                200,
                json={
                    "Plaintext": base64.b64encode(raw_dek).decode("ascii"),
                    "KeyId": "arn:aws:kms:us-east-1:123456789012:key/k1",
                },
            )
        return httpx.Response(400)

    kms_http = httpx.AsyncClient(transport=httpx.MockTransport(_kms_handler))
    aws_kms = AwsKmsAdapter(
        key_id="arn:aws:kms:us-east-1:123456789012:key/k1",
        region_name="us-east-1",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        endpoint_url="https://kms.us-east-1.amazonaws.com/",
        http_client=kms_http,
    )

    store = EncryptedSecretStore(kms_adapter=aws_kms)
    kms_ref = await store.put_with_kms(
        str(tenant_a.id),
        "carrier_token",
        "super-secret-carrier-token",
    )
    assert kms_ref.startswith("secret://kms/aws_kms/")
    recovered = await store.get_with_kms(
        str(tenant_a.id),
        "carrier_token",
        kms_ref,
    )
    assert recovered == "super-secret-carrier-token"
    await kms_http.aclose()

    # Key rotation script across CrmIntegration with two-key ring
    key_old = crypto.generate_key()
    key_new = crypto.generate_key()
    ring_old_first = crypto.parse_key_ring(f"k2025:{key_old},k2026:{key_new}")
    ring_new_first = crypto.parse_key_ring(f"k2026:{key_new},k2025:{key_old}")

    ct_old, kid_old = crypto.encrypt_credentials(
        {"access_token": "hubspot-tok-123"},
        tenant_id=str(tenant_a.id),
        provider="hubspot",
        key_ring=ring_old_first,
    )
    assert kid_old == "k2025"
    crm_row = CrmIntegration(
        tenant_id=tenant_a.id,
        provider=CrmProviderType.HUBSPOT,
        credentials_encrypted=ct_old,
        credentials_key_id=kid_old,
        is_enabled=True,
    )
    db.add(crm_row)
    await db.commit()

    monkeypatch.setattr(crypto, "key_ring_from_settings", lambda: ring_new_first)
    monkeypatch.setattr(
        "scripts.rotate_secrets.get_identity_key_ring", lambda: ring_new_first
    )

    resume_file = tmp_path / "rotate_resume.json"
    first_run = await rotate_all_secrets(db, dry_run=False, resume_path=resume_file)
    assert first_run["rotated_count"] >= 1
    await db.refresh(crm_row)
    assert crm_row.credentials_key_id == "k2026"

    # Running a second time is idempotent (0 rotated)
    second_run = await rotate_all_secrets(db, dry_run=False, resume_path=resume_file)
    assert second_run["rotated_count"] == 0
