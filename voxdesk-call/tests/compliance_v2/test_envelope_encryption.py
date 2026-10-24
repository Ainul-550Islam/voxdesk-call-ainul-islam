"""Envelope encryption, the development KMS and the rekey job.

The cryptography here is real: a local master key file wraps a per-record data
key, the record ciphertext is AES-GCM with an associated-data binding, and a
rekey re-wraps the data key without ever putting plaintext back on disk. The
tests assert the properties an operator relies on — a value cannot be decrypted
in the wrong tenant or the wrong column, a rewrapped token still decrypts, and a
rekey job reports what it touched.
"""

from __future__ import annotations

import base64

import pytest

from app.compliance_v2.kms import envelope, get_kms, provider_name
from app.compliance_v2.kms.base import (
    DataKey,
    KmsPermanentError,
    encrypt_context,
)
from app.compliance_v2.kms.local_dev import LocalDevKms


async def test_local_dev_round_trip_uses_an_envelope_token(compliance_settings):
    kms = get_kms(compliance_settings)
    assert provider_name(compliance_settings) == "local_dev"

    token = await envelope.encrypt_value(
        "client-secret-value",
        tenant_id="tenant-1",
        column="crm_integrations.credentials_encrypted",
        provider=kms,
    )

    assert token.startswith("env1.local_dev.")
    assert "client-secret-value" not in token
    assert (
        await envelope.decrypt_value(
            token,
            tenant_id="tenant-1",
            column="crm_integrations.credentials_encrypted",
            provider=kms,
        )
        == "client-secret-value"
    )


async def test_envelope_binds_the_tenant_and_the_column(compliance_settings):
    kms = get_kms(compliance_settings)
    token = await envelope.encrypt_value(
        "value", tenant_id="tenant-1", column="webhook_subscriptions.secret_envelope", provider=kms
    )

    with pytest.raises(envelope.EnvelopeAuthError):
        await envelope.decrypt_value(
            token,
            tenant_id="tenant-2",
            column="webhook_subscriptions.secret_envelope",
            provider=kms,
        )
    with pytest.raises(envelope.EnvelopeAuthError):
        await envelope.decrypt_value(
            token, tenant_id="tenant-1", column="mfa_factors.secret_encrypted", provider=kms
        )


async def test_tampered_ciphertext_is_refused(compliance_settings):
    kms = get_kms(compliance_settings)
    token = await envelope.encrypt_value("value", tenant_id="t", column="c", provider=kms)
    prefix, provider, wrapped, nonce, ciphertext = token.split(".")
    raw = bytearray(base64.urlsafe_b64decode(ciphertext + "=" * (-len(ciphertext) % 4)))
    # Flip a byte the GCM tag covers; the last base64 character can carry
    # unused padding bits, so tampering there is not reliably observable.
    raw[0] ^= 0x01
    broken = ".".join(
        [prefix, provider, wrapped, nonce, base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")]
    )

    with pytest.raises(envelope.EnvelopeAuthError):
        await envelope.decrypt_value(broken, tenant_id="t", column="c", provider=kms)


async def test_parse_and_classification_helpers(compliance_settings):
    kms = get_kms(compliance_settings)
    token = await envelope.encrypt_value("value", tenant_id="t", column="c", provider=kms)

    header = envelope.parse(token)

    assert header.provider == "local_dev"
    assert envelope.is_envelope(token) is True
    assert envelope.is_envelope("plain-text") is False
    assert envelope.is_legacy("v1.local_dev.abc.def") is True
    assert envelope.needs_rewrap(token, provider_name="aws") is True
    assert envelope.needs_rewrap(token, provider_name="local_dev") is False


async def test_rotate_value_produces_a_fresh_data_key(compliance_settings):
    kms = get_kms(compliance_settings)

    first = await envelope.encrypt_value("value", tenant_id="t", column="c", provider=kms)
    rotated = await envelope.rotate_value(first, tenant_id="t", column="c", provider=kms)

    assert rotated != first
    assert await envelope.decrypt_value(rotated, tenant_id="t", column="c", provider=kms) == "value"


async def test_health_reports_the_key_id(tmp_path):
    kms = LocalDevKms(tmp_path / "k.key", environment="development")
    health = await kms.health()

    assert health.ok is True
    assert health.key_id == kms.key_id
    assert health.as_dict()["provider"] == "local_dev"


async def test_data_key_destroy_clears_the_plaintext(compliance_settings):
    kms = get_kms(compliance_settings)
    data_key = await kms.generate_data_key(
        context=encrypt_context(tenant_id="t", column="c")
    )

    assert isinstance(data_key, DataKey)
    assert len(data_key.plaintext) == 32
    data_key.destroy()
    assert data_key.plaintext == b""


async def test_unwrapping_with_the_wrong_context_is_permanent(compliance_settings):
    kms = get_kms(compliance_settings)
    key = await kms.generate_data_key(context=encrypt_context(tenant_id="t1", column="c"))

    with pytest.raises(KmsPermanentError):
        await kms.unwrap_data_key(key.wrapped, context=encrypt_context(tenant_id="t2", column="c"))


async def test_rewrap_keeps_the_material_but_changes_the_wrapping(compliance_settings):
    kms = get_kms(compliance_settings)
    context = encrypt_context(tenant_id="t", column="c")
    key = await kms.generate_data_key(context=context)

    rewrapped = await kms.rewrap_data_key(key.wrapped, context=context)

    assert rewrapped != key.wrapped
    assert await kms.unwrap_data_key(rewrapped, context=context) == key.plaintext


# --------------------------------------------------------------- rekey job ---


