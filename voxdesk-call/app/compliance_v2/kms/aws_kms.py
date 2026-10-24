"""AWS KMS adapter (``GenerateDataKey`` / ``Decrypt`` / ``ReEncrypt``).

Notes that matter for correctness:

* the encryption context is passed on **every** call, including ``Decrypt`` and
  ``ReEncrypt``. AWS refuses a decrypt whose context does not match the one used
  at wrap time, which is where cross-tenant protection actually lives;
* ``boto3`` is imported lazily: the API process must boot on a deployment that
  uses a different provider;
* client errors are classified by ``Error.Code`` — throttling and 5xx retry,
  everything else (``AccessDeniedException``, ``DisabledException``,
  ``InvalidCiphertextException``) is permanent, because retrying a permission
  error just multiplies noise in the logs;
* the data key is requested through ``KeySpec=AES_256`` rather than
  ``NumberOfBytes``, so KMS returns a clean 32-byte key with no padding.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.compliance_v2.kms.base import (
    DataKey,
    KeyHealth,
    KmsPermanentError,
    KmsProvider,
    KmsUnavailable,
)

#: AWS error codes that are worth retrying.
_RETRYABLE_CODES = frozenset(
    {
        "ThrottlingException",
        "KMSInternalException",
        "DependencyTimeoutException",
        "LimitExceededException",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
    }
)


class AwsKms(KmsProvider):
    name = "aws"

    def __init__(
        self,
        *,
        key_id: str,
        region: str = "",
        client: Any | None = None,
        max_attempts: int = 4,
        endpoint_url: str = "",
    ) -> None:
        if not key_id:
            raise KmsPermanentError("AWS KMS requires a key id (alias or ARN)")
        self._key_id = key_id
        self._region = region
        self._client = client
        self._max_attempts = max(1, int(max_attempts))
        self._endpoint_url = endpoint_url

    @property
    def key_id(self) -> str:
        return self._key_id

    def _client_or_build(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - dependency is in requirements
            raise KmsPermanentError("boto3 is not installed") from exc
        self._client = boto3.client(
            "kms",
            region_name=self._region or None,
            endpoint_url=self._endpoint_url or None,
        )
        return self._client

    def _classify(self, exc: Exception) -> Exception:
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str((response.get("Error") or {}).get("Code") or "")
        if code in _RETRYABLE_CODES:
            return KmsUnavailable(f"AWS KMS is temporarily unavailable ({code})")
        return KmsPermanentError(f"AWS KMS rejected the request ({code or type(exc).__name__})")

    async def _call(self, operation: str, **kwargs: Any) -> dict:
        client = self._client_or_build()
        delay = 0.2
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await asyncio.to_thread(getattr(client, operation), **kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below
                classified = self._classify(exc)
                if isinstance(classified, KmsPermanentError):
                    raise classified from exc
                last = classified
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2.0)
        raise KmsUnavailable(str(last) if last else "AWS KMS call failed")

    async def generate_data_key(self, *, context: dict[str, str]) -> DataKey:
        response = await self._call(
            "generate_data_key",
            KeyId=self._key_id,
            KeySpec="AES_256",
            EncryptionContext=dict(context),
        )
        plaintext = bytes(response["Plaintext"])
        wrapped = bytes(response["CiphertextBlob"])
        if len(plaintext) != 32:
            raise KmsPermanentError("AWS KMS returned a data key of unexpected length")
        return DataKey(
            plaintext=plaintext,
            wrapped=wrapped,
            provider=self.name,
            key_id=str(response.get("KeyId") or self._key_id),
        )

    async def unwrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        response = await self._call(
            "decrypt", CiphertextBlob=bytes(wrapped), EncryptionContext=dict(context)
        )
        plaintext = bytes(response["Plaintext"])
        if len(plaintext) != 32:
            raise KmsPermanentError("AWS KMS returned a data key of unexpected length")
        return plaintext

    async def rewrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        response = await self._call(
            "re_encrypt",
            CiphertextBlob=bytes(wrapped),
            DestinationKeyId=self._key_id,
            SourceEncryptionContext=dict(context),
            DestinationEncryptionContext=dict(context),
        )
        return bytes(response["CiphertextBlob"])

    async def health(self) -> KeyHealth:
        try:
            response = await self._call("describe_key", KeyId=self._key_id)
        except (KmsPermanentError, KmsUnavailable) as exc:
            return KeyHealth(
                provider=self.name,
                key_id=self._key_id,
                reachable=False,
                detail={"error": str(exc)[:200]},
            )
        metadata = response.get("KeyMetadata") or {}
        state = str(metadata.get("KeyState") or "")
        return KeyHealth(
            provider=self.name,
            key_id=self._key_id,
            reachable=state == "Enabled",
            detail={"key_state": state, "key_manager": str(metadata.get("KeyManager") or "")},
        )

    async def list_key_versions(self) -> list[str]:
        try:
            response = await self._call("list_key_rotations", KeyId=self._key_id)
        except (KmsPermanentError, KmsUnavailable):
            return [self._key_id]
        versions = [
            str(item.get("KeyId") or "")
            for item in response.get("Rotations") or []
            if item.get("KeyId")
        ]
        return versions or [self._key_id]


__all__ = ["AwsKms"]
