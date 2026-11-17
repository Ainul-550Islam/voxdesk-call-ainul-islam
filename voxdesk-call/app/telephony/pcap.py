"""Per-call packet capture on the SIP gateway path (PART 13, P13-11).

Support engineering needs the actual SIP/RTP packets to answer "why did this
one call have no audio?". That is a *debug* artefact, not a recording:

* it is captured on the SIP gateway host only -- never on the app process, so
  a capture cannot see another tenant's traffic by accident;
* the capture is addressed by call id, so the gateway hands back exactly one
  call's packets;
* the file lands in object storage under a random key, and the row in
  `call_debug_artifacts` is the only way to find it;
* access is RBAC-gated (`calls.debug`), audited on the row, and the URL that
  comes back expires. The dropped `pcap_artifacts` table is not reused: it had
  no retention column, which is how "temporary" debug captures become permanent
  liabilities.

The storage backend is an interface. `LocalCaptureStore` writes to disk and
signs its own URLs with HMAC -- correct for a single host and for the tests,
and honest about being local. Production passes an S3 implementation with the
same three methods; nothing else in this module changes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

DEFAULT_RETENTION_HOURS = 72
MAX_RETENTION_HOURS = 24 * 30
DEFAULT_URL_TTL_SECONDS = 300
#: A capture that claims to be larger than this is not a debug artefact.
MAX_CAPTURE_BYTES = 512 * 1024 * 1024
CLASSIC_PCAP_MAGIC_LE = b"\xd4\xc3\xb2\xa1"
CLASSIC_PCAP_HEADER_BYTES = 24
DLT_RAW = 101


class CaptureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CaptureRequest:
    """What the gateway is asked to capture."""

    tenant_id: str
    call_id: str
    seconds: int = 30
    snaplen: int = 262
    direction: str = "both"
    # The application database UUID can differ from a carrier call ID. When
    # present, this is the gateway's live-call key; artifact ownership remains
    # scoped to ``call_id`` above.
    gateway_call_id: str = ""

    def validate(self) -> None:
        if not self.tenant_id:
            raise CaptureError("missing_tenant_id", "a capture needs a tenant id")
        if not self.call_id:
            raise CaptureError("missing_call_id", "a capture needs a call id")
        if self.gateway_call_id and (
            len(self.gateway_call_id) > 128
            or any(ord(character) < 33 or ord(character) > 126 for character in self.gateway_call_id)
        ):
            raise CaptureError("invalid_call_id", "the gateway call id is malformed")
        if not 1 <= int(self.seconds) <= 300:
            raise CaptureError("invalid_duration", "capture length must be 1-300 seconds")
        if not 64 <= int(self.snaplen) <= 65535:
            raise CaptureError("invalid_snaplen", "snaplen must be 64-65535")
        if self.direction not in ("both", "inbound", "outbound"):
            raise CaptureError("invalid_direction", f"unknown direction {self.direction!r}")

    def as_gateway_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "tenant_id": self.tenant_id,
            "call_id": self.gateway_call_id or self.call_id,
            "duration_seconds": int(self.seconds),
            "snaplen": int(self.snaplen),
            "direction": self.direction,
        }


class CaptureStore(Protocol):
    async def put(self, key: str, data: bytes) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def signed_url(self, key: str, *, expires_in: int) -> str: ...

    async def delete(self, key: str) -> bool: ...

    def verify(self, key: str, *, expires: int, signature: str, now: int | None = None) -> bool: ...


class LocalCaptureStore:
    """Disk-backed store with HMAC-signed URLs.

    Deliberately not a pretend S3: the URL it signs is served by this app's
    `/api/calls/{id}/pcap/{token}` route, and the token carries the key and
    expiry inside the signature.
    """

    def __init__(self, root: str | Path, *, secret: str) -> None:
        if not secret:
            raise CaptureError("missing_secret", "the capture store needs a signing secret")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._secret = secret.encode()

    def _path(self, key: str) -> Path:
        # Keys are server-generated (`{tenant}/{call}/{random}.pcap`); use a
        # path-component check so sibling prefixes cannot pass as descendants.
        root = self.root.resolve()
        candidate = (root / key).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise CaptureError("invalid_key", "capture key escapes the storage root") from exc
        return candidate

    async def put(self, key: str, data: bytes) -> str:
        if len(data) > MAX_CAPTURE_BYTES:
            raise CaptureError("capture_too_large", "the capture exceeds the artefact size limit")
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_bytes, data)
        except OSError as exc:
            raise CaptureError("storage_write_failed", "could not store the capture") from exc
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise CaptureError("artifact_missing", "the capture has been purged or is unavailable") from exc
        except OSError as exc:
            raise CaptureError("storage_read_failed", "local capture storage could not deliver the file") from exc

    async def signed_url(self, key: str, *, expires_in: int = DEFAULT_URL_TTL_SECONDS) -> str:
        if not 1 <= int(expires_in) <= MAX_RETENTION_HOURS * 3600:
            raise CaptureError("invalid_ttl", "signed URL lifetime must be 1 second to 30 days")
        expires = int(time.time()) + int(expires_in)
        mac = hmac.new(self._secret, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()
        return f"/api/calls/debug-artifacts/{key}?expires={expires}&sig={mac}"

    async def delete(self, key: str) -> bool:
        path = self._path(key)

        def _delete() -> bool:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True

        try:
            return await asyncio.to_thread(_delete)
        except OSError as exc:
            raise CaptureError("storage_delete_failed", "could not delete the expired capture") from exc

    def verify(self, key: str, *, expires: int, signature: str, now: int | None = None) -> bool:
        """Check a signed URL. Expired signatures are rejected, not refreshed."""
        if (now or int(time.time())) > int(expires):
            return False
        expected = hmac.new(
            self._secret, f"{key}:{int(expires)}".encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature or "")



class S3CaptureStore:
    """S3-compatible PCAP storage with app-minted, short-lived download links.

    The bucket URL is never returned to a browser. The application verifies
    the HMAC token and retrieves the object after the link is presented.
    """

    PREFIX = "telephony-pcap/"

    def __init__(self, *, secret: str, storage: Any = None) -> None:
        if not secret:
            raise CaptureError("missing_secret", "the capture store needs a signing secret")
        if storage is None:
            from app.knowledge.storage.s3 import S3Storage

            storage = S3Storage()
        self._storage = storage
        self._secret = secret.encode()

    @staticmethod
    def _validate_key(key: str) -> str:
        value = str(key or "")
        parts = value.split("/")
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise CaptureError("invalid_key", "capture key is invalid")
        return value

    def _object_key(self, key: str) -> str:
        return self.PREFIX + self._validate_key(key)

    async def put(self, key: str, data: bytes) -> str:
        if len(data) > MAX_CAPTURE_BYTES:
            raise CaptureError("capture_too_large", "the capture exceeds the artefact size limit")
        object_key = self._object_key(key)
        try:
            await self._storage.put(
                object_key, data, content_type="application/vnd.tcpdump.pcap"
            )
        except Exception as exc:
            raise CaptureError("storage_write_failed", "could not store the capture") from exc
        return key

    @staticmethod
    def _is_missing_object_error(exc: Exception) -> bool:
        current: BaseException | None = exc
        while current is not None:
            if isinstance(current, (KeyError, FileNotFoundError)):
                return True
            response = getattr(current, "response", None)
            if isinstance(response, dict):
                error = response.get("Error")
                code = str(error.get("Code") or "") if isinstance(error, dict) else ""
                metadata = response.get("ResponseMetadata")
                status_code = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
                if code in {"404", "NoSuchKey", "NoSuchObject", "NotFound"}:
                    return True
                # Some S3-compatible stores omit Error.Code on an HTTP 404. Do
                # not classify an explicit different code (for example
                # NoSuchBucket) as a missing object merely because it is 404.
                if not code and status_code == 404:
                    return True
            current = current.__cause__
        return False

    async def get(self, key: str) -> bytes:
        object_key = self._object_key(key)
        try:
            return await self._storage.get(object_key)
        except Exception as exc:
            if self._is_missing_object_error(exc):
                raise CaptureError("artifact_missing", "the capture has been purged or is unavailable") from exc
            raise CaptureError("storage_read_failed", "object storage could not deliver the capture") from exc

    async def signed_url(self, key: str, *, expires_in: int = DEFAULT_URL_TTL_SECONDS) -> str:
        self._validate_key(key)
        if not 1 <= int(expires_in) <= MAX_RETENTION_HOURS * 3600:
            raise CaptureError("invalid_ttl", "signed URL lifetime must be 1 second to 30 days")
        expires = int(time.time()) + int(expires_in)
        mac = hmac.new(self._secret, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()
        return f"/api/calls/debug-artifacts/{key}?expires={expires}&sig={mac}"

    async def delete(self, key: str) -> bool:
        object_key = self._object_key(key)

        def _delete() -> None:
            # Use the low-level operation because the general knowledge-store
            # adapter intentionally treats deletion as best effort. Retention
            # must not mark a PCAP purged when the object delete actually failed.
            self._storage._get_client().delete_object(
                Bucket=self._storage.bucket, Key=object_key
            )

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:
            raise CaptureError("storage_delete_failed", "could not delete the expired capture") from exc
        return True

    def verify(self, key: str, *, expires: int, signature: str, now: int | None = None) -> bool:
        try:
            value = self._validate_key(key)
        except CaptureError:
            return False
        moment = int(time.time()) if now is None else int(now)
        if moment > int(expires):
            return False
        expected = hmac.new(
            self._secret, f"{value}:{int(expires)}".encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature or "")


def build_storage_key(tenant_id: str, call_id: str, *, suffix: str = "pcap") -> str:
    return f"{tenant_id}/{call_id}/{secrets.token_hex(8)}.{suffix}"


def retention_deadline(
    *, hours: int | None = None, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Return ``(captured_at, expires_at)`` for a new artefact."""
    captured = now or datetime.now(timezone.utc)
    window = DEFAULT_RETENTION_HOURS if hours is None else int(hours)
    if not 1 <= window <= MAX_RETENTION_HOURS:
        raise CaptureError(
            "invalid_retention", f"retention must be 1-{MAX_RETENTION_HOURS} hours"
        )
    return captured, captured + timedelta(hours=window)


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CaptureOutcome:
    artifact_id: str
    storage_key: str
    size_bytes: int
    sha256: str
    expires_at: datetime
    packet_count: int
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "storage_key": self.storage_key,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "expires_at": self.expires_at.isoformat(),
            "packet_count": self.packet_count,
            "truncated": self.truncated,
        }


