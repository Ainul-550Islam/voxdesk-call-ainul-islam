"""A malformed path or query value must never be a bare 5xx.

The Batch 03 harness caught an unhandled ``ExceptionGroup`` — surfacing as a
bare 500 with no correlation id — when a value of the wrong *type* landed in a
path parameter (a UUID where an ``int`` was declared). The cause was structural:
the request-id middleware sits outside ``ExceptionMiddleware``, so anything that
escaped another middleware's ``call_next`` skipped the handlers entirely.
Batch 06 hardens that middleware so escaped caller errors are unwrapped and
translated to a documented, request-id-carrying response, while genuine server
faults still propagate to the outer error middleware (and to TestClient).

These tests walk the live route table rather than a hand-written list, so a
route added tomorrow is covered without anyone remembering to add it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

BODYLESS_METHODS = frozenset({"GET", "HEAD", "DELETE"})

#: A value that is wrong for at least one declared scalar type in this codebase.
HOSTILE_VALUES = (
    uuid.uuid4().hex,  # a UUID string where an int is declared
    "not-a-uuid-at-all",
    "999999999999999999999999999999",
)

_DOCUMENTED_5XX = {501, 503}


def _fuzzable_routes() -> list[APIRoute]:
    from app.main import app

    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and "{" in route.path
        and (route.methods or set()) & BODYLESS_METHODS
        and not route.path.endswith("{full_path:path}")
    ]


def _materialise(path: str, value: str) -> str:
    """Substitute every path parameter with ``value``."""
    output = ""
    depth = 0
    for character in path:
        if character == "{":
            depth += 1
            if depth == 1:
                output += value
                continue
        elif character == "}":
            depth -= 1
            continue
        if depth == 0:
            output += character
    return output


def _assert_clean(status_code: int, body: str, headers, label: str) -> None:
    # 501/503 are only acceptable when they are the deliberate, machine-coded
    # "not implemented"/"dependency unavailable" answers, never a bare 500.
    if status_code in _DOCUMENTED_5XX:
        assert '"code"' in body, f"{label} returned an uncoded {status_code}: {body[:200]}"
        return
    assert status_code < 500, f"{label} returned {status_code}: {body[:200]}"
    # The correlation id must survive on every response: it is what makes a
    # customer's report actionable.
    assert headers.get("X-Request-ID") or '"request_id"' in body, (
        f"{label} has no request id: {body[:200]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", HOSTILE_VALUES)
async def test_no_route_returns_a_bare_5xx_for_a_wrongly_typed_path_parameter(value: str):
    from app.main import app

    routes = _fuzzable_routes()
    assert len(routes) > 300, f"only {len(routes)} fuzzable routes found"

    failures: list[str] = []
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for route in routes:
            method = sorted((route.methods or set()) & BODYLESS_METHODS)[0]
            path = _materialise(route.path, value)
            try:
                response = await client.request(
                    method, path, headers={"X-Request-ID": "fuzz-test"}
                )
            except Exception as exc:  # noqa: BLE001 - a raised exception is the finding
                failures.append(f"{method} {path} raised {type(exc).__name__}: {exc}")
                continue
            try:
                _assert_clean(
                    response.status_code, response.text, response.headers, f"{method} {path}"
                )
            except AssertionError as exc:
                failures.append(str(exc))

    assert failures == [], "\n".join(failures[:20])


@pytest.mark.asyncio
async def test_an_int_path_parameter_rejects_a_uuid_with_422_or_404():
    """The exact shape the harness reported: a UUID in an ``int`` slot."""
    from app.main import app

    typed = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and ":int}" in route.path
    ]
    # A vacuous pass here would hide the exact regression the harness reported.
    assert typed, "no `{param:int}` route exists to exercise"
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for route in typed:
            path = _materialise(route.path, str(uuid.uuid4()))
            response = await client.get(path, headers={"X-Request-ID": "int-param-fuzz"})
            assert response.status_code in {404, 422}, (
                path,
                response.status_code,
                response.text[:200],
            )
            assert response.headers.get("X-Request-ID")


@pytest.mark.asyncio
async def test_a_middleware_failure_becomes_a_json_error_with_a_request_id():
    """Prove the middleware translation directly, including an ExceptionGroup."""
    from app.core.errors import install_error_handling

    probe = FastAPI()
    install_error_handling(probe)

    @probe.middleware("http")
    async def _explode(request, call_next):
        raise RuntimeError("middleware blew up")

    @probe.get("/anything")
    async def anything():  # pragma: no cover - never reached
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=probe, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/anything")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"]
    assert response.headers["X-Request-ID"] == body["error"]["request_id"]


@pytest.mark.asyncio
async def test_a_bad_path_value_raised_in_a_dependency_is_a_4xx():
    """A ``ValueError`` from path parsing is the client's fault, not a 500."""
    from app.core.errors import install_error_handling

    probe = FastAPI()
    install_error_handling(probe)

    def _parse(value: str = "") -> int:
        return int(value)  # raises ValueError for a non-numeric value

    @probe.get("/parse/{value}")
    async def parse(value: str, parsed: int = Depends(_parse)):
        return {"parsed": parsed}

    async with AsyncClient(
        transport=ASGITransport(app=probe, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/parse/not-a-number")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_request_value"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
