"""Envelope encryption adapters against local fakes.

Both external KMS adapters (AWS KMS over SigV4-signed JSON, and HashiCorp Vault
Transit) accept an injected ``http_client``, so the whole envelope path can be
exercised without an account, a network or a cost. The fakes are deliberately
*faithful*: they enforce the encryption context the way the real services do and
return the real services' error codes, because a fake that accepts everything
would prove nothing about the context-mismatch and tamper cases — the two that
matter most.

What is proven here:

* generate -> unwrap round-trips to the same 32-byte key;
* a data key wrapped under one encryption context cannot be unwrapped under
  another (this is what stops a ciphertext from one tenant being decrypted in
  another tenant's context);
* key rotation: a key wrapped under an older key id still unwraps, and the
  adapter records the id it was actually wrapped under;
* a tampered ciphertext is refused, never decrypted to garbage;
* ``UnconfiguredKmsAdapter`` fails closed: it raises instead of returning a
  locally-derived key, so a missing KMS cannot silently downgrade to software
  encryption.
"""
from __future__ import annotations

import base64
import json
import os

import httpx
import pytest

from app.security.kms import (
    KmsNotConfiguredError,
    KmsOperationError,
    UnconfiguredKmsAdapter,
    WrappedDataKey,
    get_kms_adapter,
)
from app.security.kms.aws_kms import AwsKmsAdapter, VaultTransitKmsAdapter

