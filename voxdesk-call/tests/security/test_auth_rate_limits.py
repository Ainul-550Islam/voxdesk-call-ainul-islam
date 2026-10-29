"""Rate limits on the authentication endpoints.

An unauthenticated endpoint that is not throttled is an offline password oracle:
the only thing standing between a leaked email list and a tenant's account is how
many guesses a single source can make per minute. These tests drive the real
router with the account lookup stubbed, so they prove the limit is *reached*
rather than merely configured.

Asserted here:

* ``POST /auth/login`` throttles per source IP *before* any account lookup, so
  credential stuffing across many accounts from one address is also throttled;
* the throttle returns 429 with ``Retry-After`` and the structured body
  ``{"code": "rate_limited", ...}``, and the account lookup is never reached once
  the budget is spent;
* the budget is per source: a second address still gets its own allowance;
* the MFA challenge endpoint refuses a locked challenge with 429 +
  ``Retry-After`` rather than a 401 an attacker could brute-force through;
* the limit is read from settings, so an operator can lower it without a release.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.api import auth_routes
from app.auth import service
from app.core.config import settings
from app.core.rate_limit import reset_identity_limiter

LIMIT = 3


@pytest.fixture(autouse=True)
def in_process_limiter(monkeypatch) -> None:
    """Use the in-process sliding window; no Redis in the unit suite."""
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "login_ip_limit_per_minute", LIMIT)
    reset_identity_limiter()
    yield
    reset_identity_limiter()


@pytest.fixture
def auth_app(monkeypatch):
    """The real auth router with the database lookup replaced by a counter.

    ``_client_ip`` is also replaced, and this is the one substitution that needs
    explaining: in production the source address comes from the ASGI peer after
    Uvicorn's ``FORWARDED_ALLOW_IPS`` trust boundary has rewritten it, and the
    in-process ASGI transport has no peer to vary. The production code path
    (``app.core.rate_limit.client_ip``) is covered by its own tests; what this
    suite needs is a way to present two different sources, so the header is read
    here and documented as a test seam.
    """
    calls = {"authenticate": 0, "emails": []}

    async def _authenticate(*_args, **kwargs):
        calls["authenticate"] += 1
        calls["emails"].append(kwargs.get("email"))
        raise service.AuthError("Invalid email or password.")

    def _ip(request) -> str:
        return request.headers.get("X-Test-Client-IP", "testclient")[:128]

    monkeypatch.setattr(service, "authenticate", _authenticate)
    monkeypatch.setattr(auth_routes, "_client_ip", _ip)

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.state.calls = calls
    return app


@pytest.fixture
async def auth_client(auth_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_app), base_url="http://test"
    ) as client:
        yield client


def _login(client: httpx.AsyncClient, email: str, *, ip: str = "203.0.113.10"):
    return client.post(
        "/auth/login",
        json={"email": email, "password": "Wrong-Password-1!"},
        headers={"X-Test-Client-IP": ip},
    )


@pytest.mark.anyio
async def test_the_login_endpoint_throttles_per_source(auth_client, auth_app) -> None:
    codes = []
    for index in range(LIMIT + 2):
        response = await _login(auth_client, f"victim{index}@example.com")
        codes.append(response.status_code)

    assert codes[:LIMIT] == [401] * LIMIT, (
        f"the first {LIMIT} attempts should fail authentication, not be throttled: {codes}"
    )
    assert 429 in codes[LIMIT:], (
        f"no 429 after {LIMIT} attempts from one source: {codes}. The endpoint is "
        "an unthrottled password oracle."
    )


@pytest.mark.anyio
async def test_the_limit_is_enforced_before_the_account_is_looked_up(
    auth_client, auth_app
) -> None:
    for index in range(LIMIT):
        await _login(auth_client, f"before{index}@example.com")
    lookups_after_budget = auth_app.state.calls["authenticate"]

    throttled = await _login(auth_client, "after@example.com")
    assert throttled.status_code == 429
    assert auth_app.state.calls["authenticate"] == lookups_after_budget, (
        "the throttled request still reached the account lookup, so the limit is "
        "not protecting the credential store"
    )


@pytest.mark.anyio
async def test_the_throttle_carries_retry_after_and_a_structured_body(auth_client) -> None:
    for index in range(LIMIT):
        await _login(auth_client, f"headers{index}@example.com")

    response = await _login(auth_client, "headers@example.com")
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "60", (
        "a 429 without Retry-After gives the client no idea when to come back"
    )
    body = response.json()
    detail = body.get("detail", body)
    assert detail.get("code") == "rate_limited", f"unexpected body: {body}"


@pytest.mark.anyio
async def test_the_budget_is_per_source_address(auth_client) -> None:
    for index in range(LIMIT):
        await _login(auth_client, f"srca{index}@example.com", ip="203.0.113.10")

    # A different source has its own budget: throttling one attacker must not
    # lock out every user behind the same NAT.
    response = await _login(auth_client, "other@example.com", ip="198.51.100.20")
    assert response.status_code == 401, (
        f"a second source was throttled by the first source's budget: {response.status_code}"
    )


@pytest.mark.anyio
async def test_the_limit_follows_the_configured_value(auth_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "login_ip_limit_per_minute", 1)
    reset_identity_limiter()

    assert (await _login(auth_client, "one@example.com", ip="203.0.113.77")).status_code == 401
    assert (await _login(auth_client, "two@example.com", ip="203.0.113.77")).status_code == 429


def test_a_locked_mfa_challenge_is_refused_with_429_and_retry_after() -> None:
    """The refusal is a wiring assertion: the handler and the exception it maps.

    Exercising it end to end needs a persisted challenge in a locked state and a
    database round trip, which is what the identity suite does. What this guards
    is the mapping itself — a locked challenge must be a 429 with Retry-After, not
    a 401 that reads as "wrong code, try again".
    """
    import inspect

    from app.api import auth_routes as module
    from app.auth.identity import exceptions as mfa_identity

    assert hasattr(mfa_identity, "MFAChallengeLocked"), (
        "MFAChallengeLocked no longer exists; a locked challenge would fall "
        "through to the generic 401 path"
    )

    source = inspect.getsource(module)
    assert "MFAChallengeLocked" in source
    locked_block = source.split("except mfa_identity.MFAChallengeLocked", 1)[1]
    assert "429" in locked_block, (
        "a locked MFA challenge no longer returns 429; without it an attacker can "
        "brute-force a code through an endpoint that only ever says 401"
    )
    assert "Retry-After" in locked_block
