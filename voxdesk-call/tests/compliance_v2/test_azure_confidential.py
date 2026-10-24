"""The Azure Key Vault adapter's containment properties.

"Confidential" here is about what the adapter will accept and what it will send,
not a vendor badge: the vault must be reached over TLS, the wrap algorithm is
pinned to ``RSA-OAEP-256`` (which is what a Premium vault / Managed HSM key
supports for hardware-backed wrapping), the stored blob keeps the key id *and*
version it was wrapped under, and the encryption context travels with every call
so a blob moved to another tenant's row cannot be unwrapped.
"""

from __future__ import annotations

import base64

import pytest

from app.compliance_v2.kms.azure_keyvault import (
    ALGORITHM,
    AzureKeyVaultKms,
    parse_wrapped_token,
)
from app.compliance_v2.kms.base import KmsPermanentError

CONTEXT = {"tenant_id": "t-9", "column": "recording_url", "purpose": "compliance_v2"}
VAULT = "https://voxdesk-prod-hsm.vault.azure.net"


class FakeCryptoClient:
    """Stands in for ``azure.keyvault.keys.crypto.aio.CryptographyClient``."""

    def __init__(self, *, versions: list[str] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._versions = versions or []

    async def wrap_key(self, **kwargs):
        self.calls.append(("wrap_key", kwargs))
        return _WrapResult(encrypted_key=b"wrapped:" + kwargs["key"][:4])

    async def unwrap_key(self, **kwargs):
        self.calls.append(("unwrap_key", kwargs))
        return _UnwrapResult(key=b"z" * 32)

    async def get_key(self, **kwargs):
        self.calls.append(("get_key", kwargs))
        return {"key": "metadata"}


class _WrapResult:
    def __init__(self, *, encrypted_key: bytes):
        self.encrypted_key = encrypted_key
        self.key_id = f"{VAULT}/keys/compliance/7f3c"
        self.algorithm = ALGORITHM


class _UnwrapResult:
    def __init__(self, *, key: bytes):
        self.key = key
        self.key_id = f"{VAULT}/keys/compliance/7f3c"
        self.algorithm = ALGORITHM


def _provider(client: FakeCryptoClient, *, version: str = "") -> AzureKeyVaultKms:
    return AzureKeyVaultKms(
        vault_url=VAULT, key_name="compliance", key_version=version, client=client
    )


@pytest.mark.parametrize(
    "vault_url",
    ["http://voxdesk.vault.azure.net", "voxdesk.vault.azure.net", "ftp://x/y"],
)
def test_a_plaintext_vault_url_is_refused(vault_url):
    with pytest.raises(KmsPermanentError) as raised:
        AzureKeyVaultKms(vault_url=vault_url, key_name="compliance")

    assert "https" in str(raised.value)


def test_a_managed_hsm_vault_url_is_accepted():
    provider = AzureKeyVaultKms(vault_url=VAULT + "/", key_name="compliance")
    assert provider.key_id == f"{VAULT}/keys/compliance"


def test_the_key_id_carries_the_version_when_one_is_pinned():
    provider = _provider(FakeCryptoClient(), version="7f3c")
    assert provider.key_id.endswith("/keys/compliance/7f3c")


async def test_a_wrap_names_the_pinned_algorithm_and_the_context():
    client = FakeCryptoClient()
    provider = _provider(client)

    key = await provider.generate_data_key(context=CONTEXT)
    try:
        operation, kwargs = client.calls[-1]
        assert operation == "wrap_key"
        assert kwargs["algorithm"] == ALGORITHM == "RSA-OAEP-256"
        assert kwargs["encryption_context"] == CONTEXT
        assert len(key.plaintext) == 32
        assert key.provider == "azure"
    finally:
        key.destroy()


async def test_an_unwrap_of_the_wrong_length_is_refused():
    class ShortClient(FakeCryptoClient):
        async def unwrap_key(self, **kwargs):
            self.calls.append(("unwrap_key", kwargs))
            return _UnwrapResult(key=b"short")

    with pytest.raises(KmsPermanentError):
        await _provider(ShortClient()).unwrap_data_key(b"blob", context=CONTEXT)


async def test_an_empty_wrap_is_refused():
    class EmptyClient(FakeCryptoClient):
        async def wrap_key(self, **kwargs):
            self.calls.append(("wrap_key", kwargs))
            return _WrapResult(encrypted_key=b"")

    with pytest.raises(KmsPermanentError):
        await _provider(EmptyClient()).generate_data_key(context=CONTEXT)


async def test_a_rewrap_returns_a_different_blob_under_the_current_version():
    client = FakeCryptoClient()
    provider = _provider(client)

    rewrapped = await provider.rewrap_data_key(b"old-blob", context=CONTEXT)

    assert rewrapped != b"old-blob"
    assert [name for name, _ in client.calls] == ["unwrap_key", "wrap_key"]


async def test_a_healthy_vault_reports_reachable():
    health = await _provider(FakeCryptoClient()).health()

    assert health.provider == "azure"
    assert health.reachable is True
    assert health.detail["key_name"] == "compliance"


async def test_an_unreachable_vault_reports_unreachable_not_a_pass():
    class DownClient(FakeCryptoClient):
        async def get_key(self, **kwargs):
            raise RuntimeError("ServiceRequestError: connection refused")

    health = await _provider(DownClient()).health()

    assert health.reachable is False
    assert health.detail["error"]


def test_the_stored_token_round_trips_with_its_vault_url():
    wrapped = b"wrapped-bytes"
    token = f"{VAULT}/keys/compliance/7f3c:{base64.b64encode(wrapped).decode()}"

    key_id, recovered = parse_wrapped_token(token)

    assert key_id == f"{VAULT}/keys/compliance/7f3c"
    assert recovered == wrapped


def test_a_token_without_a_key_id_is_refused():
    with pytest.raises(KmsPermanentError):
        parse_wrapped_token("bm90LWEtdG9rZW4=")
