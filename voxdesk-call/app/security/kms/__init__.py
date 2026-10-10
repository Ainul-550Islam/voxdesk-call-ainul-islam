"""Key Management Service (KMS) envelope-encryption adapters.

Provides the ``KmsAdapter`` protocol for wrapping and unwrapping 256-bit data
encryption keys (DEKs) with authenticated ``encryption_context`` metadata.
By default, when no external KMS (AWS KMS or HashiCorp Vault Transit) is
configured, ``get_kms_adapter()`` returns ``UnconfiguredKmsAdapter``, which
fails closed with ``KmsNotConfiguredError`` (``code = "NOT_CONFIGURED"``).
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.integrations.crm import crypto


class KmsError(RuntimeError):
    """Base error for KMS adapter failures."""

    def __init__(self, code: str, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class KmsNotConfiguredError(KmsError):
    """Raised when an external KMS operation is requested without KMS configuration."""

    def __init__(self, message: str = "External KMS adapter is not configured") -> None:
        super().__init__("NOT_CONFIGURED", message, status_code=501)


class KmsOperationError(KmsError):
    """Raised when a KMS wrap/unwrap call is rejected by the KMS provider."""

    def __init__(self, message: str = "KMS key wrap/unwrap operation failed") -> None:
        super().__init__("KMS_OPERATION_FAILED", message, status_code=502)


@dataclass(frozen=True)
class WrappedDataKey:
    """An encrypted Data Encryption Key (DEK) bound to an encryption context."""

    key_id: str
    ciphertext_blob: bytes
    provider: str = "unconfigured"
    encryption_context: dict[str, str] = field(default_factory=dict)

    def to_token(self) -> str:
        payload = {
            "v": 1,
            "p": self.provider,
            "kid": self.key_id,
            "ct": base64.urlsafe_b64encode(self.ciphertext_blob).decode("ascii").rstrip("="),
            "ctx": dict(sorted(self.encryption_context.items())),
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def from_token(cls, token: str) -> "WrappedDataKey":
        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
            ct_b64 = str(data["ct"])
            ct_bytes = base64.urlsafe_b64decode(ct_b64 + "=" * (-len(ct_b64) % 4))
            return cls(
                key_id=str(data["kid"]),
                ciphertext_blob=ct_bytes,
                provider=str(data.get("p") or "unknown"),
                encryption_context={str(k): str(v) for k, v in (data.get("ctx") or {}).items()},
            )
        except Exception as exc:
            raise KmsOperationError("Malformed wrapped data key token") from exc


@runtime_checkable
class KmsAdapter(Protocol):
    """Protocol for KMS key-wrapping adapters (AWS KMS, Vault Transit, or local key ring)."""

    provider_name: str

    def is_configured(self) -> bool: ...

    async def generate_data_key(
        self, *, encryption_context: dict[str, str] | None = None
    ) -> tuple[bytes, WrappedDataKey]: ...

    async def wrap_data_key(
        self,
        plaintext_key: bytes,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> WrappedDataKey: ...

    async def unwrap_data_key(
        self,
        wrapped_key: WrappedDataKey,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> bytes: ...


class UnconfiguredKmsAdapter:
    """Default fail-closed KMS adapter returning NOT_CONFIGURED."""

    provider_name = "unconfigured"

    def is_configured(self) -> bool:
        return False

    async def generate_data_key(
        self, *, encryption_context: dict[str, str] | None = None
    ) -> tuple[bytes, WrappedDataKey]:
        raise KmsNotConfiguredError(
            "External KMS is not configured (set AWS_KMS_KEY_ID or VAULT_TRANSIT_KEY)"
        )

    async def wrap_data_key(
        self,
        plaintext_key: bytes,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> WrappedDataKey:
        raise KmsNotConfiguredError(
            "External KMS is not configured (set AWS_KMS_KEY_ID or VAULT_TRANSIT_KEY)"
        )

    async def unwrap_data_key(
        self,
        wrapped_key: WrappedDataKey,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> bytes:
        raise KmsNotConfiguredError(
            "External KMS is not configured (set AWS_KMS_KEY_ID or VAULT_TRANSIT_KEY)"
        )


class LocalKeyRingKmsAdapter:
    """Key-wrapping adapter backed by the deployment AES-256-GCM ``KeyRing``."""

    provider_name = "local_keyring"

    def __init__(self, key_ring: crypto.KeyRing) -> None:
        self._key_ring = key_ring

    @property
    def active_key_id(self) -> str:
        return self._key_ring.active_id

    def is_configured(self) -> bool:
        return bool(self._key_ring and self._key_ring.keys)

    @staticmethod
    def _context_parts(encryption_context: dict[str, str] | None) -> tuple[str, str]:
        ctx = encryption_context or {}
        tenant_id = str(ctx.get("tenant_id") or "global")
        purpose = ":".join(
            f"{k}={v}" for k, v in sorted(ctx.items()) if k != "tenant_id"
        ) or "dek"
        return tenant_id, purpose

    async def generate_data_key(
        self, *, encryption_context: dict[str, str] | None = None
    ) -> tuple[bytes, WrappedDataKey]:
        dek = os.urandom(32)
        wrapped = await self.wrap_data_key(dek, encryption_context=encryption_context)
        return dek, wrapped

    async def wrap_data_key(
        self,
        plaintext_key: bytes,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> WrappedDataKey:
        if not isinstance(plaintext_key, (bytes, bytearray)) or len(plaintext_key) != 32:
            raise KmsOperationError("DEK must be exactly 32 bytes for AES-256")
        tenant_id, purpose = self._context_parts(encryption_context)
        dek_b64 = base64.urlsafe_b64encode(bytes(plaintext_key)).decode("ascii").rstrip("=")
        envelope, key_id = crypto.encrypt_credentials(
            {"dek": dek_b64},
            tenant_id=tenant_id,
            provider=purpose,
            key_ring=self._key_ring,
            namespace="kms_dek",
        )
        return WrappedDataKey(
            key_id=key_id,
            ciphertext_blob=envelope.encode("utf-8"),
            provider=self.provider_name,
            encryption_context=dict(encryption_context or {}),
        )

    async def unwrap_data_key(
        self,
        wrapped_key: WrappedDataKey,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> bytes:
        effective_ctx = encryption_context if encryption_context is not None else wrapped_key.encryption_context
        tenant_id, purpose = self._context_parts(effective_ctx)
        try:
            envelope = wrapped_key.ciphertext_blob.decode("utf-8")
            payload = crypto.decrypt_credentials(
                envelope,
                tenant_id=tenant_id,
                provider=purpose,
                key_ring=self._key_ring,
                namespace="kms_dek",
            )
            dek_b64 = str(payload["dek"])
            raw = base64.urlsafe_b64decode(dek_b64 + "=" * (-len(dek_b64) % 4))
        except Exception as exc:
            raise KmsOperationError("Wrapped DEK failed authentication or key lookup") from exc
        if len(raw) != 32:
            raise KmsOperationError("Unwrapped DEK has invalid length")
        return raw


def get_kms_adapter() -> KmsAdapter:
    """Resolve the configured external KMS adapter or return ``UnconfiguredKmsAdapter``."""
    aws_key_id = os.environ.get("AWS_KMS_KEY_ID", "").strip()
    if aws_key_id:
        from app.security.kms.aws_kms import AwsKmsAdapter

        return AwsKmsAdapter(
            key_id=aws_key_id,
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
            access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "").strip() or None,
            secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip() or None,
            session_token=os.environ.get("AWS_SESSION_TOKEN", "").strip() or None,
        )

    vault_addr = os.environ.get("VAULT_ADDR", "").strip()
    vault_token = os.environ.get("VAULT_TOKEN", "").strip()
    vault_key = os.environ.get("VAULT_TRANSIT_KEY", "").strip()
    if vault_addr and vault_token and vault_key:
        from app.security.kms.aws_kms import VaultTransitKmsAdapter

        return VaultTransitKmsAdapter(
            vault_addr=vault_addr,
            vault_token=vault_token,
            key_name=vault_key,
        )

    return UnconfiguredKmsAdapter()


__all__ = [
    "KmsAdapter",
    "KmsError",
    "KmsNotConfiguredError",
    "KmsOperationError",
    "LocalKeyRingKmsAdapter",
    "UnconfiguredKmsAdapter",
    "WrappedDataKey",
    "get_kms_adapter",
]
