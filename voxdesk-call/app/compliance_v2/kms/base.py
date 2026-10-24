"""Provider-independent KMS contract.

Every adapter implements the same three operations, because those are the three
an envelope scheme needs:

``generate_data_key``
    mint a fresh 256-bit data key and return it both in the clear (for immediate
    use) and wrapped by the KMS. The clear key is never stored.
``unwrap_data_key``
    turn the wrapped key back into a usable data key, with the caller's
    encryption context bound in. A wrapped key that was wrapped for another
    tenant or another column must fail to unwrap — that is the cross-tenant
    control, and it is enforced by the KMS, not by our code.
``rewrap_data_key``
    re-wrap an existing wrapped key under the current key-version, without ever
    handling the clear data key beyond the adapter. This is what rekey needs:
    the ciphertext does not have to be re-encrypted when only the wrapping key
    rotates.

Providers raise :class:`KmsUnavailable` for transient failures (throttling,
network, retryable provider errors) and :class:`KmsPermanentError` for the
cases where retrying cannot help (key disabled, permission denied, malformed
ciphertext). The envelope layer maps both onto a value-free
:class:`app.compliance_v2.kms.envelope.EnvelopeError`, so a decryption failure
is never distinguishable from an authorisation failure by an outsider.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any


class KmsError(RuntimeError):
    """Base class for key-management failures."""


class KmsUnavailable(KmsError):
    """The provider could not be reached, or asked us to retry."""


class KmsPermanentError(KmsError):
    """The request will not succeed on retry (disabled key, bad ciphertext...)."""


class KmsNotConfigured(KmsError):
    """No provider is configured for the requested region or deployment."""


@dataclasses.dataclass
class DataKey:
    """A data key: clear material for immediate use, plus its wrapped form."""

    plaintext: bytes
    wrapped: bytes
    provider: str
    key_id: str

    def __post_init__(self) -> None:
        if len(self.plaintext) != 32:
            raise ValueError("data keys are 256-bit")
        if not self.wrapped:
            raise ValueError("a data key must carry its wrapped form")

    def destroy(self) -> None:
        """Best-effort scrub of the in-memory copy.

        Python cannot guarantee a buffer is zeroed — bytes are immutable and the
        garbage collector may have copied them. Dropping the reference is the
        best available action, and it is described here rather than implied to
        be more than it is.
        """
        self.plaintext = b""


@dataclasses.dataclass(frozen=True)
class KeyHealth:
    provider: str
    key_id: str
    reachable: bool
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Alias used by the evidence collector: a key we can use is healthy."""
        return self.reachable

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "key_id": self.key_id,
            "reachable": self.reachable,
            "ok": self.ok,
            "detail": self.detail,
        }


class KmsProvider(ABC):
    """The adapter contract. All methods are async: every real provider is I/O."""

    #: Short provider name recorded in every envelope header.
    name: str = "unknown"

    @property
    @abstractmethod
    def key_id(self) -> str:
        """Identifier of the wrapping key currently in use."""

    @abstractmethod
    async def generate_data_key(self, *, context: dict[str, str]) -> DataKey:
        """Mint a data key, wrapped for ``context``."""

    @abstractmethod
    async def unwrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        """Unwrap a data key bound to ``context``."""

    @abstractmethod
    async def rewrap_data_key(self, wrapped: bytes, *, context: dict[str, str]) -> bytes:
        """Re-wrap an existing wrapped key under the current key version."""

    @abstractmethod
    async def health(self) -> KeyHealth:
        """A cheap reachability/configuration probe for the evidence collector."""

    async def list_key_versions(self) -> list[str]:
        """Key versions the provider can currently unwrap with.

        Adapters that cannot enumerate return the current key only; the rekey
        planner treats that as "one version" rather than guessing.
        """
        return [self.key_id]

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "key_id": self.key_id}


def encrypt_context(*, tenant_id: str, column: str, purpose: str = "") -> dict[str, str]:
    """The encryption context bound into every wrap and unwrap.

    Bound into the KMS call itself (AWS/GCP/Azure all support this), so a
    wrapped key stolen from one tenant's row cannot be unwrapped while
    presenting another tenant's context — and the failure happens inside the
    provider, before any ciphertext is produced.
    """
    context = {"tenant": str(tenant_id), "column": str(column)}
    if purpose:
        context["purpose"] = str(purpose)
    return context


__all__ = [
    "DataKey",
    "KeyHealth",
    "KmsError",
    "KmsNotConfigured",
    "KmsPermanentError",
    "KmsProvider",
    "KmsUnavailable",
    "encrypt_context",
]
