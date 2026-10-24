"""Google Cloud KMS adapter.

GCP exposes envelope encryption through a *key encryption key*: the data key is
generated locally and the KEK encrypts it. That is a different API shape from
AWS (which can mint the data key for you), so the adapter does the wrapping
explicitly with the ``google-cloud-kms`` client.

Additional-data binding: GCP calls it ``additional_authenticated_data`` and it
plays the same role as the AWS encryption context — the context is passed on
encrypt *and* decrypt, and a mismatch fails inside GCP. It is therefore the
cross-tenant control here too, not a comment in our code.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
from typing import Any

from app.compliance_v2.kms.base import (
    DataKey,
    KeyHealth,
    KmsPermanentError,
    KmsProvider,
    KmsUnavailable,
)

_AAD_VERSION = b"v1|"


def _aad(context: dict[str, str]) -> bytes:
    body = "|".join(f"{key}={value}" for key, value in sorted(context.items()))
    return _AAD_VERSION + body.encode("utf-8")


class GcpKms(KmsProvider):
    name = "gcp"

    def __init__(
        self,
        *,
        key_name: str,
        client: Any | None = None,
        max_attempts: int = 4,
    ) -> None:
        if not key_name:
            raise KmsPermanentError("GCP KMS requires a full key resource name")
        self._key_name = key_name
        self._client = client
        self._max_attempts = max(1, int(max_attempts))

    @property
    def key_id(self) -> str:
        return self._key_name

    def _client_or_build(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.cloud import kms
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise KmsPermanentError("google-cloud-kms is not installed") from exc
        self._client = kms.KeyManagementServiceClient()
        return self._client

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        client = self._client_or_build()
        delay = 0.2
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await asyncio.to_thread(getattr(client, operation), **kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below
                transient = type(exc).__name__ in {
                    "ServiceUnavailable",
                    "DeadlineExceeded",
                    "InternalServerError",
                    "TooManyRequests",
                } or "503" in str(exc)
                if not transient:
                    raise KmsPermanentError(
                        f"GCP KMS rejected the request ({type(exc).__name__})"
                    ) from exc
                last = KmsUnavailable(f"GCP KMS is temporarily unavailable ({type(exc).__name__})")
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 2.0)
        raise KmsUnavailable(str(last) if last else "GCP KMS call failed")

    async def generate_data_key(self, *, context: dict[str, str]) -> DataKey:
        plaintext = secrets.token_bytes(32)
        response = await self._call(
            "encrypt",
            request={
                "name": self._key_name,
                "plaintext": plaintext,
                "additional_authenticated_data": _aad(context),
            },
        )
        wrapped = bytes(response.ciphertext)
        if not wrapped:
            raise KmsPermanentError("GCP KMS returned an empty wrapped key")
        return DataKey(
            plaintext=plaintext, wrapped=wrapped, provider=self.name, key_id=self._key_name
        )

    async def unwrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        response = await self._call(
            "decrypt",
            request={
                "name": self._key_name,
                "ciphertext": bytes(wrapped),
                "additional_authenticated_data": _aad(context),
            },
        )
        plaintext = bytes(response.plaintext)
        if len(plaintext) != 32:
            raise KmsPermanentError("GCP KMS returned a data key of unexpected length")
        return plaintext

    async def rewrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        plaintext = await self.unwrap_data_key(wrapped, context=context)
        response = await self._call(
            "encrypt",
            request={
                "name": self._key_name,
                "plaintext": plaintext,
                "additional_authenticated_data": _aad(context),
            },
        )
        return bytes(response.ciphertext)

    async def health(self) -> KeyHealth:
        try:
            response = await self._call("get_key_version", request={"name": f"{self._key_name}/cryptoKeyVersions/1"})
        except (KmsPermanentError, KmsUnavailable) as exc:
            return KeyHealth(
                provider=self.name,
                key_id=self._key_name,
                reachable=False,
                detail={"error": str(exc)[:200]},
            )
        state = getattr(response, "state", None)
        return KeyHealth(
            provider=self.name,
            key_id=self._key_name,
            reachable=int(state or 0) == 1,
            detail={"state": str(state), "algorithm": str(getattr(response, "algorithm", ""))},
        )

    @staticmethod
    def encode_ciphertext(wrapped: bytes) -> str:
        """Base64 form for transport/debugging (never stored in this shape)."""
        return base64.b64encode(wrapped).decode("ascii")


__all__ = ["GcpKms"]
