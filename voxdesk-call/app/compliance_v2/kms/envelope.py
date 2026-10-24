"""Envelope encryption for stored secrets, with a readable legacy path.

Two-layer scheme:

1. the KMS mints (or helps mint) a **data key** and wraps it, binding the
   tenant and the column name into the encryption context;
2. the value is sealed with that data key using AES-256-GCM, with the same
   context as associated data.

Both bindings are deliberate. The KMS context stops a wrapped key from opening
under another tenant's identity; the GCM associated data stops ciphertext from
being moved between columns or tenants even when the same data key is somehow
available. Together they make "copy the row to another tenant" fail at two
independent layers.

Stored format, single line, URL-safe base64, prefixed so a reader can tell what
it is looking at without a schema lookup::

    env1.<provider>.<wrapped>.<nonce>.<ciphertext>

``v1.<key_id>.<nonce>.<ciphertext>`` values written by
``app.integrations.crm.crypto`` (CRM credentials, webhook secrets, MFA seeds,
SSO client secrets, OAuth tokens) are still readable through
:func:`decrypt_legacy`; :mod:`app.compliance_v2.kms.rekey_job` rewrites them into
``env1`` without changing the plaintext.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.compliance_v2.kms.base import KmsError, KmsProvider, encrypt_context

PREFIX = "env1."
LEGACY_PREFIX = "v1."
_NONCE_BYTES = 12


class EnvelopeError(KmsError):
    """A value could not be sealed or opened. Never carries the plaintext."""


class EnvelopeAuthError(EnvelopeError):
    """The value does not authenticate under this key and context."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (ValueError, binascii.Error) as exc:
        raise EnvelopeAuthError("value is not valid base64") from exc


