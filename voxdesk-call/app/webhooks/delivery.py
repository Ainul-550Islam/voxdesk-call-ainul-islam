"""Outbound webhook HTTP delivery.

Redirects are not followed. The URL is checked with the existing SSRF guard
before any socket is opened. Logs contain status and ids, never the secret or
the customer body.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.logging import log
from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.webhooks.signing import sign

_MAX_BODY = 4096


@dataclass(frozen=True)
class HttpResult:
    kind: str
    status: int = 0
    category: str = ""


async def deliver(
    *,
    url: str,
    body: bytes,
    secret: str,
    event_id: str,
    timeout_seconds: float = 5.0,
    client: httpx.AsyncClient | None = None,
) -> HttpResult:
    try:
        validate_outbound_url(url, require_https=True)
    except OutboundUrlError:
        return HttpResult("permanent", category="invalid_endpoint")
    header = sign(secret, body)
    owns = client is None
    http = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    try:
        response = await http.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-VoxDesk-Signature": header,
                "X-VoxDesk-Event": event_id,
            },
        )
    except httpx.TimeoutException:
        log.info("webhook.delivery_timeout", event_id=event_id)
        return HttpResult("retryable", category="timeout")
    except httpx.TransportError:
        log.info("webhook.delivery_network", event_id=event_id)
        return HttpResult("retryable", category="network")
    finally:
        if owns:
            await http.aclose()
    if 300 <= response.status_code < 400:
        log.info("webhook.redirect_rejected", event_id=event_id, status=response.status_code)
        return HttpResult("permanent", response.status_code, "redirect_rejected")
    if response.status_code >= 500 or response.status_code in {408, 429}:
        return HttpResult("retryable", response.status_code, f"http_{response.status_code}")
    if response.status_code >= 400:
        return HttpResult("permanent", response.status_code, "provider_4xx")
    if len(response.content) > _MAX_BODY:
        log.info("webhook.response_truncated", event_id=event_id, status=response.status_code)
    log.info("webhook.delivered", event_id=event_id, status=response.status_code)
    return HttpResult("success", response.status_code, "ok")
