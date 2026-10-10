"""Provider-neutral secret references and envelope encryption with KMS & key-rotation support.

The database stores only opaque ``secret://`` references or authenticated
``v1.<key_id>.<nonce>.<ct>`` envelopes. This module supports:
1. ``EncryptedSecretStore``: AES-256-GCM envelope encryption backed by the
   deployment ``KeyRing``, plus optional external KMS envelope encryption via
   ``KmsAdapter`` (where each secret gets a unique 256-bit DEK wrapped by KMS).
2. Key-rotation helpers (``extract_key_id``, ``needs_rotation``,
   ``rotate_reference``) used by ``scripts/rotate_secrets.py`` and admin jobs
   to re-seal provider, CRM, webhook, identity, and HTTP-tool secrets onto the
   active key without losing tenant or purpose AAD binding.
3. ``MemorySecretStore``: ephemeral test backend (never selected in production).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.auth.identity.exceptions import IdentitySecretsUnavailable
from app.auth.identity.secrets import decrypt_text, encrypt_text, key_ring
from app.security.kms import (
    KmsAdapter,
    KmsError,
    WrappedDataKey,
    get_kms_adapter,
)


class SecretStoreError(RuntimeError):
    """Secret reference is invalid or the configured key ring / KMS is unavailable."""


class SecretStore(Protocol):
    def put(self, tenant_id: str, purpose: str, value: str) -> str: ...
    def get(self, tenant_id: str, purpose: str, reference: str) -> str: ...
    def delete(self, tenant_id: str, purpose: str, reference: str) -> None: ...


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _kms_aad(tenant_id: str, purpose: str) -> bytes:
    return f"voxdesk:kms_secret:v1:{tenant_id}:{purpose}".encode("utf-8")


def extract_key_id(reference: str) -> str | None:
    """Extract the wrapping key ID from a ``secret://`` reference or ``v1.`` envelope."""
    if not isinstance(reference, str):
        return None
    if reference.startswith("secret://encrypted/"):
        parts = reference.removeprefix("secret://encrypted/").split("/", 2)
        if len(parts) == 3 and parts[0]:
            return parts[0]
        return None
    if reference.startswith("secret://kms/"):
        body = reference.removeprefix("secret://kms/")
        tail = body.rsplit("/", 3)
        if len(tail) == 4:
            head = tail[0].split("/", 1)
            if len(head) == 2 and head[1]:
                return head[1]
        return None
    if reference.startswith("v1."):
        parts = reference.split(".")
        if len(parts) == 4 and parts[1]:
            return parts[1]
    return None


