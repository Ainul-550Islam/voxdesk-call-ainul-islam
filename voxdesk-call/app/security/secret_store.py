"""Provider-neutral secret references.

The database stores only opaque ``secret://`` references. The production
backend is the existing AES-256-GCM identity secret implementation backed by
the deployment key ring; a process-local backend exists only for tests and
never persists across a process restart.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Protocol

from app.auth.identity.secrets import decrypt_text, encrypt_text
from app.auth.identity.exceptions import IdentitySecretsUnavailable


class SecretStoreError(RuntimeError):
    """Secret reference is invalid or the configured key ring is unavailable."""


class SecretStore(Protocol):
    def put(self, tenant_id: str, purpose: str, value: str) -> str: ...
    def get(self, tenant_id: str, purpose: str, reference: str) -> str: ...
    def delete(self, tenant_id: str, purpose: str, reference: str) -> None: ...


class EncryptedSecretStore:
    """Production store using the repository's existing envelope/key ring."""

    def put(self, tenant_id: str, purpose: str, value: str) -> str:
        try:
            envelope, key_id = encrypt_text(
                value, tenant_id=tenant_id, purpose=f"prompt3:{purpose}"
            )
        except IdentitySecretsUnavailable as exc:
            raise SecretStoreError("secret store encryption is not configured") from exc
        digest = hashlib.sha256(envelope.encode()).hexdigest()[:24]
        # This backend embeds authenticated ciphertext in the opaque
        # reference. It is not plaintext, but it is not an external HSM/vault:
        # deployments requiring reference-only custody must replace this
        # adapter with a vault-backed implementation.
        return f"secret://encrypted/{key_id}/{digest}/{envelope}"

    def get(self, tenant_id: str, purpose: str, reference: str) -> str:
        if not reference.startswith("secret://encrypted/"):
            raise SecretStoreError("invalid secret reference")
        parts = reference.removeprefix("secret://encrypted/").split("/", 2)
        if len(parts) != 3:
            raise SecretStoreError("invalid secret reference")
        envelope = parts[2]
        try:
            return decrypt_text(envelope, tenant_id=tenant_id, purpose=f"prompt3:{purpose}")
        except IdentitySecretsUnavailable as exc:
            raise SecretStoreError("secret cannot be opened") from exc

    def delete(self, tenant_id: str, purpose: str, reference: str) -> None:
        # Envelopes are immutable records; deletion is performed by the owning
        # external vault. We reject malformed references rather than pretending
        # a delete occurred.
        self.get(tenant_id, purpose, reference)


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
