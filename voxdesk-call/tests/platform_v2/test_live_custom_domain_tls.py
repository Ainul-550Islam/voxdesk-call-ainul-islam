"""Opt-in verification of a real custom domain against a real edge.

Nothing here runs by default. Each test needs its own environment variable and skips
with the missing list otherwise, so a normal ``pytest -m "not live"`` run is offline
and a CI job that *does* hold the credentials gets the real protocol checked:

* the ownership TXT is actually published in public DNS,
* the edge's on-demand TLS issues a certificate that covers the domain,
* ``/api/platform/tls/ask`` answers 200 for a live domain and refuses an unknown one,
* an unknown ``Host`` never resolves to a tenant.

The DNS and handshake checks need ``VOXDESK_LIVE_DOMAIN``; the edge checks need
``VOXDESK_LIVE_EDGE_URL`` (the edge that terminates the domain, e.g.
``https://edge.voxdesk.app``). Each test skips on its own missing variable rather than
borrowing a neighbour's, so a partial configuration cannot produce a false pass.
"""

from __future__ import annotations

import os
import socket
import ssl
from datetime import datetime, timezone

import httpx
import pytest

from app.platform_v2.whitelabel import dns as dns_mod

pytestmark = [pytest.mark.live, pytest.mark.timeout(60)]

DOMAIN_ENV = "VOXDESK_LIVE_DOMAIN"
EDGE_ENV = "VOXDESK_LIVE_EDGE_URL"
TOKEN_ENV = "VOXDESK_LIVE_OWNERSHIP_TOKEN"


def _require(*names: str) -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip("live custom-domain verification is opt-in; missing " + ", ".join(missing))
    return values


@pytest.fixture(scope="module")
def live_domain() -> str:
    domain = _require(DOMAIN_ENV)[DOMAIN_ENV].lower().rstrip(".")
    return domain


@pytest.fixture(scope="module")
def edge() -> str:
    return _require(EDGE_ENV)[EDGE_ENV].rstrip("/")


async def test_the_ownership_txt_record_is_published(live_domain: str):
    """The record ``POST /whitelabel/domains`` told the tenant to publish really exists."""
    record_name = f"_voxdesk-challenge.{live_domain}"
    resolution = await dns_mod.resolve_txt(record_name, timeout=5.0)
    values = list(resolution.values)
    assert values, f"no TXT published at {record_name}"
    expect = os.environ.get(TOKEN_ENV, "").strip()
    if expect:
        assert any(expect in value for value in values), (
            f"{record_name} does not carry the ownership token"
        )


async def test_public_dns_agrees_on_the_routing_target(live_domain: str):
    """Two independent lookups, so a single rotating resolver cannot fake agreement."""
    first = await dns_mod.resolve_cname(live_domain, timeout=5.0)
    second = await dns_mod.resolve_cname(live_domain, timeout=5.0)
    if not first.values:
        # Apex domains cannot hold a CNAME; the platform accepts an A record there.
        first = await dns_mod.resolve_a(live_domain, timeout=5.0)
        second = await dns_mod.resolve_a(live_domain, timeout=5.0)
    assert first.values, f"{live_domain} does not resolve at all"
    assert set(first.values) == set(second.values)


async def test_the_edge_serves_a_certificate_covering_the_domain(live_domain: str):
    """A real TLS handshake: the certificate must name the domain and not be expired."""
    context = ssl.create_default_context()
    with socket.create_connection((live_domain, 443), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=live_domain) as tls:
            certificate = tls.getpeercert()
    sans = {value for kind, value in certificate.get("subjectAltName", ()) if kind == "DNS"}
    assert live_domain in sans, f"certificate does not cover {live_domain}: {sorted(sans)}"
    not_after = datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=timezone.utc
    )
    assert not_after > datetime.now(timezone.utc), "certificate has expired"


async def test_tls_ask_allows_the_live_domain_and_refuses_an_unknown_one(live_domain: str, edge: str):
    async with httpx.AsyncClient(base_url=edge, timeout=20.0, follow_redirects=False) as http:
        allowed = await http.get("/api/platform/tls/ask", params={"domain": live_domain})
        assert allowed.status_code == 200, f"{allowed.status_code}: {allowed.text[:200]}"

        stranger = await http.get(
            "/api/platform/tls/ask", params={"domain": "not-a-tenant-of-ours.invalid"}
        )
    # On-demand issuance must never be handed out to a domain we have not verified;
    # 404 and the Caddy `ask` contract's 4xx both mean "do not issue".
    assert stranger.status_code in (403, 404), stranger.text


async def test_an_unknown_host_never_resolves_to_a_tenant(edge: str):
    async with httpx.AsyncClient(base_url=edge, timeout=20.0, follow_redirects=False) as http:
        response = await http.get(
            "/api/whitelabel/public/config",
            headers={"host": "definitely-not-a-tenant.invalid"},
        )
    assert response.status_code == 404, response.text
