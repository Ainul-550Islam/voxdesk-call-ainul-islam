"""Customer-managed keys, exercised rather than promised (PART 16, P16-08).

`docs/DUE_DILIGENCE/CMK_AND_KEYS.md` claims three things; each has a test here so
the claim can be checked instead of believed:

1. **Envelope encryption round-trips through the real adapter stack** — a DEK is
   wrapped and unwrapped by `LocalKeyRingKmsAdapter`, which is the same protocol
   AWS KMS and Vault Transit implement.
2. **The encryption context is binding.** A wrapped DEK moved to another tenant's
   context fails authentication. Without this, a CMK is decoration: the customer
   key would wrap a DEK that any tenant could unwrap.
3. **An unconfigured deployment fails closed.** `get_kms_adapter()` returns
   `UnconfiguredKmsAdapter`, which raises `NOT_CONFIGURED` (HTTP 501) rather than
   silently storing a plaintext DEK or inventing a local key.

The AWS and Vault adapters are driven through an httpx mock transport, so the
request shape (action name, encryption context, key id) is asserted without a
network call or a real cloud account — which is the only honest way to claim
"tested" for a credential the repository does not have.
"""

from __future__ import annotations

import base64
import json
import os

import httpx
import pytest

from app.integrations.crm import crypto
from app.security.kms import (
    LocalKeyRingKmsAdapter,
    KmsNotConfiguredError,
    KmsOperationError,
    UnconfiguredKmsAdapter,
    WrappedDataKey,
    get_kms_adapter,
)
from app.security.kms.aws_kms import AwsKmsAdapter, VaultTransitKmsAdapter

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def _key_ring() -> crypto.KeyRing:
    return crypto.parse_key_ring(f"k1:{crypto.generate_key()}")


async def test_an_unconfigured_deployment_fails_closed(monkeypatch):
    for name in ("AWS_KMS_KEY_ID", "VAULT_ADDR", "VAULT_TOKEN", "VAULT_TRANSIT_KEY"):
        monkeypatch.delenv(name, raising=False)
    adapter = get_kms_adapter()
    assert isinstance(adapter, UnconfiguredKmsAdapter)
    assert adapter.is_configured() is False
    with pytest.raises(KmsNotConfiguredError) as exc:
        await adapter.generate_data_key(encryption_context={"tenant_id": TENANT})
    assert exc.value.code == "NOT_CONFIGURED"
    # 501, not 500: "we did not configure this" is not a server fault.
    assert exc.value.status_code == 501


async def test_envelope_round_trip_binds_the_tenant_context():
    adapter = LocalKeyRingKmsAdapter(_key_ring())
    dek, wrapped = await adapter.generate_data_key(
        encryption_context={"tenant_id": TENANT, "purpose": "crm"}
    )
    assert len(dek) == 32
    assert wrapped.provider == "local_keyring"
    assert wrapped.encryption_context["tenant_id"] == TENANT

    recovered = await adapter.unwrap_data_key(
        wrapped, encryption_context={"tenant_id": TENANT, "purpose": "crm"}
    )
    assert recovered == dek


async def test_a_wrapped_key_moved_to_another_tenant_is_refused():
    adapter = LocalKeyRingKmsAdapter(_key_ring())
    dek, wrapped = await adapter.generate_data_key(
        encryption_context={"tenant_id": TENANT, "purpose": "crm"}
    )
    with pytest.raises(KmsOperationError):
        await adapter.unwrap_data_key(
            wrapped, encryption_context={"tenant_id": OTHER_TENANT, "purpose": "crm"}
        )
    # And the right context still works — the refusal above is not corruption.
    assert (
        await adapter.unwrap_data_key(
            wrapped, encryption_context={"tenant_id": TENANT, "purpose": "crm"}
        )
        == dek
    )


async def test_a_different_purpose_is_a_different_key_namespace():
    adapter = LocalKeyRingKmsAdapter(_key_ring())
    _dek, wrapped = await adapter.generate_data_key(
        encryption_context={"tenant_id": TENANT, "purpose": "crm"}
    )
    with pytest.raises(KmsOperationError):
        await adapter.unwrap_data_key(
            wrapped, encryption_context={"tenant_id": TENANT, "purpose": "mfa"}
        )


async def test_a_dek_that_is_not_256_bits_is_refused():
    adapter = LocalKeyRingKmsAdapter(_key_ring())
    with pytest.raises(KmsOperationError):
        await adapter.wrap_data_key(b"too short", encryption_context={"tenant_id": TENANT})


async def test_the_wrapped_token_round_trips_and_keeps_its_context():
    adapter = LocalKeyRingKmsAdapter(_key_ring())
    _dek, wrapped = await adapter.generate_data_key(
        encryption_context={"tenant_id": TENANT, "purpose": "webhooks"}
    )
    token = wrapped.to_token()
    restored = WrappedDataKey.from_token(token)
    assert restored.key_id == wrapped.key_id
    assert restored.provider == "local_keyring"
    assert restored.encryption_context == {"tenant_id": TENANT, "purpose": "webhooks"}
    with pytest.raises(KmsOperationError):
        WrappedDataKey.from_token("not-a-token")