def _validate_classic_raw_pcap(data: bytes) -> int:
    """Validate the gateway's PCAP stream and return its packet-record count."""
    if len(data) < CLASSIC_PCAP_HEADER_BYTES or data[:4] != CLASSIC_PCAP_MAGIC_LE:
        raise CaptureError("invalid_capture", "the gateway response is not a classic PCAP file")
    if int.from_bytes(data[4:6], "little") != 2 or int.from_bytes(data[6:8], "little") != 4:
        raise CaptureError("invalid_capture", "the gateway PCAP version is unsupported")
    snaplen = int.from_bytes(data[16:20], "little")
    if snaplen < 1 or snaplen > 65535:
        raise CaptureError("invalid_capture", "the gateway PCAP snaplen is invalid")
    if int.from_bytes(data[20:24], "little") != DLT_RAW:
        raise CaptureError("invalid_capture", "the gateway PCAP link type is unsupported")

    offset = CLASSIC_PCAP_HEADER_BYTES
    packet_count = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < 16:
            raise CaptureError("invalid_capture", "the gateway PCAP has a truncated packet header")
        timestamp_usec = int.from_bytes(data[offset + 4 : offset + 8], "little")
        captured_length = int.from_bytes(data[offset + 8 : offset + 12], "little")
        original_length = int.from_bytes(data[offset + 12 : offset + 16], "little")
        if timestamp_usec >= 1_000_000 or captured_length > snaplen or captured_length > original_length:
            raise CaptureError("invalid_capture", "the gateway PCAP packet metadata is invalid")
        offset += 16 + captured_length
        if offset > len(data):
            raise CaptureError("invalid_capture", "the gateway PCAP has a truncated packet")
        packet_count += 1
    return packet_count


