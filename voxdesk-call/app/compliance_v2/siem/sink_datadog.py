"""Datadog Logs intake (v2) sink.

The intake endpoint accepts a JSON array of log objects and enforces two hard
limits: at most **1000 logs** and **5 MB of uncompressed payload** per request.
Both are enforced here *before* the request is sent, because a rejected
oversized batch would otherwise be retried forever — the payload never gets
smaller.

The body is gzipped (``Content-Encoding: gzip``), which is what lets a batch of
1000 audit events travel in a few hundred kilobytes. Authentication is the
``DD-API-KEY`` header; the site selects the intake hostname.
"""

from __future__ import annotations

import gzip
import json
from typing import Any

import httpx

from app.compliance_v2.siem.events import SiemEvent
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_AUTH,
    CATEGORY_RATE_LIMITED,
    CATEGORY_REJECTED,
    CATEGORY_SERVER,
    CATEGORY_TIMEOUT,
    CATEGORY_TRANSPORT,
    SiemSink,
    SinkHealth,
    SinkNotConfiguredError,
    SinkResult,
)

#: Datadog's documented per-request limits.
MAX_EVENTS_PER_REQUEST = 1000
MAX_UNCOMPRESSED_BYTES = 5 * 1024 * 1024

_SITE_DOMAINS = {
    "us1": "datadoghq.com",
    "us3": "us3.datadoghq.com",
    "us5": "us5.datadoghq.com",
    "eu": "datadoghq.eu",
    "ap1": "ap1.datadoghq.com",
    "us2": "us2.datadoghq.com",
    "gov": "ddog-gov.com",
}


def intake_url(site: str, endpoint: str = "") -> str:
    """Resolve the intake hostname from a site key, or accept a full override."""
    if endpoint.strip():
        return endpoint.strip().rstrip("/")
    domain = _SITE_DOMAINS.get((site or "us1").strip().lower())
    if domain is None:
        raise SinkNotConfiguredError(f"unknown Datadog site {site!r}")
    return f"https://http-intake.logs.{domain}/api/v2/logs"


class DatadogLogsSink(SiemSink):
    """Delivers audit events to the Datadog log intake."""

    kind = "datadog_logs"

    def __init__(
        self,
        *,
        api_key: str,
        site: str = "us1",
        endpoint: str = "",
        service: str = "voxdesk",
        ddsource: str = "voxdesk-audit",
        tags: str = "",
        verify_tls: bool = True,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise SinkNotConfiguredError("Datadog API key is required")
        self._api_key = api_key
        self._url = intake_url(site, endpoint)
        self._service = service
        self._source = ddsource
        self._tags = tags
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None
        self._verify = verify_tls
        self._timeout = timeout

    def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=self._timeout, verify=self._verify, follow_redirects=False
            )
        return self._owned_client

    def _log_entry(self, event: SiemEvent) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ddsource": self._source,
            "service": self._service,
            "ddtags": self._tags,
            "message": event.canonical_json(),
            "hostname": "voxdesk",
        }
        if event.tenant_id:
            entry["ddtags"] = f"tenant_id:{event.tenant_id}" + (f",{self._tags}" if self._tags else "")
        return entry

    @staticmethod
    def _chunk(events: list[SiemEvent]) -> list[list[SiemEvent]]:
        """Split a batch so no request exceeds either documented limit."""
        chunks: list[list[SiemEvent]] = []
        current: list[SiemEvent] = []
        current_bytes = 2  # the enclosing "[]"
        for event in events:
            size = len(event.canonical_json().encode("utf-8")) + 1
            if size > MAX_UNCOMPRESSED_BYTES:
                # A single event larger than the request limit cannot be sent
                # at all; carrying it alone lets the caller dead-letter it.
                if current:
                    chunks.append(current)
                    current, current_bytes = [], 2
                chunks.append([event])
                continue
            if len(current) >= MAX_EVENTS_PER_REQUEST or current_bytes + size > MAX_UNCOMPRESSED_BYTES:
                if current:
                    chunks.append(current)
                current, current_bytes = [], 2
            current.append(event)
            current_bytes += size
        if current:
            chunks.append(current)
        return chunks

    async def send_batch(self, events: list[SiemEvent]) -> SinkResult:
        if not events:
            return SinkResult.success(0)
        accepted = 0
        details: dict[str, Any] = {"requests": 0}
        for chunk in self._chunk(events):
            body = json.dumps([self._log_entry(event) for event in chunk], ensure_ascii=False)
            raw = body.encode("utf-8")
            if len(raw) > MAX_UNCOMPRESSED_BYTES:
                return SinkResult.permanent(
                    CATEGORY_REJECTED,
                    "a single audit event exceeds Datadog's 5MB intake limit",
                    accepted=accepted,
                    permanent_event_ids=[event.event_id for event in chunk],
                )
            compressed = gzip.compress(raw)
            try:
                response = await self._http().post(
                    self._url,
                    content=compressed,
                    headers={
                        "DD-API-KEY": self._api_key,
                        "Content-Type": "application/json",
                        "Content-Encoding": "gzip",
                    },
                )
            except httpx.TimeoutException as exc:
                return SinkResult.retry(CATEGORY_TIMEOUT, f"Datadog intake timed out: {exc}")
            except httpx.HTTPError as exc:
                return SinkResult.retry(CATEGORY_TRANSPORT, f"Datadog transport error: {exc}")

            details["requests"] += 1
            details["last_status"] = response.status_code
            if response.status_code in (401, 403):
                return SinkResult.permanent(
                    CATEGORY_AUTH,
                    f"Datadog rejected the API key (HTTP {response.status_code})",
                    accepted=accepted,
                    detail=details,
                )
            if response.status_code == 429:
                return SinkResult.retry(
                    CATEGORY_RATE_LIMITED, "Datadog rate limited the batch", detail=details
                )
            if response.status_code >= 500:
                return SinkResult.retry(
                    CATEGORY_SERVER,
                    f"Datadog server error (HTTP {response.status_code})",
                    detail=details,
                )
            if response.status_code >= 400:
                return SinkResult.permanent(
                    CATEGORY_REJECTED,
                    f"Datadog rejected the batch (HTTP {response.status_code}): {response.text[:200]}",
                    accepted=accepted,
                    detail=details,
                )
            accepted += len(chunk)
        return SinkResult.success(accepted, detail=details)

    async def health(self) -> SinkHealth:
        """Validate the API key against the logs configuration endpoint."""
        import datetime as dt

        checked = dt.datetime.now(dt.timezone.utc).isoformat()
        domain = self._url.split("/api/")[0]
        try:
            response = await self._http().get(
                f"{domain}/api/v1/validate", headers={"DD-API-KEY": self._api_key}
            )
        except httpx.HTTPError as exc:
            return SinkHealth(ok=False, detail=f"transport error: {exc}", checked_at=checked)
        ok = response.status_code == 200
        return SinkHealth(
            ok=ok,
            detail="valid" if ok else f"HTTP {response.status_code}: {response.text[:160]}",
            checked_at=checked,
        )

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


__all__ = [
    "MAX_EVENTS_PER_REQUEST",
    "MAX_UNCOMPRESSED_BYTES",
    "DatadogLogsSink",
    "intake_url",
]
