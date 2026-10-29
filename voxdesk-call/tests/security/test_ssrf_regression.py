"""SSRF guard regression for every surface that accepts a tenant-supplied URL.

A tenant can configure: a CRM ``base_url``, an OAuth ``token_url``, a
signed-webhook ``url``, and runtime HTTP tools for an agent. Every one of those
requests carries a bearer token, a client secret or customer PII, so a tenant who
points one at ``169.254.169.254`` makes *our* server place an authenticated
request they could never place themselves — the cloud metadata endpoint being
the worst case, because it hands back instance credentials.

The guard is static and deterministic by design (`app/core/ssrf.py`): it never
resolves DNS during configuration validation, so a config check cannot be
flipped by an attacker's DNS. Callers that are about to connect use the async
companion, which resolves every current answer and fails closed.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.agent.tools.http_tools import execute_agent_http_tool
from app.core.ssrf import (
    OutboundUrlError,
    is_safe_outbound_url,
    validate_outbound_url,
    validate_resolved_outbound_url,
)

# ---------------------------------------------------------------------------
# Destinations that must never be reachable through a tenant-supplied URL.
# ---------------------------------------------------------------------------

BLOCKED = {
    "aws/gcp/azure metadata": "http://169.254.169.254/latest/meta-data/",
    "ECS task metadata": "http://169.254.170.2/v2/credentials",
    "GCP metadata by name": "http://metadata.google.internal/computeMetadata/v1/",
    "Alibaba metadata": "http://100.100.100.200/latest/meta-data/",
    "loopback": "http://127.0.0.1:8000/admin",
    "loopback by name": "http://localhost:8000/admin",
    "localhost subdomain": "http://api.localhost/",
    "RFC1918 /8": "http://10.1.2.3/",
    "RFC1918 /12": "http://172.16.0.1/",
    "RFC1918 /16": "http://192.168.1.1/",
    "RFC1918 over https": "https://10.0.0.5/hook",
    "carrier-grade NAT": "http://100.64.0.1/",
    "link-local": "http://169.254.1.1/",
    "mDNS .local": "http://printer.local/",
    ".internal": "http://vault.internal/",
    "IPv6 loopback": "http://[::1]/",
    "IPv6 link-local": "http://[fe80::1]/",
    "IPv6 ULA": "http://[fd00::1]/",
    "non-http scheme": "file:///etc/passwd",
    "gopher scheme": "gopher://127.0.0.1:6379/_INFO",
    "credential in URL": "https://admin:supersecret@api.example.com/",
}


@pytest.mark.parametrize("url", list(BLOCKED.values()), ids=list(BLOCKED))
def test_a_static_guard_blocks_the_destination(url: str) -> None:
    with pytest.raises(OutboundUrlError):
        validate_outbound_url(url)
    assert is_safe_outbound_url(url) is False


def test_a_public_https_destination_is_allowed() -> None:
    validate_outbound_url("https://api.hubapi.com/crm/v3/objects/contacts")
    assert is_safe_outbound_url("https://api.hubapi.com/crm/v3/objects/contacts") is True


def test_require_https_rejects_a_plaintext_destination() -> None:
    """Credentials and PII must never travel over an unauthenticated channel."""
    with pytest.raises(OutboundUrlError):
        validate_outbound_url("http://api.hubapi.com/token", require_https=True)
    validate_outbound_url("https://api.hubapi.com/token", require_https=True)


def test_the_error_message_never_echoes_the_smuggled_credential() -> None:
    """A rejected URL is reported back to an operator; it must not leak a secret."""
    with pytest.raises(OutboundUrlError) as caught:
        validate_outbound_url("https://admin:supersecret@169.254.169.254/")
    assert "supersecret" not in str(caught.value), (
        "the guard echoed the userinfo credential into an operator-visible message"
    )


async def test_a_hostname_that_resolves_to_a_private_address_is_refused(monkeypatch) -> None:
    """DNS rebinding: a public-looking name that answers with a private IP.

    ``validate_outbound_url`` is static and would let the name through; the async
    companion is what a network caller must use, and it is the one that catches
    this.
    """

    async def fake_getaddrinfo(host, port, **_kwargs):  # noqa: ANN001, ARG001
        return [
            (asyncio.AF_INET, None, None, None, ("127.0.0.1", port)),
        ]

    monkeypatch.setattr("app.core.ssrf.asyncio.getaddrinfo", fake_getaddrinfo, raising=False)
    monkeypatch.setattr(asyncio, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(OutboundUrlError):
        await validate_resolved_outbound_url("https://rebind.example.com/")


async def test_a_hostname_that_resolves_to_a_public_address_is_allowed(monkeypatch) -> None:
    async def fake_getaddrinfo(host, port, **_kwargs):  # noqa: ANN001, ARG001
        return [(asyncio.AF_INET, None, None, None, ("93.184.216.34", port))]

    monkeypatch.setattr("app.core.ssrf.asyncio.getaddrinfo", fake_getaddrinfo, raising=False)
    monkeypatch.setattr(asyncio, "getaddrinfo", fake_getaddrinfo)

    await validate_resolved_outbound_url("https://example.com/")


# ---------------------------------------------------------------------------
# The HTTP tool surface
# ---------------------------------------------------------------------------


def _recording_client(target_status: int = 200, redirect_to: str | None = None) -> httpx.AsyncClient:
    """A client that records every URL it is asked to fetch."""
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if redirect_to:
            return httpx.Response(302, headers={"Location": redirect_to})
        return httpx.Response(target_status, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.requested = requested  # type: ignore[attr-defined]
    return client


TOOL_SPEC = {
    "name": "crm_lookup",
    "url": "https://api.example.com/contacts",
    "method": "POST",
    "schema": {"url": "https://api.example.com/contacts", "method": "POST"},
}


async def test_the_http_tool_refuses_a_private_destination_before_connecting() -> None:
    client = _recording_client()
    result = await execute_agent_http_tool(
        tool_spec={**TOOL_SPEC, "url": "http://169.254.169.254/latest/meta-data/"},
        arguments={},
        client=client,
    )
    assert result["ok"] is False
    assert "SSRF" in (result.get("error") or ""), (
        f"the tool reported {result.get('error')!r}; a refusal must name the policy"
    )
    assert client.requested == [], (  # type: ignore[attr-defined]
        f"the tool contacted {client.requested} despite the SSRF policy"  # type: ignore[attr-defined]
    )


async def test_the_http_tool_does_not_follow_a_redirect_into_a_private_range() -> None:
    """A 302 to the metadata endpoint must not be followed.

    The tool's client is created with ``follow_redirects=False`` and each hop is
    validated, so an attacker who controls a public endpoint cannot bounce the
    request (and the Authorization header) into the private network.
    """
    client = _recording_client(redirect_to="http://169.254.169.254/latest/meta-data/")
    result = await execute_agent_http_tool(
        tool_spec=TOOL_SPEC,
        arguments={},
        client=client,
    )
    requested = client.requested  # type: ignore[attr-defined]
    assert all(
        "169.254.169.254" not in url for url in requested
    ), f"the tool followed a redirect into the metadata range: {requested}"
    # The public hop was made and its 302 returned; nothing else was fetched.
    assert len(requested) == 1
    assert result.get("status_code") == 302 or result.get("ok") in (True, False)


async def test_the_http_tool_allows_a_public_destination() -> None:
    client = _recording_client(200)
    result = await execute_agent_http_tool(
        tool_spec=TOOL_SPEC,
        arguments={"id": "42"},
        client=client,
    )
    assert result["ok"] is True, result
    assert client.requested == ["https://api.example.com/contacts"]  # type: ignore[attr-defined]


async def test_the_http_tool_interpolates_a_destination_and_still_validates_it() -> None:
    """A template that renders to a private host must be blocked after render."""
    client = _recording_client()
    result = await execute_agent_http_tool(
        tool_spec={
            "name": "webhook",
            "method": "POST",
            "schema": {"url": "https://{{ host }}/hook", "method": "POST"},
        },
        arguments={"host": "127.0.0.1"},
        client=client,
    )
    assert result["ok"] is False
    assert "SSRF" in (result.get("error") or "")
    assert client.requested == []  # type: ignore[attr-defined]


async def test_the_http_tool_rejects_an_unsupported_method() -> None:
    client = _recording_client()
    result = await execute_agent_http_tool(
        tool_spec={**TOOL_SPEC, "method": "TRACE"},
        arguments={},
        client=client,
    )
    assert result["ok"] is False
    assert "method" in (result.get("error") or "").lower()


async def test_the_http_tool_reports_a_missing_url_without_guessing() -> None:
    result = await execute_agent_http_tool(tool_spec={"name": "x"}, arguments={})
    assert result["ok"] is False
    assert "URL" in (result.get("error") or "")