async def capture_call(
    session,
    *,
    tenant_id,
    call_id,
    gateway: Any,
    store: CaptureStore,
    request: CaptureRequest | None = None,
    retention_hours: int | None = None,
    now: datetime | None = None,
):
    """Ask the gateway for a capture, store it and write the audit row.

    The row is written only after the bytes are in the store: an artefact row
    that points at nothing is worse than a failed capture, because it makes the
    support engineer believe evidence exists.
    """
    from app.db.telephony_models import CallDebugArtifact

    capture = request or CaptureRequest(tenant_id=str(tenant_id), call_id=str(call_id))
    if str(capture.tenant_id) != str(tenant_id) or str(capture.call_id) != str(call_id):
        raise CaptureError("scope_mismatch", "capture tenant and call must match the authorized request")
    payload = capture.as_gateway_payload()
    # Validate retention before asking the gateway to spend capture time or
    # writing a file that would have no valid expiry window.
    captured_at, expires_at = retention_deadline(hours=retention_hours, now=now)

    fetch = getattr(gateway, "capture", None)
    if fetch is None:
        raise CaptureError("gateway_unsupported", "the SIP gateway does not offer packet capture")
    data = await fetch(payload)
    reported_packet_count: int | None = None
    truncated = False
    if isinstance(data, (bytes, bytearray)):
        blob = bytes(data)
    elif isinstance(data, dict):
        encoded = data.get("data_base64")
        if not isinstance(encoded, str) or not encoded:
            raise CaptureError("invalid_capture", "the gateway returned no packet-capture bytes")
        max_encoded_bytes = 4 * ((MAX_CAPTURE_BYTES + 2) // 3)
        if len(encoded) > max_encoded_bytes:
            raise CaptureError("capture_too_large", "the capture exceeds the artefact size limit")
        if "packet_count" in data:
            reported_count = data["packet_count"]
            if type(reported_count) is not int or reported_count < 0:
                raise CaptureError("invalid_capture", "the gateway packet count is invalid")
            reported_packet_count = reported_count
        if "truncated" in data:
            reported_truncated = data["truncated"]
            if type(reported_truncated) is not bool:
                raise CaptureError("invalid_capture", "the gateway truncation flag is invalid")
            truncated = reported_truncated
        try:
            blob = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise CaptureError("invalid_capture", "the gateway returned malformed capture data") from exc
    else:
        resolved = await data if hasattr(data, "__await__") else data
        try:
            blob = bytes(resolved)
        except (TypeError, ValueError) as exc:
            raise CaptureError("invalid_capture", "the gateway returned invalid capture data") from exc
    if len(blob) > MAX_CAPTURE_BYTES:
        raise CaptureError("capture_too_large", "the capture exceeds the artefact size limit")
    packet_count = _validate_classic_raw_pcap(blob)
    if reported_packet_count is not None and reported_packet_count != packet_count:
        raise CaptureError(
            "invalid_capture", "the gateway packet count does not match the PCAP records"
        )

    key = build_storage_key(str(tenant_id), str(call_id))
    await store.put(key, blob)
    row = CallDebugArtifact(
        tenant_id=tenant_id,
        call_id=call_id,
        kind="pcap",
        storage_key=key,
        size_bytes=len(blob),
        sha256=checksum(blob),
        packet_count=packet_count,
        truncated=truncated,
        captured_at=captured_at,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return CaptureOutcome(
        str(row.id), key, len(blob), row.sha256, expires_at, packet_count, truncated
    )


async def sign_download(
    session,
    artifact,
    *,
    store: CaptureStore,
    user_id: str,
    ttl_seconds: int = DEFAULT_URL_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mint a short-lived URL and record who asked for it."""
    moment = now or datetime.now(timezone.utc)
    if artifact.purged_at is not None:
        raise CaptureError("artifact_purged", "this capture has been purged under retention")
    expires_at = artifact.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if moment >= expires_at:
        raise CaptureError("artifact_expired", "this capture is past its retention window")
    # Never let a freshly minted download link outlive the artefact's own
    # retention window, even if the purge worker has not run yet. The one-second
    # margin accounts for the URL store rounding its issue time to whole seconds.
    remaining_ttl = int(expires_at.timestamp()) - int(time.time()) - 1
    if remaining_ttl < 1:
        raise CaptureError("artifact_expired", "this capture is too close to its retention deadline")
    effective_ttl = min(int(ttl_seconds), remaining_ttl)
    url = await store.signed_url(artifact.storage_key, expires_in=effective_ttl)
    artifact.last_accessed_by = str(user_id or "")
    artifact.last_accessed_at = moment
    artifact.access_count = int(artifact.access_count or 0) + 1
    await session.flush()
    return {
        "url": url,
        "expires_in": effective_ttl,
        "artifact_expires_at": expires_at.isoformat(),
    }


async def purge_expired(
    session,
    *,
    tenant_id: uuid.UUID,
    store: CaptureStore,
    now: datetime | None = None,
) -> dict[str, int]:
    """Purge expired captures for one tenant only.

    A scheduler that services the whole installation must invoke this once per
    tenant; requiring the tenant scope here prevents a tenant-authorized route
    from accidentally deleting another tenant's objects.
    """
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    moment = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(CallDebugArtifact).where(
                CallDebugArtifact.tenant_id == tenant_id,
                CallDebugArtifact.purged_at.is_(None),
                CallDebugArtifact.expires_at <= moment,
            )
        )
    ).scalars().all()
    removed = 0
    for row in rows:
        try:
            await store.delete(row.storage_key)
        except CaptureError:
            continue
        row.purged_at = moment
        removed += 1
    await session.flush()
    return {"purged": removed, "examined": len(rows)}