class EncryptedSecretStore:
    """Production store using AES-256-GCM key rings and optional KMS envelope encryption."""

    def __init__(self, kms_adapter: KmsAdapter | None = None) -> None:
        self._kms_adapter = kms_adapter

    @property
    def kms_adapter(self) -> KmsAdapter:
        if self._kms_adapter is not None:
            return self._kms_adapter
        return get_kms_adapter()

    def put(self, tenant_id: str, purpose: str, value: str) -> str:
        try:
            envelope, key_id = encrypt_text(
                value, tenant_id=str(tenant_id), purpose=f"prompt3:{purpose}"
            )
        except IdentitySecretsUnavailable as exc:
            raise SecretStoreError("secret store encryption is not configured") from exc
        digest = hashlib.sha256(envelope.encode()).hexdigest()[:24]
        return f"secret://encrypted/{key_id}/{digest}/{envelope}"

    def get(self, tenant_id: str, purpose: str, reference: str) -> str:
        if not isinstance(reference, str) or not reference.startswith("secret://encrypted/"):
            raise SecretStoreError("invalid secret reference")
        parts = reference.removeprefix("secret://encrypted/").split("/", 2)
        if len(parts) != 3:
            raise SecretStoreError("invalid secret reference")
        envelope = parts[2]
        try:
            return decrypt_text(
                envelope, tenant_id=str(tenant_id), purpose=f"prompt3:{purpose}"
            )
        except IdentitySecretsUnavailable as exc:
            raise SecretStoreError("secret cannot be opened") from exc

    def delete(self, tenant_id: str, purpose: str, reference: str) -> None:
        self.get(tenant_id, purpose, reference)

    async def put_with_kms(
        self,
        tenant_id: str,
        purpose: str,
        value: str,
        *,
        kms_adapter: KmsAdapter | None = None,
    ) -> str:
        """Envelope-encrypt ``value`` using a unique 256-bit DEK wrapped by ``KmsAdapter``."""
        if not isinstance(value, str) or value == "":
            raise SecretStoreError("refusing to seal an empty secret")
        adapter = kms_adapter or self.kms_adapter
        ctx = {"tenant_id": str(tenant_id), "purpose": str(purpose)}
        try:
            dek, wrapped = await adapter.generate_data_key(encryption_context=ctx)
        except KmsError as exc:
            raise SecretStoreError(str(exc)) from exc

        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(
            nonce, value.encode("utf-8"), _kms_aad(str(tenant_id), str(purpose))
        )
        wrapped_token = wrapped.to_token()
        return (
            f"secret://kms/{adapter.provider_name}/{wrapped.key_id}/"
            f"{wrapped_token}/{_b64e(nonce)}/{_b64e(ciphertext)}"
        )

    async def get_with_kms(
        self,
        tenant_id: str,
        purpose: str,
        reference: str,
        *,
        kms_adapter: KmsAdapter | None = None,
    ) -> str:
        """Open a ``secret://kms/...`` envelope by unwrapping its DEK via ``KmsAdapter``."""
        if not isinstance(reference, str) or not reference.startswith("secret://kms/"):
            raise SecretStoreError("invalid KMS secret reference")
        body = reference.removeprefix("secret://kms/")
        tail = body.rsplit("/", 3)
        if len(tail) != 4:
            raise SecretStoreError("invalid KMS secret reference")
        head, wrapped_token, nonce_b64, ct_b64 = tail
        head_parts = head.split("/", 1)
        if len(head_parts) != 2 or not head_parts[0] or not head_parts[1]:
            raise SecretStoreError("invalid KMS secret reference")
        _provider, _key_id = head_parts
        adapter = kms_adapter or self.kms_adapter
        ctx = {"tenant_id": str(tenant_id), "purpose": str(purpose)}
        try:
            wrapped = WrappedDataKey.from_token(wrapped_token)
            if wrapped.encryption_context != ctx:
                raise SecretStoreError("KMS encryption context mismatch")
            dek = await adapter.unwrap_data_key(wrapped, encryption_context=ctx)
            plaintext = AESGCM(dek).decrypt(
                _b64d(nonce_b64),
                _b64d(ct_b64),
                _kms_aad(str(tenant_id), str(purpose)),
            )
            return plaintext.decode("utf-8")
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("KMS secret reference cannot be opened") from exc

    def needs_rotation(
        self, reference: str, *, active_key_id: str | None = None
    ) -> bool:
        """Return True when ``reference`` is sealed under an older key ID."""
        current_key_id = extract_key_id(reference)
        if current_key_id is None:
            return False
        target = active_key_id
        if target is None:
            try:
                target = key_ring().active_id
            except IdentitySecretsUnavailable as exc:
                raise SecretStoreError("secret store encryption is not configured") from exc
        return current_key_id != target

    def rotate_reference(
        self,
        tenant_id: str,
        purpose: str,
        reference: str,
    ) -> tuple[str, bool]:
        """Re-seal a ``secret://encrypted/...`` reference with the active key if needed.

        Returns ``(new_reference, rotated)``. If the reference already uses the
        active key ID, it is verified by decrypting and returned unchanged with
        ``rotated=False``.
        """
        plaintext = self.get(tenant_id, purpose, reference)
        if not self.needs_rotation(reference):
            return reference, False
        return self.put(tenant_id, purpose, plaintext), True


class MemorySecretStore:
    """Ephemeral test backend. It is never selected by production code."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, str, str]] = {}

    def put(self, tenant_id: str, purpose: str, value: str) -> str:
        ref = f"secret://memory/{secrets.token_urlsafe(18)}"
        self._values[ref] = (tenant_id, purpose, value)
        return ref

    def get(self, tenant_id: str, purpose: str, reference: str) -> str:
        row = self._values.get(reference)
        if row is None or row[:2] != (tenant_id, purpose):
            raise SecretStoreError("secret reference is not valid for this tenant")
        return row[2]

    def delete(self, tenant_id: str, purpose: str, reference: str) -> None:
        row = self._values.get(reference)
        if row is not None and row[:2] != (tenant_id, purpose):
            raise SecretStoreError("secret reference is not valid for this tenant")
        self._values.pop(reference, None)


_default_store = EncryptedSecretStore()


def get_secret_store() -> SecretStore:
    return _default_store
