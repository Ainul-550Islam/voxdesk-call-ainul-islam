"""Recording media access.

VoxDesk does not copy provider media by default. A grant is a short HMAC over
the tenant, the recording and an expiry. It is not a public object URL and it
does not embed a bearer token or a provider signature. Bytes, when stored,
live under a tenant-prefixed key. This module does not claim encryption at rest.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

_PREFIX = "voxdesk-recording-v1"
_SECRET_MARKERS = ("sig=", "signature=", "token=", "bearer", "auth_token=")


@dataclass(frozen=True)
class AccessGrant:
    token: str
    expires_at: int
    recording_id: str
    tenant_id: str

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "expires_at": self.expires_at,
            "recording_id": self.recording_id,
            "tenant_id": self.tenant_id,
            "permanent": False,
        }


@dataclass(frozen=True)
class AccessVerdict:
    allowed: bool
    reason: str

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason}


def storage_key(tenant_id: uuid.UUID, recording_id: uuid.UUID) -> str:
    return f"tenant/{tenant_id}/recordings/{recording_id}"


def issue(
    tenant_id: uuid.UUID,
    recording_id: uuid.UUID,
    *,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> AccessGrant:
    ttl = ttl_seconds if ttl_seconds is not None else int(settings.recording_url_ttl_seconds or 120)
    if ttl < 1 or ttl > 900:
        raise ValueError("Recording grant TTL must be between 1 and 900 seconds")
    issued = int(now if now is not None else time.time())
    expires = issued + ttl
    payload = f"{tenant_id}.{recording_id}.{expires}"
    signature = _sign(payload)
    return AccessGrant(
        token=f"{payload}.{signature}",
        expires_at=expires,
        recording_id=str(recording_id),
        tenant_id=str(tenant_id),
    )


def verify(
    token: str | None,
    *,
    tenant_id: uuid.UUID,
    recording_id: uuid.UUID,
    now: int | None = None,
) -> AccessVerdict:
    if not token or token.count(".") != 3:
        return AccessVerdict(False, "malformed")
    tenant_raw, recording_raw, exp_raw, signature = token.split(".", 3)
    if tenant_raw != str(tenant_id) or recording_raw != str(recording_id):
        return AccessVerdict(False, "mismatch")
    try:
        expires = int(exp_raw)
    except ValueError:
        return AccessVerdict(False, "malformed")
    current = int(now if now is not None else time.time())
    if current >= expires:
        return AccessVerdict(False, "expired")
    expected = _sign(f"{tenant_raw}.{recording_raw}.{exp_raw}")
    if not hmac.compare_digest(expected, signature):
        return AccessVerdict(False, "signature_rejected")
    return AccessVerdict(True, "valid")


def safe_reference(value: str | None) -> str | None:
    """Drop provider URLs that carry a signature or bearer. Never return those."""
    if not value:
        return None
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return None
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return None
    return value


def put_bytes(tenant_id: uuid.UUID, recording_id: uuid.UUID, data: bytes) -> tuple[str, str, int]:
    root = (settings.telephony_media_dir or "").strip()
    if not root:
        raise RuntimeError("Recording media directory is not configured")
    if not data:
        raise RuntimeError("Empty recording is not stored")
    key = storage_key(tenant_id, recording_id)
    path = Path(root) / key
    if not str(path.resolve()).startswith(str(Path(root).resolve())):
        raise RuntimeError("Recording key escaped the media directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return key, digest, len(data)


def delete_bytes(key: str) -> None:
    root = (settings.telephony_media_dir or "").strip()
    if not root or not key or not key.startswith("tenant/"):
        return
    path = Path(root) / key
    if path.is_file() and str(path.resolve()).startswith(str(Path(root).resolve())):
        path.unlink()


def _sign(payload: str) -> str:
    return hmac.new(
        settings.jwt_secret.encode(),
        f"{_PREFIX}:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