async def test_get_kms_adapter_selects_aws_when_configured(monkeypatch):
    monkeypatch.setenv("AWS_KMS_KEY_ID", "arn:aws:kms:eu-central-1:1234:key/abcd")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-a-real-secret")
    for name in ("VAULT_ADDR", "VAULT_TOKEN", "VAULT_TRANSIT_KEY"):
        monkeypatch.delenv(name, raising=False)
    adapter = get_kms_adapter()
    assert isinstance(adapter, AwsKmsAdapter)
    assert adapter.provider_name == "aws_kms"
    assert adapter.is_configured() is True


async def test_get_kms_adapter_selects_vault_when_configured(monkeypatch):
    monkeypatch.delenv("AWS_KMS_KEY_ID", raising=False)
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "hvs.example")
    monkeypatch.setenv("VAULT_TRANSIT_KEY", "voxdesk-dek")
    adapter = get_kms_adapter()
    assert isinstance(adapter, VaultTransitKmsAdapter)
    assert adapter.provider_name == "vault_transit"


async def test_the_aws_adapter_sends_the_key_id_and_context_it_should():
    """The request shape is the contract with AWS; assert it without a network."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["target"] = request.headers.get("x-amz-target") or request.headers.get("X-Amz-Target")
        seen["body"] = json.loads(request.content or b"{}")
        plaintext = base64.b64encode(b"0" * 32).decode()
        return httpx.Response(200, json={"CiphertextBlob": plaintext, "KeyId": "kid-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = AwsKmsAdapter(
        key_id="arn:aws:kms:eu-central-1:1234:key/abcd",
        region_name="eu-central-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="not-a-real-secret",
        http_client=client,
    )
    wrapped = await adapter.wrap_data_key(
        b"0" * 32, encryption_context={"tenant_id": TENANT, "purpose": "crm"}
    )
    assert wrapped.provider == "aws_kms"
    assert "Encrypt" in (seen["target"] or "")
    assert seen["body"]["KeyId"].endswith("abcd")
    context = seen["body"]["EncryptionContext"]
    assert context["tenant_id"] == TENANT and context["purpose"] == "crm"
    await client.aclose()


async def test_the_vault_adapter_encodes_the_context_and_hits_transit():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content or b"{}")
        return httpx.Response(
            200,
            json={"data": {"ciphertext": "vault:v1:abcd", "plaintext": base64.b64encode(b"0" * 32).decode()}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = VaultTransitKmsAdapter(
        vault_addr="https://vault.example.com",
        vault_token="hvs.example",
        key_name="voxdesk-dek",
        http_client=client,
    )
    wrapped = await adapter.wrap_data_key(
        b"0" * 32, encryption_context={"tenant_id": TENANT, "purpose": "crm"}
    )
    assert wrapped.provider == "vault_transit"
    assert "/v1/transit/encrypt/voxdesk-dek" in seen["url"]
    decoded = json.loads(base64.b64decode(seen["body"]["context"]))
    assert decoded == {"purpose": "crm", "tenant_id": TENANT}
    await client.aclose()


async def test_a_non_https_kms_endpoint_is_refused():
    """A CMK that talks plaintext to the KMS is not a key management story."""
    adapter = AwsKmsAdapter(
        key_id="kid",
        region_name="eu-central-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="s",
        endpoint_url="http://kms.internal.example.com/",
    )
    with pytest.raises(KmsOperationError):
        await adapter.wrap_data_key(b"0" * 32, encryption_context={"tenant_id": TENANT})


@pytest.mark.parametrize("value", ["", "   "])
async def test_an_empty_key_id_is_not_configuration(monkeypatch, value):
    monkeypatch.setenv("AWS_KMS_KEY_ID", value)
    # A blank key id resolves to the fail-closed adapter, not to an AWS adapter
    # that would fail later with a confusing message.
    assert isinstance(get_kms_adapter(), UnconfiguredKmsAdapter)
    adapter = AwsKmsAdapter(
        key_id=value,
        region_name="eu-central-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="s",
    )
    assert adapter.is_configured() is False
    with pytest.raises(KmsNotConfiguredError):
        await adapter.wrap_data_key(b"0" * 32, encryption_context={"tenant_id": TENANT})


async def test_empty_environment_values_resolve_to_fail_closed(monkeypatch):
    """A half-set environment must not select a half-configured adapter."""
    for name in ("AWS_KMS_KEY_ID", "VAULT_ADDR", "VAULT_TOKEN", "VAULT_TRANSIT_KEY"):
        monkeypatch.setenv(name, "")
    assert os.environ.get("AWS_KMS_KEY_ID") == ""
    assert isinstance(get_kms_adapter(), UnconfiguredKmsAdapter)