CONTEXT = {"tenant_id": "tenant-acme", "purpose": "call_recording"}
OTHER_CONTEXT = {"tenant_id": "tenant-other", "purpose": "call_recording"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAwsKms:
    """A local stand-in for the KMS JSON 1.1 API.

    Keys are wrapped by storing ``json({key: b64, context: {...}})`` — a stand-in
    for the real service's AEAD. The important part is that the *context is part
    of the sealed blob*, exactly as it is in AWS KMS: presenting a different
    context makes decryption fail rather than silently succeeding.
    """

    def __init__(self, *, key_id: str = "arn:aws:kms:us-east-1:1:key/primary") -> None:
        self.key_id = key_id
        self.rotated_key_id = key_id + "-rotated"
        self.active_key_id = key_id
        self.calls: list[str] = []

    def _seal(self, plaintext: bytes, context: dict) -> bytes:
        return base64.b64encode(
            json.dumps(
                {"k": base64.b64encode(plaintext).decode("ascii"), "ctx": context, "key": self.active_key_id},
                sort_keys=True,
            ).encode("utf-8")
        )

    def _open(self, blob: bytes, context: dict) -> bytes:
        """Open a sealed blob, refusing anything malformed or mis-scoped.

        Both refusal modes surface as ``ValueError`` here; the HTTP handler
        turns them into the 400 the real service returns. A fake that let a
        transport exception escape would be exercising an error path no
        deployment ever sees.
        """
        try:
            sealed = json.loads(base64.b64decode(blob, validate=True).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - any malformed blob is a refusal
            raise ValueError(f"InvalidCiphertextException: {exc}") from exc
        if not isinstance(sealed, dict) or "k" not in sealed:
            raise ValueError("InvalidCiphertextException: malformed sealed payload")
        if sealed.get("ctx") != context:
            # Real KMS: InvalidCiphertextException, HTTP 400.
            raise ValueError("InvalidCiphertextException: encryption context mismatch")
        return base64.b64decode(sealed["k"])

    @property
    def client(self) -> httpx.AsyncClient:
        async def handler(request: httpx.Request) -> httpx.Response:
            action = (request.headers.get("X-Amz-Target") or "").split(".")[-1]
            payload = json.loads(request.content or b"{}")
            self.calls.append(action)
            context = payload.get("EncryptionContext") or {}
            try:
                if action == "GenerateDataKey":
                    plaintext = os.urandom(32)
                    return httpx.Response(
                        200,
                        json={
                            "KeyId": self.active_key_id,
                            "Plaintext": base64.b64encode(plaintext).decode("ascii"),
                            "CiphertextBlob": base64.b64encode(
                                self._seal(plaintext, context)
                            ).decode("ascii"),
                        },
                    )
                if action == "Encrypt":
                    plaintext = base64.b64decode(payload["Plaintext"])
                    return httpx.Response(
                        200,
                        json={
                            "KeyId": self.active_key_id,
                            "CiphertextBlob": base64.b64encode(
                                self._seal(plaintext, context)
                            ).decode("ascii"),
                        },
                    )
                if action == "Decrypt":
                    plaintext = self._open(
                        base64.b64decode(payload["CiphertextBlob"]), context
                    )
                    return httpx.Response(
                        200,
                        json={
                            "KeyId": self.active_key_id,
                            "Plaintext": base64.b64encode(plaintext).decode("ascii"),
                        },
                    )
            except ValueError:
                return httpx.Response(
                    400,
                    json={
                        "__type": "InvalidCiphertextException",
                        "message": "encryption context mismatch",
                    },
                )
            return httpx.Response(400, json={"__type": "UnknownOperationException"})

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://kms.us-east-1.amazonaws.com"
        )


class FakeVaultTransit:
    """A local stand-in for the Vault Transit engine."""

    def __init__(self, *, key_name: str = "voxdesk-dek") -> None:
        self.key_name = key_name
        self.calls: list[str] = []

    def _seal(self, plaintext: bytes, context: str | None) -> str:
        return base64.b64encode(
            json.dumps(
                {"k": base64.b64encode(plaintext).decode("ascii"), "ctx": context},
                sort_keys=True,
            ).encode("utf-8")
        ).decode("ascii")

    def _open(self, ciphertext: str, context: str | None) -> bytes:
        sealed = json.loads(base64.b64decode(ciphertext).decode("utf-8"))
        if sealed.get("ctx") != context:
            raise ValueError("context mismatch")
        return base64.b64decode(sealed["k"])

    @property
    def client(self) -> httpx.AsyncClient:
        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            payload = json.loads(request.content or b"{}")
            context = payload.get("context")
            self.calls.append(path)
            try:
                if "datakey/plaintext" in path:
                    plaintext = os.urandom(32)
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "plaintext": base64.b64encode(plaintext).decode("ascii"),
                                "ciphertext": self._seal(plaintext, context),
                            }
                        },
                    )
                if "/encrypt/" in path:
                    plaintext = base64.b64decode(payload["plaintext"])
                    return httpx.Response(
                        200, json={"data": {"ciphertext": self._seal(plaintext, context)}}
                    )
                if "/decrypt/" in path:
                    plaintext = self._open(payload["ciphertext"], context)
                    return httpx.Response(
                        200,
                        json={"data": {"plaintext": base64.b64encode(plaintext).decode("ascii")}},
                    )
            except ValueError:
                return httpx.Response(400, json={"errors": ["encryption context mismatch"]})
            return httpx.Response(404, json={"errors": ["unsupported path"]})

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://vault.example.com"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_kms() -> AwsKmsAdapter:
    fake = FakeAwsKms()
    adapter = AwsKmsAdapter(
        key_id=fake.key_id,
        region_name="us-east-1",
        access_key_id="AKIAFAKEFAKEFAKE",
        secret_access_key="fake-secret-access-key",
        http_client=fake.client,
    )
    adapter._fake = fake  # type: ignore[attr-defined]
    return adapter


@pytest.fixture
def vault() -> VaultTransitKmsAdapter:
    fake = FakeVaultTransit()
    adapter = VaultTransitKmsAdapter(
        vault_addr="https://vault.example.com",
        vault_token="fake-token",
        key_name=fake.key_name,
        http_client=fake.client,
    )
    adapter._fake = fake  # type: ignore[attr-defined]
    return adapter


# ---------------------------------------------------------------------------
# AWS KMS
# ---------------------------------------------------------------------------


