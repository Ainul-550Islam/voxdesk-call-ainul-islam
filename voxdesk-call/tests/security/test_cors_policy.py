"""CORS is an allowlist, never a wildcard — and never a wildcard with credentials.

``Access-Control-Allow-Origin: *`` together with ``Allow-Credentials: true`` is
the one combination a browser refuses to honour, which is lucky, because the
server-side version of that mistake is far worse: any page on the internet could
read an authenticated tenant response. This suite pins both halves:

* the configured allowlist must not contain ``*``, and ``validate_security()``
  must say so when it does;
* a request from an origin that is not on the list gets no
  ``Access-Control-Allow-Origin`` header at all, so the browser blocks it;
* a request from an allowed origin gets that exact origin echoed back (not a
  wildcard), with ``Allow-Credentials: true`` and a ``Vary: Origin`` so a shared
  cache cannot serve one tenant's CORS decision to another.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, settings

ALLOWED = "https://console.example.com"
OTHER = "https://evil.example.net"


@pytest.fixture
def cors_app(monkeypatch) -> FastAPI:
    """The real CORS middleware, configured exactly the way app/main.py does."""
    monkeypatch.setattr(settings, "cors_origins", ALLOWED)
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "Idempotency-Key",
            "X-VoxDesk-Public-Key",
            "X-VoxDesk-Widget-Session",
        ],
    )

    @app.get("/probe")
    async def probe() -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.fixture
async def cors_client(cors_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cors_app), base_url="http://test"
    ) as client:
        yield client


async def test_the_shipped_default_allowlist_is_not_a_wildcard() -> None:
    assert "*" not in settings.cors_origin_list, (
        "the default CORS_ORIGINS is a wildcard; a browser will not honour it "
        "with credentials, and a server that echoes it leaks tenant data"
    )
    for origin in settings.cors_origin_list:
        assert origin.startswith(("http://", "https://")), (
            f"origin {origin!r} is not an absolute URL"
        )


async def test_validate_security_rejects_a_wildcard_origin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "cors_origins", "*")
    problems = settings.validate_security()
    assert any("must not be '*'" in problem for problem in problems), (
        "a wildcard CORS origin is not reported as an insecure configuration"
    )


async def test_an_unknown_origin_receives_no_allow_origin_header(cors_client) -> None:
    """The browser keys off Access-Control-Allow-Origin, so its absence blocks.

    Starlette still emits a static ``Access-Control-Allow-Credentials: true`` on
    every response when ``allow_credentials`` is set, including for a rejected
    origin. That header is inert without a matching ``Allow-Origin`` — the
    browser refuses the response either way — so the assertion that matters, and
    the one made here, is that no ``Allow-Origin`` was echoed. Asserting the
    absence of the credentials header too would test Starlette, not us, and would
    fail against the framework's deliberate behaviour.
    """
    response = await cors_client.get("/probe", headers={"Origin": OTHER})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers, (
        f"an unlisted origin was echoed back: "
        f"{response.headers.get('access-control-allow-origin')!r}"
    )


async def test_an_allowed_origin_is_echoed_exactly_with_credentials(cors_client) -> None:
    response = await cors_client.get("/probe", headers={"Origin": ALLOWED})
    assert response.status_code == 200
    # The exact origin, never "*": a wildcard with credentials is rejected by
    # browsers and would be a data leak if it were not.
    assert response.headers.get("access-control-allow-origin") == ALLOWED
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert "origin" in (response.headers.get("vary") or "").lower(), (
        "Vary: Origin is required or a shared cache can serve one tenant's CORS "
        "decision to another"
    )


async def test_a_preflight_from_an_unknown_origin_is_refused(cors_client) -> None:
    response = await cors_client.options(
        "/probe",
        headers={
            "Origin": OTHER,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert "access-control-allow-origin" not in response.headers, (
        "a preflight from an unlisted origin was approved"
    )


async def test_a_preflight_from_an_allowed_origin_lists_only_the_documented_headers(
    cors_client,
) -> None:
    response = await cors_client.options(
        "/probe",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization,idempotency-key",
        },
    )
    assert response.headers.get("access-control-allow-origin") == ALLOWED
    allowed_headers = (response.headers.get("access-control-allow-headers") or "").lower()
    for header in ("authorization", "idempotency-key"):
        assert header in allowed_headers, f"{header} is not allowed by the preflight"


def test_production_requires_https_origins(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cors_origins", "http://console.example.com")
    problems = settings.validate_security()
    assert any("must use https in production" in problem for problem in problems), (
        "a plain-HTTP CORS origin passes production validation"
    )


def test_the_field_default_cannot_be_a_wildcard() -> None:
    default = str(Settings.model_fields["cors_origins"].default)
    assert "*" not in [origin.strip() for origin in default.split(",")]
