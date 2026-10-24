"""S3 JSONL archive sink.

The archive is the last-resort copy of the audit trail, so it has to be usable
years later without VoxDesk:

* **gzip JSONL** — one audit event per line, no framing, decompressible by
  ``gunzip`` and readable by ``jq``;
* **partitioned** by ``{prefix}/{tenant}/{YYYY-MM-DD}/{HH}/`` so a retrieval
  request can name a narrow prefix instead of scanning a bucket;
* **SSE-KMS** on every object, because the archive holds the same facts the
  live audit table does;
* **object lock** optionally, which is what makes the archive tamper-evident
  at the storage layer rather than only at the application layer;
* **a manifest object per data object** — sha256, byte count, event count and
  time range — so a later integrity check does not need the original process
  to have survived.

The boto3 call is blocking, so it runs in a worker thread; the sink never runs
network I/O on the event loop. A client may be injected, which is how the tests
drive a real S3-compatible endpoint without any AWS account.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import hashlib
import json
import uuid
from typing import Any

from app.compliance_v2.siem.events import SiemEvent
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_AUTH,
    CATEGORY_SERVER,
    CATEGORY_TIMEOUT,
    CATEGORY_TRANSPORT,
    SiemSink,
    SinkHealth,
    SinkNotConfiguredError,
    SinkResult,
)

_OBJECT_LOCK_MODES = frozenset({"GOVERNANCE", "COMPLIANCE"})


def partition_key(
    tenant_id: str, moment: dt.datetime, *, prefix: str = "audit", region: str = ""
) -> str:
    """Build the object key for one batch.

    The key is deterministic given the batch's first event, which is what lets
    the manifest reference the data object by name without a second lookup.
    """
    stamp = moment.astimezone(dt.timezone.utc)
    segments = [prefix.strip("/") or "audit"]
    if region:
        segments.append(region.strip())
    segments.append(tenant_id or "platform")
    segments.append(stamp.strftime("%Y-%m-%d"))
    segments.append(stamp.strftime("%H"))
    return "/".join(segments)


class S3JsonlSink(SiemSink):
    """Archives audit events as gzip JSONL objects in S3."""

    kind = "s3_jsonl"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "audit",
        region: str = "",
        kms_key_id: str = "",
        object_lock_mode: str = "",
        object_lock_days: int = 0,
        endpoint_url: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        client: Any | None = None,
    ) -> None:
        if not (bucket or "").strip():
            raise SinkNotConfiguredError("S3 bucket is required")
        if object_lock_mode and object_lock_mode not in _OBJECT_LOCK_MODES:
            raise SinkNotConfiguredError(
                f"object lock mode must be one of {sorted(_OBJECT_LOCK_MODES)}"
            )
        if object_lock_mode and object_lock_days < 1:
            raise SinkNotConfiguredError("object lock requires a positive retention in days")
        self._bucket = bucket.strip()
        self._prefix = prefix
        self._region = region
        self._kms_key_id = kms_key_id
        self._lock_mode = object_lock_mode
        self._lock_days = int(object_lock_days)
        self._endpoint_url = endpoint_url
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client = client

    def _s3(self):
        if self._client is not None:
            return self._client
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=self._region or None,
            endpoint_url=self._endpoint_url or None,
            aws_access_key_id=self._access_key_id or None,
            aws_secret_access_key=self._secret_access_key or None,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                signature_version="s3v4",
                s3={"addressing_style": "path" if self._endpoint_url else "auto"},
            ),
        )

    def _extra_args(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self._kms_key_id:
            extra["ServerSideEncryption"] = "aws:kms"
            extra["SSEKMSKeyId"] = self._kms_key_id
        else:
            extra["ServerSideEncryption"] = "AES256"
        if self._lock_mode:
            extra["ObjectLockMode"] = self._lock_mode
            extra["ObjectLockRetainUntilDate"] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                days=self._lock_days
            )
        return extra

    def _build_object(self, events: list[SiemEvent]) -> tuple[str, bytes, dict[str, Any]]:
        first = events[0]
        moment = dt.datetime.fromisoformat(first.occurred_at.replace("Z", "+00:00"))
        key = f"{partition_key(first.tenant_id, moment, prefix=self._prefix, region=self._region)}/{uuid.uuid4().hex}.jsonl.gz"
        lines = "\n".join(event.canonical_json() for event in events) + "\n"
        compressed = gzip.compress(lines.encode("utf-8"), mtime=0)
        manifest = {
            "schema_version": "voxdesk.siem.s3.manifest.v1",
            "bucket": self._bucket,
            "object_key": key,
            "tenant_id": first.tenant_id,
            "event_count": len(events),
            "first_event_at": events[0].occurred_at,
            "last_event_at": events[-1].occurred_at,
            "sha256_uncompressed": hashlib.sha256(lines.encode("utf-8")).hexdigest(),
            "sha256_gzip": hashlib.sha256(compressed).hexdigest(),
            "bytes_gzip": len(compressed),
            "event_ids": [event.event_id for event in events],
        }
        return key, compressed, manifest

    @staticmethod
    def _put(client, **kwargs):
        return client.put_object(**kwargs)

    async def send_batch(self, events: list[SiemEvent]) -> SinkResult:
        if not events:
            return SinkResult.success(0)
        key, body, manifest = self._build_object(events)
        client = self._s3()
        extra = self._extra_args()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._put,
                    client,
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/gzip",
                    ContentEncoding="gzip",
                    Metadata={
                        "event-count": str(len(events)),
                        "sha256": manifest["sha256_gzip"],
                        "tenant-id": manifest["tenant_id"] or "platform",
                    },
                    **extra,
                ),
                timeout=60,
            )
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._put,
                    client,
                    Bucket=self._bucket,
                    Key=f"{key}.manifest.json",
                    Body=json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
                    ContentType="application/json",
                    **{k: v for k, v in extra.items() if not k.startswith("ObjectLock")},
                ),
                timeout=60,
            )
        except asyncio.TimeoutError:
            return SinkResult.retry(CATEGORY_TIMEOUT, "S3 PutObject timed out")
        except Exception as exc:  # noqa: BLE001 - botocore error classes vary by version
            return self._classify(exc)

        return SinkResult.success(
            len(events),
            detail={
                "object_key": key,
                "manifest_key": f"{key}.manifest.json",
                "bytes": len(body),
                "sha256": manifest["sha256_gzip"],
            },
        )

    @staticmethod
    def _classify(exc: Exception) -> SinkResult:
        """Map a botocore exception onto the retry/permanent taxonomy."""
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str((response.get("Error") or {}).get("Code", ""))
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0)
        else:
            status = 0
        if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "NoSuchBucket"}:
            return SinkResult.permanent(CATEGORY_AUTH, f"S3 refused the write: {code}")
        if status and 400 <= int(status) < 500 and int(status) not in (429,):
            return SinkResult.permanent(CATEGORY_AUTH, f"S3 rejected the write: {code or status}")
        if status and int(status) >= 500 or code in {"Throttling", "SlowDown", "RequestTimeout"}:
            return SinkResult.retry(CATEGORY_SERVER, f"S3 transient failure: {code or status}")
        return SinkResult.retry(CATEGORY_TRANSPORT, f"S3 write failed: {type(exc).__name__}: {exc}")

    async def health(self) -> SinkHealth:
        checked = dt.datetime.now(dt.timezone.utc).isoformat()
        client = self._s3()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(client.head_bucket, Bucket=self._bucket), timeout=15
            )
        except Exception as exc:  # noqa: BLE001 - head_bucket failures are heterogeneous
            return SinkHealth(ok=False, detail=f"{type(exc).__name__}: {exc}", checked_at=checked)
        return SinkHealth(ok=True, detail="bucket reachable", checked_at=checked)


__all__ = ["S3JsonlSink", "partition_key"]
