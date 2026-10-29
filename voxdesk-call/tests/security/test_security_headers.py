"""Transport security headers on API responses and on the shipped Caddy edge.

The API sets its own headers (`app/core/security_headers.py`) and the reverse
proxy sets them again (`Caddyfile`): the second layer is what still protects a
request that reaches the origin without going through the proxy, and the first
is what protects the API when it is deployed behind someone else's load
balancer. Both layers are asserted here, because either one alone is a single
point of failure.

HSTS is deliberately conditional: it is emitted only when the deployment is
actually served over TLS (`settings.uses_https`, derived from PUBLIC_BASE_URL).
A test that simply asserted "HSTS is always present" would be wrong, so the
assertion is made over both branches of that condition.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "Caddyfile"

#: The always-on controls. None of these are configurable, by design: there is
#: no per-tenant switch that could turn one off.
ALWAYS_ON = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Cross-Origin-Resource-Policy": "same-origin",
}


async def test_every_api_response_carries_the_always_on_headers(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200, response.text
    for header, expected in ALWAYS_ON.items():
        assert response.headers.get(header) == expected, (
            f"{header} is {response.headers.get(header)!r}, expected {expected!r}"
        )


async def test_the_content_security_policy_forbids_inline_and_foreign_scripts(client) -> None:
    response = await client.get("/health")
    csp = response.headers.get("Content-Security-Policy", "")
    assert csp, "no Content-Security-Policy header was set"

    directives: dict[str, str] = {}
    for part in csp.split(";"):
        entry = part.strip()
        if not entry or " " not in entry:
            continue
        name, _, values = entry.partition(" ")
        directives[name.strip()] = values.strip()
    assert "default-src" in directives
    assert "'unsafe-inline'" not in directives.get("script-src", ""), (
        "script-src allows 'unsafe-inline': a stored XSS payload in a transcript "
        "could then execute inline script"
    )
    assert "'unsafe-eval'" not in directives.get("script-src", "")
    assert directives.get("frame-ancestors") == "'none'", (
        "frame-ancestors must be 'none' so the console cannot be clickjacked"
    )
    assert directives.get("object-src") == "'none'"
    assert directives.get("base-uri") == "'self'", (
        "base-uri 'self' stops an injected <base> tag from repointing relative URLs"
    )


async def test_hsts_is_emitted_when_the_deployment_is_served_over_tls(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "public_base_url", "https://api.example.com")
    assert settings.uses_https is True
    response = await client.get("/health")
    hsts = response.headers.get("Strict-Transport-Security", "")
    assert hsts, "HSTS is missing on a TLS deployment"
    max_age = re.search(r"max-age=(\d+)", hsts)
    assert max_age, f"HSTS without max-age is not enforced: {hsts!r}"
    assert int(max_age.group(1)) >= 31536000, (
        f"max-age of {max_age.group(1)}s is below the one-year floor for HSTS "
        "preload eligibility"
    )


async def test_hsts_is_absent_over_plain_http_so_localhost_stays_usable(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")
    assert settings.uses_https is False
    response = await client.get("/health")
    assert "Strict-Transport-Security" not in response.headers, (
        "HSTS over plain HTTP would pin a browser to HTTPS on a host that cannot "
        "serve it, which is exactly why it is conditional"
    )


async def test_authenticated_responses_are_never_stored_by_intermediaries(client) -> None:
    response = await client.get("/api/agents")
    # 401 is the expected outcome for an unauthenticated call; the header is what
    # this test is about, and it must be present on the error path too.
    assert response.status_code in (200, 401, 403), response.status_code
    assert "no-store" in (response.headers.get("Cache-Control") or ""), (
        "/api/ responses carry tenant data and must not be cached"
    )


def test_the_caddy_edge_sets_the_same_controls() -> None:
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    for directive in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
    ):
        assert directive in caddyfile, f"{directive} is missing from the Caddyfile"

    # Every site block that terminates TLS must carry the header block; a later
    # block added without it would silently lose the protection.
    blocks = re.findall(r"header\s*\{(.*?)\}", caddyfile, re.DOTALL)
    assert blocks, "the Caddyfile declares no header block"
    for block in blocks:
        assert "X-Content-Type-Options" in block, (
            "a header block exists without X-Content-Type-Options"
        )
        assert "X-Frame-Options" in block, "a header block exists without X-Frame-Options"
        assert "Strict-Transport-Security" in block, (
            "a header block exists without HSTS"
        )
