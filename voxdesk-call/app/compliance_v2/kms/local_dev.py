"""Development-only KMS: a master key on the local filesystem.

This exists so that developers and the test suite exercise the *same* envelope
path as production — two-step key wrap, encryption context, key ids — without a
cloud account. It is refused outright when ``APP_ENV=production``: the whole
point of envelope encryption is that the wrapping key does not sit next to the
ciphertext, and a file next to the database is exactly that.

The wrap is AES-256-GCM with the encryption context as associated data, which
gives the same cross-tenant property as a cloud KMS: an envelope wrapped for
``{"tenant": "a", "column": "x"}`` will not open when presented with tenant
``b``'s context.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.compliance_v2.kms.base import (
    DataKey,
    KeyHealth,
    KmsNotConfigured,
    KmsPermanentError,
    KmsProvider,
)

#: Version byte, so a future format change is detectable rather than ambiguous.
_FORMAT_VERSION = b"\x01"


class LocalDevKms(KmsProvider):
    """File-based master key. Development and test only."""

    name = "local_dev"

    def __init__(self, master_key_path: str | Path, *, environment: str = "development", key_id: str = "local-dev-1") -> None:
        if (environment or "").strip().lower() in {"production", "prod"}:
            raise KmsNotConfigured(
                "the local development KMS is refused in production; "
                "configure KMS_PROVIDER=aws, gcp or azure"
            )
        self._path = Path(master_key_path)
        self._key_id = key_id
        self._master = self._load_or_create()

    def _load_or_create(self) -> bytes:
        if self._path.exists():
            raw = self._path.read_bytes()
            if len(raw) != 32:
                raise KmsPermanentError(
                    f"local master key at {self._path} is {len(raw)} bytes, expected 32"
                )
            return raw
        self._path.parent.mkdir(parents=True, exist_ok=True)
        material = secrets.token_bytes(32)
        self._path.write_bytes(material)
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        return material

    @property
    def key_id(self) -> str:
        return self._key_id

    def _aad(self, context: dict[str, str]) -> bytes:
        return _FORMAT_VERSION + b"|" + "|".join(
            f"{key}={value}" for key, value in sorted(context.items())
        ).encode("utf-8")

    async def generate_data_key(self, *, context: dict[str, str]) -> DataKey:
        plaintext = secrets.token_bytes(32)
        wrapped = await asyncio.to_thread(self._wrap_sync, plaintext, context)
        return DataKey(
            plaintext=plaintext, wrapped=wrapped, provider=self.name, key_id=self._key_id
        )

    def _wrap_sync(self, material: bytes, context: dict[str, str]) -> bytes:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._master).encrypt(nonce, material, self._aad(context))
        return _FORMAT_VERSION + nonce + ciphertext

    async def unwrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        return await asyncio.to_thread(self._unwrap_sync, wrapped, context)

    def _unwrap_sync(self, wrapped: bytes, context: dict[str, str]) -> bytes:
        if not wrapped or wrapped[:1] != _FORMAT_VERSION:
            raise KmsPermanentError("wrapped key has an unknown format")
        nonce, ciphertext = wrapped[1:13], wrapped[13:]
        try:
            return AESGCM(self._master).decrypt(nonce, ciphertext, self._aad(context))
        except InvalidTag as exc:
            raise KmsPermanentError(
                "wrapped key does not open under this context (wrong tenant or column)"
            ) from exc

    async def rewrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        material = await self.unwrap_data_key(wrapped, context=context)
        return await asyncio.to_thread(self._wrap_sync, material, context)

    async def health(self) -> KeyHealth:
        reachable = self._path.exists()
        return KeyHealth(
            provider=self.name,
            key_id=self._key_id,
            reachable=reachable,
            detail={"path_present": reachable, "development_only": True},
        )


__all__ = ["LocalDevKms"]