def is_envelope(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def is_legacy(value: object) -> bool:
    return isinstance(value, str) and value.startswith(LEGACY_PREFIX)


def _aad(*, tenant_id: str, column: str, purpose: str) -> bytes:
    return f"voxdesk.envelope.v1|{tenant_id}|{column}|{purpose}".encode("utf-8")


@dataclass(frozen=True)
class EnvelopeHeader:
    provider: str
    wrapped: bytes
    nonce: bytes
    ciphertext: bytes

    @property
    def key_id_hint(self) -> str:
        """A short fingerprint of the wrapped key, for operator diagnostics."""
        import hashlib

        return hashlib.sha256(self.wrapped).hexdigest()[:16]


def parse(token: str) -> EnvelopeHeader:
    """Split an ``env1`` token. Raises :class:`EnvelopeAuthError` when malformed."""
    if not is_envelope(token):
        raise EnvelopeAuthError("value is not in the envelope format")
    parts = token[len(PREFIX) :].split(".")
    if len(parts) != 4:
        raise EnvelopeAuthError("envelope is missing components")
    provider, wrapped, nonce, ciphertext = parts
    return EnvelopeHeader(
        provider=provider,
        wrapped=_unb64(wrapped),
        nonce=_unb64(nonce),
        ciphertext=_unb64(ciphertext),
    )


def render(*, provider: str, wrapped: bytes, nonce: bytes, ciphertext: bytes) -> str:
    return PREFIX + ".".join(
        [provider, _b64(wrapped), _b64(nonce), _b64(ciphertext)]
    )


async def encrypt_value(
    plaintext: str,
    *,
    tenant_id: str,
    column: str,
    provider: KmsProvider,
    purpose: str = "",
) -> str:
    """Seal one value. Returns the ``env1`` token."""
    if not isinstance(plaintext, str) or plaintext == "":
        raise EnvelopeError("refusing to encrypt an empty value")
    context = encrypt_context(tenant_id=tenant_id, column=column, purpose=purpose)
    data_key = await provider.generate_data_key(context=context)
    try:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = AESGCM(data_key.plaintext).encrypt(
            nonce, plaintext.encode("utf-8"), _aad(tenant_id=tenant_id, column=column, purpose=purpose)
        )
        return render(
            provider=str(getattr(provider, "name", "unknown")),
            wrapped=data_key.wrapped,
            nonce=nonce,
            ciphertext=sealed,
        )
    finally:
        data_key.destroy()


async def decrypt_value(
    token: str,
    *,
    tenant_id: str,
    column: str,
    provider: KmsProvider,
    purpose: str = "",
) -> str:
    """Open an ``env1`` token. Raises :class:`EnvelopeAuthError` on any mismatch."""
    header = parse(token)
    context = encrypt_context(tenant_id=tenant_id, column=column, purpose=purpose)
    try:
        data_key = await provider.unwrap_data_key(header.wrapped, context=context)
    except KmsError as exc:
        raise EnvelopeAuthError("wrapped key does not open under this context") from exc
    try:
        raw = AESGCM(data_key).decrypt(
            header.nonce,
            header.ciphertext,
            _aad(tenant_id=tenant_id, column=column, purpose=purpose),
        )
    except (InvalidTag, ValueError) as exc:
        raise EnvelopeAuthError("ciphertext does not authenticate") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvelopeAuthError("decrypted value is not valid UTF-8") from exc


async def rotate_value(
    token: str,
    *,
    tenant_id: str,
    column: str,
    provider: KmsProvider,
    purpose: str = "",
) -> str:
    """Re-wrap the data key under the current version; ciphertext untouched.

    This is the cheap half of a rotation: the wrapping key changes, the sealed
    bytes and the nonce stay, so the operation is idempotent and cannot corrupt
    the payload.
    """
    header = parse(token)
    context = encrypt_context(tenant_id=tenant_id, column=column, purpose=purpose)
    try:
        rewrapped = await provider.rewrap_data_key(header.wrapped, context=context)
    except KmsError as exc:
        raise EnvelopeAuthError("wrapped key could not be re-wrapped") from exc
    return render(
        provider=str(getattr(provider, "name", "unknown")),
        wrapped=rewrapped,
        nonce=header.nonce,
        ciphertext=header.ciphertext,
    )


def decrypt_legacy(
    token: str,
    *,
    tenant_id: str,
    purpose: str,
) -> str:
    """Open a ``v1.`` value written by the pre-Batch-03 cipher.

    ``purpose`` is the value the writer passed as ``provider`` in
    ``crypto.encrypt_credentials`` — ``outbound_webhook``, ``totp``,
    ``oidc_client_secret``, or a CRM provider name.
    """
    from app.auth.identity.secrets import decrypt_text

    try:
        return decrypt_text(token, tenant_id=str(tenant_id), purpose=purpose)
    except Exception as exc:  # noqa: BLE001 - any failure is an auth failure here
        raise EnvelopeAuthError("legacy value does not open under this context") from exc


async def read_value(
    token: str,
    *,
    tenant_id: str,
    column: str,
    provider: KmsProvider,
    purpose: str = "",
    legacy_purpose: str | None = None,
) -> str:
    """Read a value that may be either format.

    The legacy path is only attempted when the caller supplies the purpose it
    was written with; without it the reader fails rather than guessing, because
    guessing wrong turns a decryption failure into a silent mismatch.
    """
    if is_envelope(token):
        return await decrypt_value(
            token, tenant_id=tenant_id, column=column, provider=provider, purpose=purpose
        )
    if is_legacy(token) and legacy_purpose is not None:
        return decrypt_legacy(token, tenant_id=tenant_id, purpose=legacy_purpose)
    raise EnvelopeAuthError("value is in an unsupported format")


def needs_rewrap(token: str, *, provider_name: str) -> bool:
    """True when a value should be rewritten by the rekey job."""
    if not is_envelope(token):
        return True
    return parse(token).provider != provider_name


__all__ = [
    "LEGACY_PREFIX",
    "PREFIX",
    "EnvelopeAuthError",
    "EnvelopeError",
    "EnvelopeHeader",
    "decrypt_legacy",
    "decrypt_value",
    "encrypt_value",
    "is_envelope",
    "is_legacy",
    "needs_rewrap",
    "parse",
    "read_value",
    "render",
    "rotate_value",
]
