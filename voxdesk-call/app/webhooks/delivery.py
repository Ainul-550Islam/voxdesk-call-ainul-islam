"""Outbound webhook HTTP delivery.

Redirects are not followed. The URL is checked with the existing SSRF guard
before any socket is opened. Logs contain status and ids, never the secret or
the customer body.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from time import perf_counter

import httpx

from app.core.logging import log
from app.core.ssrf import OutboundUrlError, validate_outbound_url, validate_resolved_outbound_url
from app.webhooks.signing import sign
from app.webhooks.retry import classify_status

_MAX_BODY = 4096


@dataclass(frozen=True)
class HttpResult:
    kind: str
    status: int = 0
    category: str = ""
    latency_ms: float | None = None
    response_snippet: str = ""


def validate_headers(headers: dict[str, str] | None) -> None:
    protected = {
        "host", "content-length", "content-type", "connection", "transfer-encoding",
        "proxy-authorization", "proxy-connection", "te", "trailer", "upgrade",
        "x-voxdesk-signature", "x-voxdesk-timestamp", "x-voxdesk-event",
    }
    custom = headers or {}
    if len(custom) > 32 or any(
        not isinstance(key, str) or not isinstance(value, str)
        or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", key)
        or key.lower() in protected or len(value) > 4096
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
        for key, value in custom.items()
    ):
        raise ValueError("Invalid or protected webhook headers")


async def deliver(
    *,
    url: str,
    body: bytes,
    secret: str,
    event_id: str,
    timeout_seconds: float = 5.0,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> HttpResult:
    try:
        validate_outbound_url(url, require_https=True)
        if client is None:
            await validate_resolved_outbound_url(url, require_https=True)
    except OutboundUrlError:
        return HttpResult("permanent", category="invalid_endpoint")
    if not secret:
        return HttpResult("permanent", category="secret_unavailable")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        return HttpResult("permanent", category="invalid_timeout")
    try:
        validate_headers(headers)
    except ValueError:
        return HttpResult("permanent", category="invalid_headers")
    custom = headers or {}
    header = sign(secret, body)
    owns = client is None
    http = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    started = perf_counter()
    try:
        response = await http.post(
            url,
            content=body,
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={
                **custom,
                "Content-Type": "application/json",
                "X-VoxDesk-Signature": header,
                "X-VoxDesk-Timestamp": header.split(",", 1)[0].split("=", 1)[1],
                "X-VoxDesk-Event": event_id,
            },
        )
    except httpx.TimeoutException:
        log.info("webhook.delivery_timeout", event_id=event_id)
        return HttpResult("retryable", category="timeout", latency_ms=(perf_counter() - started) * 1000)
    except httpx.TransportError:
        log.info("webhook.delivery_network", event_id=event_id)
        return HttpResult("retryable", category="network", latency_ms=(perf_counter() - started) * 1000)
    finally:
        if owns:
            await http.aclose()
    diagnostics = {
        "latency_ms": (perf_counter() - started) * 1000,
        "response_snippet": response.content[:_MAX_BODY].decode("utf-8", errors="replace"),
    }
    if response.status_code < 200:
        return HttpResult("permanent", response.status_code, "unexpected_status", **diagnostics)
    if 300 <= response.status_code < 400:
        log.info("webhook.redirect_rejected", event_id=event_id, status=response.status_code)
        return HttpResult("permanent", response.status_code, "redirect_rejected", **diagnostics)
    if response.status_code >= 500 or response.status_code in {408, 429}:
        return HttpResult(classify_status(response.status_code), response.status_code, f"http_{response.status_code}", **diagnostics)
    if response.status_code >= 400:
        return HttpResult("permanent", response.status_code, "provider_4xx", **diagnostics)
    if len(response.content) > _MAX_BODY:
        log.info("webhook.response_truncated", event_id=event_id, status=response.status_code)
    log.info("webhook.delivered", event_id=event_id, status=response.status_code)
    return HttpResult("success", response.status_code, "ok", **diagnostics)


# ---------------------------------------------------------------------------
# PART 14 / P14-09 -- delivery after event selection.
#
# `deliver` above sends one body to one URL. This is the fan-out in front of it:
# it asks `event_selection` which endpoints want the event, and only then opens a
# socket. An endpoint whose selection excludes the event is not contacted at all
# -- the cheapest refused delivery is the one that never happens.
# ---------------------------------------------------------------------------


async def deliver_to_endpoints(
    *,
    event: str,
    payload: dict,
    body: bytes,
    secret_for,
    endpoints,
    event_id: str,
    timeout_seconds: float = 5.0,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Deliver one event to every subscribed endpoint.

    `secret_for(endpoint)` returns the signing secret, so this module never sees
    a secret store. `endpoints` are `app.webhooks.event_selection.Endpoint`
    values. One failing endpoint does not stop the others.
    """
    from app.webhooks.event_selection import select_endpoints

    results: list[dict] = []
    for endpoint in select_endpoints(endpoints, event, payload):
        try:
            result = await deliver(
                url=endpoint.url,
                body=body,
                secret=secret_for(endpoint),
                event_id=event_id,
                timeout_seconds=timeout_seconds,
                client=client,
            )
            results.append(
                {
                    "endpoint_id": endpoint.id,
                    "status": result.status,
                    "kind": result.kind,
                    "latency_ms": result.latency_ms,
                }
            )
        except (OutboundUrlError, ValueError) as exc:
            # A refused URL is a configuration problem, recorded per endpoint.
            results.append({"endpoint_id": endpoint.id, "status": 0, "kind": "refused", "error": str(exc)[:200]})
    return results
