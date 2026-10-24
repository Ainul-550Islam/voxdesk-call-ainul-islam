"""Elasticsearch / OpenSearch ``_bulk`` sink.

``_bulk`` is one of the few APIs that answers ``200`` while having failed:
the envelope succeeds and individual items carry ``"status": 400`` or
``"error": {...}``. Parsing per item is therefore mandatory — a sink that
looks only at the envelope would report a full delivery for a batch a mapping
conflict silently dropped.

Per-item handling:

* ``status`` 2xx → accepted;
* ``status`` 429 / 5xx → retryable (the whole batch is retried; Elasticsearch
  de-duplicates by ``_id``, and the ``_id`` here is the audit row id);
* any other error → permanent for exactly that event.
"""

from __future__ import annotations

import base64
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


class ElasticBulkSink(SiemSink):
    """Delivers audit events into an Elasticsearch/OpenSearch index."""

    kind = "elastic_bulk"

    def __init__(
        self,
        *,
        endpoint: str,
        index: str,
        api_key: str = "",
        username: str = "",
        password: str = "",
        verify_tls: bool = True,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        base = (endpoint or "").strip().rstrip("/")
        if not base:
            raise SinkNotConfiguredError("Elastic endpoint is empty")
        if not (index or "").strip():
            raise SinkNotConfiguredError("Elastic index name is required")
        if not api_key and not (username and password):
            raise SinkNotConfiguredError(
                "Elastic requires an API key or a username/password pair"
            )
        self._base = base
        self._index = index.strip()
        self._api_key = api_key
        self._basic = (
            base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            if username and password
            else ""
        )
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

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/x-ndjson"}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        elif self._basic:
            headers["Authorization"] = f"Basic {self._basic}"
        return headers

    def _body(self, events: list[SiemEvent]) -> bytes:
        lines: list[str] = []
        for event in events:
            action = {"index": {"_index": self._index, "_id": event.event_id}}
            document = {"@timestamp": event.occurred_at, **event.as_dict()}
            lines.append(json.dumps(action, separators=(",", ":")))
            lines.append(json.dumps(document, separators=(",", ":"), ensure_ascii=False))
        return ("\n".join(lines) + "\n").encode("utf-8")

    async def send_batch(self, events: list[SiemEvent]) -> SinkResult:
        if not events:
            return SinkResult.success(0)
        try:
            response = await self._http().post(
                f"{self._base}/_bulk?refresh=false",
                content=self._body(events),
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            return SinkResult.retry(CATEGORY_TIMEOUT, f"Elastic _bulk timed out: {exc}")
        except httpx.HTTPError as exc:
            return SinkResult.retry(CATEGORY_TRANSPORT, f"Elastic transport error: {exc}")

        if response.status_code in (401, 403):
            return SinkResult.permanent(
                CATEGORY_AUTH,
                f"Elastic rejected the credential (HTTP {response.status_code})",
                detail={"status": response.status_code},
            )
        if response.status_code == 429:
            return SinkResult.retry(
                CATEGORY_RATE_LIMITED, "Elastic rate limited the bulk request", detail={"status": 429}
            )
        if response.status_code >= 500:
            return SinkResult.retry(
                CATEGORY_SERVER,
                f"Elastic server error (HTTP {response.status_code})",
                detail={"status": response.status_code},
            )
        if response.status_code >= 400:
            return SinkResult.permanent(
                CATEGORY_REJECTED,
                f"Elastic rejected the bulk request (HTTP {response.status_code})",
                detail={"status": response.status_code},
            )
        try:
            payload = response.json()
        except ValueError:
            return SinkResult.retry(
                CATEGORY_TRANSPORT, "Elastic returned a non-JSON bulk response"
            )
        return self.interpret_bulk(payload, events)

    @staticmethod
    def interpret_bulk(payload: dict[str, Any], events: list[SiemEvent]) -> SinkResult:
        """Turn a ``_bulk`` response into an acceptance/retry/permanent verdict."""
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != len(events):
            return SinkResult.retry(
                CATEGORY_TRANSPORT,
                "Elastic bulk response did not describe every submitted event",
                detail={"items": len(items) if isinstance(items, list) else 0},
            )

        accepted = 0
        retryable_ids: list[str] = []
        permanent_ids: list[str] = []
        for event, item in zip(events, items):
            operation = next(iter(item.values()), {}) if isinstance(item, dict) else {}
            status = int(operation.get("status", 0) or 0)
            if 200 <= status < 300:
                accepted += 1
            elif status in (429, 500, 502, 503, 504):
                retryable_ids.append(event.event_id)
            else:
                permanent_ids.append(event.event_id)

        if retryable_ids:
            return SinkResult.retry(
                CATEGORY_RATE_LIMITED if not payload.get("errors") else CATEGORY_SERVER,
                f"Elastic deferred {len(retryable_ids)} event(s) for retry",
                detail={
                    "accepted": accepted,
                    "retryable": len(retryable_ids),
                    "permanent": len(permanent_ids),
                },
            )
        if permanent_ids:
            return SinkResult.permanent(
                CATEGORY_REJECTED,
                f"Elastic rejected {len(permanent_ids)} event(s) permanently",
                accepted=accepted,
                permanent_event_ids=permanent_ids,
                detail={"accepted": accepted, "permanent": len(permanent_ids)},
            )
        return SinkResult.success(accepted, detail={"items": len(items)})

    async def health(self) -> SinkHealth:
        import datetime as dt

        checked = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            response = await self._http().get(f"{self._base}/_cluster/health", headers=self._headers())
        except httpx.HTTPError as exc:
            return SinkHealth(ok=False, detail=f"transport error: {exc}", checked_at=checked)
        if response.status_code != 200:
            return SinkHealth(
                ok=False, detail=f"HTTP {response.status_code}", checked_at=checked
            )
        try:
            cluster_status = response.json().get("status", "")
        except ValueError:
            return SinkHealth(ok=False, detail="non-JSON cluster health", checked_at=checked)
        # ``yellow`` is fully able to accept writes; ``red`` is not.
        return SinkHealth(ok=cluster_status in {"green", "yellow"}, detail=cluster_status, checked_at=checked)

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


__all__ = ["ElasticBulkSink"]