async def test_aws_kms_round_trips_a_data_key(aws_kms: AwsKmsAdapter) -> None:
    assert aws_kms.is_configured() is True
    dek, wrapped = await aws_kms.generate_data_key(encryption_context=CONTEXT)
    assert len(dek) == 32
    assert wrapped.provider == "aws_kms"
    assert wrapped.encryption_context == CONTEXT

    unwrapped = await aws_kms.unwrap_data_key(wrapped)
    assert unwrapped == dek, "the round trip must return the exact data key"


async def test_aws_kms_wrap_then_unwrap(aws_kms: AwsKmsAdapter) -> None:
    dek = os.urandom(32)
    wrapped = await aws_kms.wrap_data_key(dek, encryption_context=CONTEXT)
    assert wrapped.ciphertext_blob != dek
    assert await aws_kms.unwrap_data_key(wrapped) == dek


async def test_aws_kms_rejects_an_encryption_context_mismatch(aws_kms: AwsKmsAdapter) -> None:
    """A key wrapped in one tenant's context must not open in another's."""
    dek, wrapped = await aws_kms.generate_data_key(encryption_context=CONTEXT)
    with pytest.raises(KmsOperationError) as caught:
        await aws_kms.unwrap_data_key(wrapped, encryption_context=OTHER_CONTEXT)
    assert "Decrypt" in str(caught.value) or "failed" in str(caught.value).lower()


async def test_aws_kms_detects_a_tampered_ciphertext(aws_kms: AwsKmsAdapter) -> None:
    """A flipped bit must be refused, never decrypted to a wrong key."""
    dek, wrapped = await aws_kms.generate_data_key(encryption_context=CONTEXT)
    tampered_bytes = bytearray(wrapped.ciphertext_blob)
    tampered_bytes[8] ^= 0xFF
    tampered = WrappedDataKey(
        key_id=wrapped.key_id,
        ciphertext_blob=bytes(tampered_bytes),
        provider=wrapped.provider,
        encryption_context=wrapped.encryption_context,
    )
    with pytest.raises(KmsOperationError):
        await aws_kms.unwrap_data_key(tampered)
    # The tampered blob must never yield a key, not even a different one.
    assert await aws_kms.unwrap_data_key(wrapped) == dek


async def test_aws_kms_survives_key_rotation(aws_kms: AwsKmsAdapter) -> None:
    """A key wrapped under the old key id still unwraps after rotation."""
    dek, wrapped = await aws_kms.generate_data_key(encryption_context=CONTEXT)
    old_key_id = wrapped.key_id
    aws_kms._fake.active_key_id = aws_kms._fake.rotated_key_id  # type: ignore[attr-defined]

    assert await aws_kms.unwrap_data_key(wrapped) == dek, (
        "rotating the KMS key must not orphan data keys already wrapped under it"
    )

    # And a freshly wrapped key now reports the rotated id.
    _new_dek, new_wrapped = await aws_kms.generate_data_key(encryption_context=CONTEXT)
    assert new_wrapped.key_id != old_key_id
    assert new_wrapped.key_id == aws_kms._fake.rotated_key_id  # type: ignore[attr-defined]


async def test_aws_kms_refuses_a_non_256_bit_key(aws_kms: AwsKmsAdapter) -> None:
    with pytest.raises(KmsOperationError):
        await aws_kms.wrap_data_key(b"too-short", encryption_context=CONTEXT)


async def test_aws_kms_is_unconfigured_without_credentials() -> None:
    unconfigured = AwsKmsAdapter(key_id="", region_name="us-east-1")
    assert unconfigured.is_configured() is False
    with pytest.raises(KmsNotConfiguredError):
        await unconfigured.generate_data_key(encryption_context=CONTEXT)


async def test_aws_kms_rejects_a_malformed_region() -> None:
    adapter = AwsKmsAdapter(
        key_id="key-1",
        region_name="not-a-region",
        access_key_id="AKIAFAKE",
        secret_access_key="secret",
    )
    assert adapter.is_configured() is False


