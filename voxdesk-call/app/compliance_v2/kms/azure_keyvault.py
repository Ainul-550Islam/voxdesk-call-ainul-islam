"""Azure Key Vault adapter (wrapKey / unwrapKey / rotate).

Azure is the one provider whose "key" is a *key version* rather than a key id:
rotation produces a new version of the same key, and the wrapped blob records
which version wrapped it. The adapter therefore:

* performs wrap and unwrap (the local data key is generated here, since Key
  Vault has no ``GenerateDataKey`` equivalent);
* sets ``version`` on wrap when configured, and lets unwrap find the version
  from the wrapped blob itself, so a rekey can read old blobs after a rotation;
* passes the encryption context as ``encryption_context`` — Key Vault embeds it
  into the wrapped blob and refuses to unwrap with a different one, which is
  the cross-tenant control;
* uses a credential obtained from the ambient environment (managed identity,
  workload identity, or ``AZURE_*`` variables). No secret is ever read from this
  module's configuration.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
from typing import Any

from app.compliance_v2.kms.algorithms import require_approved
from app.compliance_v2.kms.base import (
    DataKey,
    KeyHealth,
    KmsPermanentError,
    KmsProvider,
    KmsUnavailable,
)

ALGORITHM = "RSA-OAEP-256"


class AzureKeyVaultKms(KmsProvider):
    name = "azure"

    def __init__(
        self,
        *,
        vault_url: str,
        key_name: str,
        key_version: str = "",
        client: Any | None = None,
        max_attempts: int = 4,
        credential: Any | None = None,
    ) -> None:
        if not vault_url or not key_name:
            raise KmsPermanentError("Azure Key Vault requires a vault URL and a key name")
        if not vault_url.strip().lower().startswith("https://"):
            # Plain HTTP would put the wrapped key material on the wire in the
            # clear; there is no local-dev exception, because the local-dev
            # provider exists for that case.
            raise KmsPermanentError("Azure Key Vault requires an https:// vault URL")
        require_approved(ALGORITHM, primitive="asymmetric_wrap")
        self._vault_url = vault_url.rstrip("/")
        self._key_name = key_name
        self._key_version = key_version
        self._client = client
        self._credential = credential
        self._max_attempts = max(1, int(max_attempts))

    @property
    def key_id(self) -> str:
        suffix = f"/{self._key_version}" if self._key_version else ""
        return f"{self._vault_url}/keys/{self._key_name}{suffix}"

    def _client_or_build(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from azure.identity.aio import DefaultAzureCredential
            from azure.keyvault.keys.crypto.aio import CryptographyClient
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise KmsPermanentError("azure-keyvault-keys and azure-identity are not installed") from exc
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        self._client = CryptographyClient(self.key_id, self._credential)
        return self._client

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        client = self._client_or_build()
        delay = 0.2
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await getattr(client, operation)(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below
                name = type(exc).__name__
                transient = name in {
                    "ServiceRequestError",
                    "ServiceResponseError",
                    "HttpResponseError",
                } and ("429" in str(exc) or "503" in str(exc) or "500" in str(exc))
                if not transient:
                    raise KmsPermanentError(f"Azure Key Vault rejected the request ({name})") from exc
                last = KmsUnavailable(f"Azure Key Vault is temporarily unavailable ({name})")
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2.0)
        raise KmsUnavailable(str(last) if last else "Azure Key Vault call failed")

    async def generate_data_key(self, *, context: dict[str, str]) -> DataKey:
        plaintext = secrets.token_bytes(32)
        result = await self._call(
            "wrap_key",
            algorithm=ALGORITHM,
            key=plaintext,
            encryption_context=dict(context),
        )
        wrapped = bytes(result.encrypted_key)
        if not wrapped:
            raise KmsPermanentError("Azure Key Vault returned an empty wrapped key")
        return DataKey(
            plaintext=plaintext,
            wrapped=wrapped,
            provider=self.name,
            key_id=str(getattr(result, "key_id", self.key_id)),
        )

    async def unwrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        result = await self._call(
            "unwrap_key",
            algorithm=ALGORITHM,
            encrypted_key=bytes(wrapped),
            encryption_context=dict(context),
        )
        plaintext = bytes(result.key)
        if len(plaintext) != 32:
            raise KmsPermanentError("Azure Key Vault returned a data key of unexpected length")
        return plaintext

    async def rewrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        plaintext = await self.unwrap_data_key(wrapped, context=context)
        result = await self._call(
            "wrap_key",
            algorithm=ALGORITHM,
            key=plaintext,
            encryption_context=dict(context),
        )
        return bytes(result.encrypted_key)

    async def health(self) -> KeyHealth:
        try:
            await self._call("get_key", key_version=self._key_version or None)
        except TypeError:
            try:
                await self._call("get_key")
            except (KmsPermanentError, KmsUnavailable) as exc:
                return KeyHealth(
                    provider=self.name,
                    key_id=self.key_id,
                    reachable=False,
                    detail={"error": str(exc)[:200]},
                )
        except (KmsPermanentError, KmsUnavailable) as exc:
            return KeyHealth(
                provider=self.name,
                key_id=self.key_id,
                reachable=False,
                detail={"error": str(exc)[:200]},
            )
        return KeyHealth(
            provider=self.name,
            key_id=self.key_id,
            reachable=True,
            detail={"vault": self._vault_url, "key_name": self._key_name},
        )

    @staticmethod
    def encode(wrapped: bytes) -> str:
        return base64.b64encode(wrapped).decode("ascii")


def parse_wrapped_token(token: str) -> tuple[str, bytes]:
    """Split a stored ``<key_id>:<base64>`` wrap token back into its parts."""
    if ":" not in token:
        raise KmsPermanentError("wrapped token is not in key-id:base64 form")
    key_id, encoded = token.rsplit(":", 1)
    try:
        return key_id, base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise KmsPermanentError("wrapped token is not valid base64") from exc


__all__ = ["ALGORITHM", "AzureKeyVaultKms", "parse_wrapped_token"]
