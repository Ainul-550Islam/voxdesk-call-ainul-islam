"""Tests for SSRF blocking, vendor host allowlists, redirect blocking, 429 Retry-After, and circuit breaking."""

from __future__ import annotations

import socket

import httpx
import pytest

from app.integrations_v2.core.errors import (
    CircuitOpenError,
    ProviderRejected,
    RateLimited,
    SSRFBlocked,
    redact_secrets,
)
from app.integrations_v2.core.http_client import Int2HttpClient, validate_vendor_url
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://acme.zendesk.com/api/v2/users/me.json",
        "https://127.0.0.1/api/v2/users/me.json",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.12/api/now/table/incident",
        "https://localhost/api/v2/tickets.json",
        "https://evil.internal/api/v2/tickets.json",
        "https://attacker.example.com/api/v2/users/me.json",
        "https://admin:secret@acme.zendesk.com/api/v2/users/me.json",
    ],
)
def test_ssrf_and_vendor_allowlist_blocks_unsafe_destinations(bad_url: str):
    with pytest.raises(SSRFBlocked):
        validate_vendor_url(bad_url, provider="zendesk")


@pytest.mark.asyncio
async def test_dns_rebinding_to_private_ip_is_blocked(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    client = Int2HttpClient(
        timeout_s=2.0,
        max_retries=0,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True})),
        resolve_dns=True,
    )
    with pytest.raises(SSRFBlocked):
        await client.request(
            "GET",
            "https://acme.zendesk.com/api/v2/users/me.json",
            provider="zendesk",
        )


@pytest.mark.asyncio
async def test_redirect_response_is_blocked_to_prevent_credential_leak():
    client = make_protocol_http_client(
        lambda _r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    with pytest.raises(SSRFBlocked):
        await client.request(
            "GET",
            "https://acme.zendesk.com/api/v2/users/me.json",
            provider="zendesk",
        )


@pytest.mark.asyncio
async def test_429_retry_after_honored_and_recovers():
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "rate_limit"})
        return httpx.Response(200, json={"ok": True})

    client = make_protocol_http_client(handler, max_retries=3)
    resp = await client.request("POST", "https://slack.com/api/auth.test", provider="slack")
    assert resp.status_code == 200
    assert calls == 3


@pytest.mark.asyncio
async def test_429_exhaustion_raises_rate_limited():
    client = make_protocol_http_client(
        lambda _r: httpx.Response(429, headers={"Retry-After": "2"}),
        max_retries=1,
    )
    with pytest.raises(RateLimited) as exc_info:
        await client.request("POST", "https://slack.com/api/auth.test", provider="slack")
    assert exc_info.value.retry_after_seconds == 2.0


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_five_consecutive_5xx_failures():
    client = make_protocol_http_client(
        lambda _r: httpx.Response(503, text="upstream down"),
        max_retries=0,
    )
    conn_id = "conn-cb-test"
    for _ in range(5):
        with pytest.raises(ProviderRejected):
            await client.request(
                "GET",
                "https://acme.freshdesk.com/api/v2/agents/me",
                provider="freshdesk",
                connection_id=conn_id,
            )

    with pytest.raises(CircuitOpenError):
        await client.request(
            "GET",
            "https://acme.freshdesk.com/api/v2/agents/me",
            provider="freshdesk",
            connection_id=conn_id,
        )


def test_secret_scrubber_strips_tokens_and_query_params():
    dirty = (
        'Error calling https://api.pipedrive.com/v1/users?api_token=supersecret999 '
        'with header Authorization: Bearer xoxb-1234567890-abcdef and {"client_secret": "topsecret"}'
    )
    clean = redact_secrets(dirty)
    assert "supersecret999" not in clean
    assert "xoxb-1234567890-abcdef" not in clean
    assert "topsecret" not in clean
    assert "[REDACTED]" in clean
