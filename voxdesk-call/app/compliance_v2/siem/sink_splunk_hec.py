"""Splunk HTTP Event Collector sink.

Protocol as Splunk defines it:

* ``POST {base}/services/collector/event`` with ``Authorization: Splunk <token>``;
* the body is a sequence of newline-delimited JSON objects;
* when the request carries ``X-Splunk-Request-Channel``, HEC returns an
  ``ackId`` and the collector expects the client to confirm delivery through
  ``POST {base}/services/collector/ack`` with ``{"acks": [id]}``;
* a ``200`` response whose body says ``"code": 0`` is acceptance — any other
  code in a 200 body is a rejection that will not improve on retry.

The ack is not decoration: an unacknowledged batch may still be sitting in
HEC's queue, so treating a 200 with an ack id as delivered without asking is
exactly the "fake success" this batch must not produce.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.compliance_v2.siem.events import SiemEvent
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_AUTH,
    CATEGORY_MALFORMED,
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


def _collector_base(endpoint: str) -> str:
    base = (endpoint or "").strip().rstrip("/")
    if not base:
        raise SinkNotConfiguredError("Splunk HEC endpoint is empty")
    if "/services/collector" in base:
        return base.split("/services/collector")[0] + "/services/collector"
    return base + "/services/collector"


class SplunkHecSink(SiemSink):
    """Delivers audit events to a Splunk indexer over HEC."""

    kind = "splunk_hec"

    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        index: str = "",
        sourcetype: str = "voxdesk:audit",
        source: str = "voxdesk",
        channel: str = "voxdesk-audit",
        verify_tls: bool = True,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise SinkNotConfiguredError("Splunk HEC token is required")
        self._base = _collector_base(endpoint)
        self._token = token
        self._index = index.strip()
        self._sourcetype = sourcetype
        self._source = source
        self._channel = channel
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None
        self._verify = verify_tls
        self._timeout = timeout

    def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                timeout=self._timeout,
                verify=self._verify,
                follow_redirects=False,
            )
        return self._owned_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Splunk {self._token}",
            "Content-Type": "application/json",
            "X-Splunk-Request-Channel": self._channel,
        }

    def _payload(self, events: list[SiemEvent]) -> str:
        lines: list[str] = []
        for event in events:
            envelope: dict[str, Any] = {
                "event": event.as_dict(),
                "time": time.time(),
                "source": self._source,
                "sourcetype": self._sourcetype,
                "host": "voxdesk",
            }
            if self._index:
                envelope["index"] = self._index
            lines.append(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False))
        return "\n".join(lines)

    async def send_batch(self, events: list[SiemEvent]) -> SinkResult:
        if not events:
            return SinkResult.success(0)
        body = self._payload(events)
        try:
            response = await self._http().post(
                f"{self._base}/event", content=body.encode("utf-8"), headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            return SinkResult.retry(CATEGORY_TIMEOUT, f"Splunk HEC timed out: {exc}")
        except httpx.HTTPError as exc:
            return SinkResult.retry(CATEGORY_TRANSPORT, f"Splunk HEC transport error: {exc}")

        if response.status_code in (401, 403):
            return SinkResult.permanent(
                CATEGORY_AUTH,
                f"Splunk HEC rejected the token (HTTP {response.status_code})",
                detail={"status": response.status_code},
            )
        if response.status_code == 429:
            return SinkResult.retry(
                CATEGORY_RATE_LIMITED, "Splunk HEC rate limited the batch", detail={"status": 429}
            )
        if response.status_code >= 500:
            return SinkResult.retry(
                CATEGORY_SERVER,
                f"Splunk HEC server error (HTTP {response.status_code})",
                detail={"status": response.status_code},
            )
        if response.status_code >= 400:
            return SinkResult.permanent(
                CATEGORY_REJECTED,
                f"Splunk HEC rejected the batch (HTTP {response.status_code}): {response.text[:200]}",
                detail={"status": response.status_code},
            )

        parsed = self._parse_body(response)
        code = parsed.get("code")
        if code not in (0, None):
            return SinkResult.permanent(
                CATEGORY_REJECTED,
                f"Splunk HEC refused the batch: {parsed.get('text', 'unknown reason')}",
                detail={"hec_code": code},
            )

        ack_id = parsed.get("ackId")
        if ack_id is None:
            return SinkResult.success(len(events), detail={"ack": "not_requested"})

        acknowledged = await self._wait_for_ack(int(ack_id))
        if not acknowledged:
            return SinkResult.retry(
                CATEGORY_TRANSPORT,
                f"Splunk HEC did not acknowledge channel batch {ack_id}",
                detail={"ack_id": ack_id},
            )
        return SinkResult.success(len(events), detail={"ack_id": ack_id, "ack": "confirmed"})

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = response.json()
        except ValueError:
            return {"code": CATEGORY_MALFORMED, "text": "non-JSON response body"}
        return parsed if isinstance(parsed, dict) else {"code": CATEGORY_MALFORMED}

    async def _wait_for_ack(self, ack_id: int, *, attempts: int = 5) -> bool:
        """Poll HEC's ack endpoint until it confirms, or the attempts run out."""
        import asyncio

        for index in range(attempts):
            try:
                response = await self._http().post(
                    f"{self._base}/ack",
                    content=json.dumps({"acks": [ack_id]}).encode("utf-8"),
                    headers={**self._headers(), "Content-Type": "application/json"},
                )
            except httpx.HTTPError:
                await asyncio.sleep(min(0.05 * (2**index), 1.0))
                continue
            if response.status_code != 200:
                await asyncio.sleep(min(0.05 * (2**index), 1.0))
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            acks = payload.get("acks") if isinstance(payload, dict) else None
            if isinstance(acks, dict) and acks.get(str(ack_id)) is True:
                return True
            await asyncio.sleep(min(0.05 * (2**index), 1.0))
        return False

    async def health(self) -> SinkHealth:
        """HEC's health endpoint answers without accepting an event."""
        import datetime as dt

        checked = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            response = await self._http().get(f"{self._base}/health", headers=self._headers())
        except httpx.HTTPError as exc:
            return SinkHealth(ok=False, detail=f"transport error: {exc}", checked_at=checked)
        ok = response.status_code == 200
        detail = response.text[:200] if not ok else "healthy"
        return SinkHealth(ok=ok, detail=detail, checked_at=checked)

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


__all__ = ["SplunkHecSink"]