# ---------------------------------------------------------------------------
# Vault Transit
# ---------------------------------------------------------------------------


async def test_vault_transit_round_trips_a_data_key(vault: VaultTransitKmsAdapter) -> None:
    assert vault.is_configured() is True
    dek, wrapped = await vault.generate_data_key(encryption_context=CONTEXT)
    assert len(dek) == 32
    assert wrapped.provider == "vault_transit"
    assert await vault.unwrap_data_key(wrapped) == dek


async def test_vault_transit_rejects_an_encryption_context_mismatch(
    vault: VaultTransitKmsAdapter,
) -> None:
    dek, wrapped = await vault.generate_data_key(encryption_context=CONTEXT)
    with pytest.raises(KmsOperationError):
        await vault.unwrap_data_key(wrapped, encryption_context=OTHER_CONTEXT)
    assert await vault.unwrap_data_key(wrapped) == dek


async def test_vault_transit_detects_a_tampered_ciphertext(
    vault: VaultTransitKmsAdapter,
) -> None:
    dek, wrapped = await vault.generate_data_key(encryption_context=CONTEXT)
    tampered_bytes = bytearray(wrapped.ciphertext_blob)
    tampered_bytes[4] ^= 0x01
    tampered = WrappedDataKey(
        key_id=wrapped.key_id,
        ciphertext_blob=bytes(tampered_bytes),
        provider=wrapped.provider,
        encryption_context=wrapped.encryption_context,
    )
    with pytest.raises(KmsOperationError):
        await vault.unwrap_data_key(tampered)


async def test_vault_transit_is_unconfigured_without_a_token() -> None:
    unconfigured = VaultTransitKmsAdapter(vault_addr="https://vault", vault_token="", key_name="k")
    assert unconfigured.is_configured() is False
    with pytest.raises(KmsNotConfiguredError):
        await unconfigured.wrap_data_key(os.urandom(32), encryption_context=CONTEXT)


# ---------------------------------------------------------------------------
# Fail-closed default
# ---------------------------------------------------------------------------


async def test_the_unconfigured_adapter_fails_closed() -> None:
    """Missing KMS must not fall back to a locally-derived key.

    A silent fallback would make every deployment look encrypted while the only
    key material sat next to the ciphertext.
    """
    adapter = UnconfiguredKmsAdapter()
    assert adapter.is_configured() is False
    for call in (
        lambda: adapter.generate_data_key(encryption_context=CONTEXT),
        lambda: adapter.wrap_data_key(os.urandom(32), encryption_context=CONTEXT),
        lambda: adapter.unwrap_data_key(
            WrappedDataKey(
                key_id="k",
                ciphertext_blob=b"blob",
                provider="unconfigured",
                encryption_context=CONTEXT,
            )
        ),
    ):
        with pytest.raises(KmsNotConfiguredError):
            await call()


def test_get_kms_adapter_returns_the_unconfigured_default_without_environment(
    monkeypatch,
) -> None:
    for name in (
        "AWS_KMS_KEY_ID",
        "VAULT_ADDR",
        "VAULT_TOKEN",
        "VAULT_TRANSIT_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    adapter = get_kms_adapter()
    assert isinstance(adapter, UnconfiguredKmsAdapter), (
        "without KMS environment variables the resolver must hand back the "
        "fail-closed adapter, never a silent local one"
    )


def test_get_kms_adapter_prefers_aws_kms_when_its_key_is_set(monkeypatch) -> None:
    monkeypatch.setenv("AWS_KMS_KEY_ID", "arn:aws:kms:us-east-1:1:key/abc")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    assert isinstance(get_kms_adapter(), AwsKmsAdapter)


def test_get_kms_adapter_uses_vault_when_only_vault_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("AWS_KMS_KEY_ID", raising=False)
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "token")
    monkeypatch.setenv("VAULT_TRANSIT_KEY", "voxdesk-dek")
    assert isinstance(get_kms_adapter(), VaultTransitKmsAdapter)
