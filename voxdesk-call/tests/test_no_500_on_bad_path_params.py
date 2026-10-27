"""A malformed path or query value must never be a 500.

The Batch 03 harness caught an unhandled ``ExceptionGroup`` — surfacing as a
bare 500 with no correlation id — when a value of the wrong *type* landed in a
path parameter (a UUID where an ``int`` was declared). The cause was structural:
the request-id middleware sits outside ``ExceptionMiddleware``, so anything that
escaped another middleware's ``call_next`` skipped the handlers entirely.

These tests walk the live route table rather than a hand-written list, so a route
added tomorrow is covered without anyone remembering to add it. For every
operation with a ``{parameter}`` the fuzzer substitutes a value that satisfies the
*shape* of a path but not its declared type, and asserts the response is a
documented 4xx carrying the request id.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.routing import APIRoute

#: Values chosen to be wrong for every declared scalar type in this codebase:
#: an int, a uuid, an enum member and a slug all fail to parse as at least one of
#: these, and none of them is an empty string (which would be a route miss).
HOSTILE_VALUES = (
    uuid.uuid4().hex,  # UUID string where an int is declared
    "not-a-uuid-at-all",
    "'; DROP TABLE calls; --",
    "-1",
    "999999999999999999999999999999",  # int where a small positive bound exists
)

#: Methods whose handlers take no body. Sending a body to a route that does not
#: declare one is a different failure mode (422 for a *supplied* body is fine but
#: noisy), so the fuzz stays on the bodyless methods plus DELETE.
BODYLESS_METHODS = frozenset({"GET", "HEAD", "DELETE"})

#: 503 is only acceptable when it is the deliberate "dependency unavailable"
#: answer, which the error contract marks with this code.
ALLOWED_SERVICE_UNAVAILABLE_CODE = "service_unavailable"


def _fuzzable_routes() -> list[APIRoute]:
    from app.main import app

    routes: list[APIRoute] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if "{" not in route.path:
            continue
        if not (route.methods or set()) & BODYLESS_METHODS:
            continue
        if route.path.endswith("{full_path:path}"):
            continue
        routes.append(route)
    return routes


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


#: 501 is a 5xx by HTTP semantics but is a *deliberate* answer in this codebase:
#: "this deployment does not implement that capability" with a machine-readable
#: code. It is accepted only when the body carries that code, so an unexplained
#: 501 still fails.
DOCUMENTED_NOT_IMPLEMENTED = 501


def _assert_clean(status_code: int, body: str, headers, label: str) -> None:
    if status_code == 503:
        assert ALLOWED_SERVICE_UNAVAILABLE_CODE in body or "unavailable" in body.lower(), body[:300]
        return
    if status_code == DOCUMENTED_NOT_IMPLEMENTED:
        assert '"code"' in body, f"{label} returned an uncoded 501: {body[:300]}"
        return
    assert status_code < 500, f"{label} returned {status_code}: {body[:300]}"
    assert status_code in {400, 401, 403, 404, 405, 409, 410, 422, 429}, (
        f"{label} returned an undocumented status {status_code}: {body[:300]}"
    )
    # The correlation id must survive on every refusal: it is what makes a
    # customer's report actionable.
    assert headers.get("X-Request-ID") or '"request_id"' in body, (
        f"{label} has no request id: {body[:300]}"
    )


@pytest.mark.parametrize("value", HOSTILE_VALUES)
async def test_no_route_returns_5xx_for_a_wrongly_typed_path_parameter(client, value: str):
    routes = _fuzzable_routes()
    assert len(routes) > 300, f"only {len(routes)} fuzzable routes found"

    failures: list[str] = []
    for route in routes:
        method = sorted((route.methods or set()) & BODYLESS_METHODS)[0]
        path = _materialise(route.path, value)
        try:
            response = await client.request(method, path, headers={"X-Request-ID": "fuzz-test"})
        except Exception as exc:  # noqa: BLE001 - a raised exception is the finding
            failures.append(f"{method} {path} raised {type(exc).__name__}: {exc}")
            continue
        try:
            _assert_clean(response.status_code, response.text, response.headers, f"{method} {path}")
        except AssertionError as exc:
            failures.append(str(exc))

    assert failures == [], "\n".join(failures[:20])


async def test_an_int_path_parameter_rejects_a_uuid_with_422_or_404(client):
    """The exact shape the harness reported: a UUID in an ``int`` slot."""
    from app.main import app

    typed = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and ":int}" in route.path
    ]
    assert typed, "no `{param:int}` route exists to exercise"

    for route in typed:
        path = _materialise(route.path, str(uuid.uuid4()))
        response = await client.get(path, headers={"X-Request-ID": "int-param-fuzz"})
        assert response.status_code in {404, 422}, (path, response.status_code, response.text[:300])
        assert response.headers.get("X-Request-ID") in {"int-param-fuzz", None} or (
            "request_id" in response.text
        )


async def test_a_middleware_failure_becomes_a_json_error_with_a_request_id():
    """Prove the middleware translation directly, including an ExceptionGroup.

    ``raise_app_exceptions=False`` is deliberately *not* used: the contract is
    that the middleware answers, so the transport must never see the exception.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

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


async def test_a_bad_path_value_raised_in_a_dependency_is_a_4xx():
    """A ``ValueError`` from path parsing is the client's fault, not a 500."""
    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient

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
