"""Ed25519 key handling for signed audit exports.

Why Ed25519 and not HMAC: an export is verified by someone who does not hold a
secret of ours. A signature scheme that needs the verifier to know a shared key
would let any verifier forge an export. Ed25519 gives a 32-byte public key that
can be published in a contract annex, and verification that needs nothing else.

Key handling rules:

* the private key lives at ``AUDIT_SIGNING_KEY_PATH`` (PEM, PKCS8, ``0600``);
* when the file is absent and the deployment is **not** production, a key is
  generated once and persisted, so a developer gets working signatures without
  a manual step — a production deployment without a key fails closed instead of
  inventing one, because a key materialised at boot is a key nobody escrowed;
* every signature carries the *key id* (first 16 hex characters of the SHA-256
  of the public key), so a verifier knows which published key to use;
* :func:`rotate_signing_key` archives the replaced key next to the new one and
  appends to the rotation history, which is what makes a historical signature
  verifiable after a rotation.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings

#: Algorithm name recorded alongside every signature.
ALGORITHM = "ed25519"
_HISTORY_SUFFIX = ".history.json"


class SigningKeyError(RuntimeError):
    """No usable signing key exists, or a key file is malformed."""


@dataclass(frozen=True)
class Signer:
    """A loaded private key plus the identity a verifier needs."""

    key_id: str
    private_key: Ed25519PrivateKey
    public_key_pem: str
    path: str

    def sign(self, payload: bytes) -> str:
        """Return the base64 signature over ``payload``."""
        return base64.b64encode(self.private_key.sign(payload)).decode("ascii")


def key_id_for(public_key: Ed25519PublicKey) -> str:
    """Derive the stable key id from the public key bytes."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:16]


def public_key_pem_of(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def public_key_from_pem(pem: str) -> Ed25519PublicKey:
    try:
        loaded = serialization.load_pem_public_key(pem.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SigningKeyError("public key is not a valid PEM Ed25519 key") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise SigningKeyError("public key is not an Ed25519 key")
    return loaded


def _write_private_key(path: Path, key: Ed25519PrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError, OSError) as exc:
        raise SigningKeyError(f"signing key at {path} cannot be loaded") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise SigningKeyError("signing key is not an Ed25519 private key")
    return loaded


def load_signer(
    path: str | Path | None = None,
    *,
    settings: ComplianceV2Settings | None = None,
    create_when_absent: bool | None = None,
) -> Signer:
    """Load the deployment's signing key, generating one outside production."""
    cfg = settings or get_compliance_v2_settings()
    key_path = Path(path or cfg.audit_signing_key_path)
    allow_create = (not cfg.is_production) if create_when_absent is None else create_when_absent

    if not key_path.exists():
        if not allow_create:
            raise SigningKeyError(
                f"no audit signing key at {key_path}; refusing to invent one in production"
            )
        _write_private_key(key_path, Ed25519PrivateKey.generate())

    private_key = _load_private_key(key_path)
    public_key = private_key.public_key()
    return Signer(
        key_id=key_id_for(public_key),
        private_key=private_key,
        public_key_pem=public_key_pem_of(public_key),
        path=str(key_path),
    )


def sign_bytes(signer: Signer, payload: bytes) -> dict[str, str]:
    """The signature envelope stored next to a bundle."""
    return {
        "algorithm": ALGORITHM,
        "key_id": signer.key_id,
        "signature": signer.sign(payload),
        "signed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "public_key_pem": signer.public_key_pem,
    }


def verify_bytes(payload: bytes, *, signature: str, public_key_pem: str) -> bool:
    """Verify a base64 Ed25519 signature. Never raises for a bad signature."""
    public_key = public_key_from_pem(public_key_pem)
    try:
        raw = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError):
        return False
    try:
        public_key.verify(raw, payload)
    except InvalidSignature:
        return False
    return True


def verify_with_key_id(
    payload: bytes, *, signature: str, public_key_pem: str, key_id: str
) -> bool:
    """Verify and additionally check that the key id matches the supplied key."""
    public_key = public_key_from_pem(public_key_pem)
    if key_id_for(public_key) != key_id:
        return False
    return verify_bytes(payload, signature=signature, public_key_pem=public_key_pem)


def _history_path(key_path: Path) -> Path:
    return key_path.with_name(key_path.name + _HISTORY_SUFFIX)


def rotation_history(path: str | Path | None = None, *, settings: ComplianceV2Settings | None = None) -> list[dict]:
    """Every rotation recorded next to the key, oldest first."""
    cfg = settings or get_compliance_v2_settings()
    history = _history_path(Path(path or cfg.audit_signing_key_path))
    if not history.exists():
        return []
    try:
        parsed = json.loads(history.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def rotate_signing_key(
    path: str | Path | None = None, *, settings: ComplianceV2Settings | None = None
) -> dict:
    """Replace the key, archive the old one and record the rotation.

    The archived private key stays on disk with ``0600`` permissions: signatures
    made before the rotation must remain verifiable, which needs the old public
    key — and keeping only the public half would make the archive unable to
    re-sign evidence during an investigation.
    """
    cfg = settings or get_compliance_v2_settings()
    key_path = Path(path or cfg.audit_signing_key_path)
    previous = load_signer(key_path, settings=cfg, create_when_absent=False) if key_path.exists() else None

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived_path = key_path.with_name(f"{key_path.name}.{stamp}.archived")
    if previous is not None:
        archived_path.parent.mkdir(parents=True, exist_ok=True)
        archived_path.write_bytes(key_path.read_bytes())
        os.chmod(archived_path, stat.S_IRUSR | stat.S_IWUSR)

    _write_private_key(key_path, Ed25519PrivateKey.generate())
    current = load_signer(key_path, settings=cfg, create_when_absent=False)

    entry = {
        "rotated_at": stamp,
        "new_key_id": current.key_id,
        "previous_key_id": previous.key_id if previous else "",
        "previous_key_archive": str(archived_path) if previous else "",
        "previous_public_key_pem": previous.public_key_pem if previous else "",
    }
    history_file = _history_path(key_path)
    history = rotation_history(key_path, settings=cfg)
    history.append(entry)
    history_file.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
    return entry


def published_public_key(settings: ComplianceV2Settings | None = None) -> dict[str, str]:
    """The public key a verifier needs, in the form the API returns it."""
    cfg = settings or get_compliance_v2_settings()
    signer = load_signer(settings=cfg)
    return {
        "algorithm": ALGORITHM,
        "key_id": signer.key_id,
        "public_key_pem": signer.public_key_pem,
    }


__all__ = [
    "ALGORITHM",
    "Signer",
    "SigningKeyError",
    "key_id_for",
    "load_signer",
    "public_key_from_pem",
    "public_key_pem_of",
    "published_public_key",
    "rotate_signing_key",
    "rotation_history",
    "sign_bytes",
    "verify_bytes",
    "verify_with_key_id",
]
