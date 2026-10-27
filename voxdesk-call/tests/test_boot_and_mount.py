"""Boot and mount contract for the Batch 06 repair pass.

Three things made the application unusable before this batch, and each of them is
invisible to a module-level import: ``import app.main`` raised before it could
mount anything, twenty-three route modules were never included, and the public
webhook families that *were* reachable were not on any reviewer's list. These
tests are the ones that fail when that happens again.

The import check runs in a **fresh interpreter** on purpose. Importing
``app.main`` inside the test process would inherit whatever the test session had
already imported, which is exactly how the missing ``Permission`` members stayed
hidden: every module compiled, and only the full import graph failed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute

ROOT = Path(__file__).resolve().parents[1]

#: Route modules that existed in the repository but were mounted nowhere. The
#: values are the prefixes their live routes must live under.
BATCH_ROUTE_MODULES: dict[str, tuple[str, ...]] = {
    "app.api.carrier_media_routes": ("/api/v1/carriers", "/api/v1/s2s"),
    "app.api.dialer_v2_routes": ("/api/cc2/dialer",),
    "app.api.softphone_routes": ("/api/cc2/softphone",),
    "app.api.agent_assist_routes": ("/api/cc2/assist",),
    "app.api.acd_routes": ("/api/cc2/acd",),
    "app.api.agent_states_v2_routes": ("/api/cc2/agent-states",),
    "app.api.supervisor_v2_routes": ("/api/cc2/supervisor",),
    "app.api.integrations_v2_routes": ("/api/v1/integrations",),
    "app.api.integrations_v2_webhooks": ("/webhooks/int2",),
    "app.api.helpdesk_routes": ("/api/v1/helpdesk",),
    "app.api.collab_routes": ("/api/v1/collab", "/webhooks/int2/slack"),
    # Agent-tools v2 serves both its own prefix and the frontend's
    # `/api/v1/integrations/v2/agent-tools` alias for the same operations.
    "app.api.agent_tools_v2_routes": ("/api/v1/agent-tools", "/api/v1/integrations/v2/agent-tools"),
    "app.api.compliance_v2.siem_routes": ("/compliance/siem",),
    "app.api.compliance_v2.audit_routes": ("/compliance/audit",),
    "app.api.compliance_v2.kms_routes": ("/compliance/kms",),
    "app.api.compliance_v2.residency_routes": ("/compliance/residency",),
    "app.api.compliance_v2.dsar_routes": ("/compliance/dsar",),
    "app.api.compliance_v2.hipaa_routes": ("/compliance/hipaa",),
    "app.api.compliance_v2.incident_routes": ("/compliance/incidents",),
    "app.api.compliance_v2.evidence_routes": ("/compliance/evidence",),
    "app.api.compliance_v2.access_review_routes": ("/compliance/access-reviews",),
    "app.api.compliance_v2.security_routes": ("/compliance/security",),
    # The realtime sockets live outside the REST prefix: a stream ticket is
    # minted under `/api/cc2/...` and the socket itself under `/ws/cc2/...`.
    "app.api.ws.assist_ws": ("/api/cc2/assist", "/ws/cc2/assist"),
    "app.api.ws.supervisor_ws": ("/api/cc2/supervisor", "/ws/cc2/supervisor"),
    "app.telephony.vonage_handler": ("/telephony/vonage",),
    "app.contact_center_v2.softphone.websocket": ("/api/cc2/softphone/signaling",),
}

#: Socket-only modules: they must be mounted, and they must not be in the HTTP
#: contract (a WebSocket is not an OpenAPI operation).
SOCKET_ONLY_MODULES = (
    "app.telephony.sip.sip_handler",
    "app.contact_center_v2.softphone.websocket",
)

#: Public endpoints that carry no session and therefore have to prove the caller
#: by signature. Each entry pairs the path family with the module that must
#: contain the verification call.
SIGNATURE_VERIFIED_PUBLIC_PATHS: dict[str, str] = {
    "/webhooks/int2/{provider}/{connection_id}": "app/api/integrations_v2_webhooks.py",
    "/webhooks/int2/slack/{connection_id}/commands": "app/api/integrations_v2_webhooks.py",
    "/webhooks/int2/slack/{connection_id}/slash": "app/api/collab_routes.py",
    "/telephony/vonage/events": "app/telephony/vonage_handler.py",
    "/telephony/vonage/answer": "app/telephony/vonage_handler.py",
}

VERIFICATION_MARKERS = (
    "verify",
    "signature",
    "signing_secret",
    "hmac",
    "constant_time",
)

#: Floor, not a target: the audit counted 218 HTTP operations plus three sockets
#: in these modules. A module that loses one endpoint should fail loudly, but the
#: number is allowed to grow.
MIN_BATCH_HTTP_OPERATIONS = 210


def _live_routes():
    from app.main import app

    return list(app.routes)


#: Variables that a *previous* test in the same session can leave behind and
#: that change how ``app.main`` imports (its settings object reads them at import
#: time). Scrubbing them is what makes "clean interpreter" mean clean application,
#: not "clean except for whatever this session happened to export".
_INHERITED_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


# A full suite keeps thousands of modules resident, so a fresh child import can
# be starved for minutes by memory pressure even though it takes ~20s alone.
@pytest.mark.timeout(1200)
def test_the_application_imports_in_a_clean_interpreter():
    """`import app.main` must succeed with nothing pre-imported."""
    probe = textwrap.dedent(
        """
        import json
        from fastapi.routing import APIRoute, APIWebSocketRoute
        import app.main as main

        routes = list(main.app.routes)
        print(json.dumps({
            "api": sum(1 for route in routes if isinstance(route, APIRoute)),
            "sockets": sum(1 for route in routes if isinstance(route, APIWebSocketRoute)),
        }))
        """
    )
    child_env = {name: os.environ[name] for name in _INHERITED_ENV_ALLOWLIST if name in os.environ}
    child_env["PYTHONPATH"] = str(ROOT)
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=1080,
        env=child_env,
    )
    elapsed = time.monotonic() - started
    assert completed.returncode == 0, (
        f"clean-interpreter import failed after {elapsed:.1f}s\n" + completed.stderr[-4000:]
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["api"] > 1000, payload
    assert payload["sockets"] >= 10, payload


def test_every_batch_route_module_serves_at_least_one_route():
    served: dict[str, int] = {}
    for route in _live_routes():
        module = str(getattr(getattr(route, "endpoint", None), "__module__", "") or "")
        served[module] = served.get(module, 0) + 1

    missing = [module for module in BATCH_ROUTE_MODULES if served.get(module, 0) == 0]
    assert missing == [], f"mounted nowhere: {missing}"

    for module in SOCKET_ONLY_MODULES:
        assert served.get(module, 0) >= 1, module


def test_every_batch_route_module_serves_under_its_documented_prefix():
    by_module: dict[str, list[str]] = {}
    for route in _live_routes():
        module = str(getattr(getattr(route, "endpoint", None), "__module__", "") or "")
        by_module.setdefault(module, []).append(str(getattr(route, "path", "")))

    for module, prefixes in BATCH_ROUTE_MODULES.items():
        paths = by_module.get(module, [])
        assert paths, module
        for path in paths:
            assert any(prefix in path for prefix in prefixes), f"{module} serves {path}"


def test_the_batch_adds_more_than_two_hundred_http_operations():
    operations = [
        route
        for route in _live_routes()
        if isinstance(route, APIRoute)
        and str(getattr(route.endpoint, "__module__", "")) in BATCH_ROUTE_MODULES
    ]
    assert len(operations) >= MIN_BATCH_HTTP_OPERATIONS, len(operations)


def test_the_aggregators_are_the_only_thing_main_py_has_to_know():
    """main.py must stay readable: one import + one include per package."""
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    for aggregator in (
        "app.api.carrier_router",
        "app.api.compliance_v2",
        "app.api.contact_center_v2_router",
        "app.api.integrations_v2_router",
    ):
        assert f"from {aggregator} import router as" in source, aggregator
    # And it must not have grown a second, direct include for each leaf module.
    for leaf in ("app.api.dialer_v2_routes", "app.api.helpdesk_routes", "app.api.compliance_v2.siem_routes"):
        assert leaf not in source, f"{leaf} should be composed by its aggregator, not by main.py"


def test_public_webhook_paths_are_mounted_and_signature_verified():
    paths = {str(getattr(route, "path", "")) for route in _live_routes()}

    for path, module in SIGNATURE_VERIFIED_PUBLIC_PATHS.items():
        assert path in paths, f"{path} is not mounted"
        source = (ROOT / module).read_text(encoding="utf-8").lower()
        assert any(marker in source for marker in VERIFICATION_MARKERS), (
            f"{module} mounts {path} but contains no verification call"
        )


def test_public_webhook_paths_are_on_the_reviewed_allowlist():
    """A public endpoint that nobody reviewed is the defect this pins down."""
    from security_harness import authz_matrix

    infos = authz_matrix.route_infos(__import__("app.main", fromlist=["app"]).app)
    unknown = authz_matrix.unknown_public_routes(infos)
    assert [info.path for info in unknown] == [], "unreviewed public routes"
    for path in SIGNATURE_VERIFIED_PUBLIC_PATHS:
        assert path in authz_matrix.PUBLIC_ROUTES, path


def test_no_route_is_registered_twice():
    """A duplicated path+method silently shadows the first handler."""
    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for route in _live_routes():
        if not isinstance(route, (APIRoute, APIWebSocketRoute)):
            continue
        path = str(getattr(route, "path", ""))
        methods = tuple(sorted(getattr(route, "methods", None) or ({"WS"} if isinstance(route, APIWebSocketRoute) else ())))
        for method in methods:
            key = (path, method)
            if key in seen:
                duplicates.append(f"{method} {path} ({seen[key]} and {route.endpoint!r})")
            seen[key] = repr(getattr(route, "endpoint", ""))
    assert duplicates == [], "\n".join(duplicates)


def test_residency_guard_is_installed():
    """Batch 03 shipped the guard; Batch 06 has to install it unconditionally.

    The guard is a no-op until a placement-bearing request names a region, so
    installing it in every deployment is safe and is what makes the Batch 03
    class reachable at all.
    """
    from app.main import app

    installed = {middleware.cls.__name__ for middleware in app.user_middleware}
    assert "ResidencyGuardMiddleware" in installed, installed


async def test_a_slack_slash_command_without_a_valid_signature_is_refused(client, db):
    """The public endpoint must refuse a body it cannot attribute to Slack."""
    from app.integrations_v2.core.connection_store import upsert_connection
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Boot Webhook Tenant")
    connection = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="slack",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "xoxb-boot-test"},
        webhook_secret="boot-test-signing-secret",
    )
    await db.commit()

    response = await client.post(
        f"/webhooks/int2/slack/{connection.id}/commands",
        content=b"token=x&command=%2Fvoxdesk&text=hello",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": "1",
            "X-Slack-Signature": "v0=deadbeef",
        },
    )
    assert response.status_code == 401, response.text
    assert "request_id" in response.text or response.headers.get("X-Request-ID")


async def test_a_vendor_webhook_without_a_valid_signature_is_refused(client, db):
    """Same rule for the generic inbound webhook family."""
    from app.integrations_v2.core.connection_store import upsert_connection
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Boot Vendor Tenant")
    connection = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="slack",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "xoxb-boot-test"},
        webhook_secret="boot-test-signing-secret",
    )
    await db.commit()

    response = await client.post(
        f"/webhooks/int2/slack/{connection.id}",
        content=b'{"type":"event_callback","event_id":"Ev-boot"}',
        headers={
            "content-type": "application/json",
            "X-Slack-Request-Timestamp": "1",
            "X-Slack-Signature": "v0=deadbeef",
        },
    )
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# Registration order
# ---------------------------------------------------------------------------
# Starlette resolves a request against the route table in *registration* order,
# so a parameterised path registered before a literal one silently swallows the
# literal. That is not a hypothetical: mounting this batch's aggregators pulled
# `GET /api/calls/{call_id}` ahead of the entire search/export router, and
# `GET /api/calls/search` answered 422 "not a valid UUID". The same shape hid
# `/api/calls/dnc`, `POST /api/calls/policies/retry`, three Salesforce metadata
# routes and `GET /api/v1/public/use-cases/health`.
_UUID_TEXT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _segment_matches(pattern_segment: str, literal_segment: str) -> bool:
    """Would the router match ``literal_segment`` against ``pattern_segment``?

    A converted parameter (``{call_id:uuid}``, ``{page:int}``) only matches a
    literal that satisfies its converter — which is why ``/api/webhooks/health``
    is *not* shadowed by ``/api/webhooks/{endpoint_id:uuid}`` even though it is
    registered after it. An unconverted parameter matches anything.
    """
    if not pattern_segment.startswith("{"):
        return pattern_segment == literal_segment
    if ":" in pattern_segment:
        converter = pattern_segment.split(":", 1)[1].rstrip("}")
        if converter == "uuid":
            return bool(_UUID_TEXT.match(literal_segment))
        if converter == "int":
            return literal_segment.isdigit()
        if converter == "path":
            return True
    return True


def _would_shadow(pattern_path: str, literal_path: str) -> bool:
    pattern = pattern_path.split("/")
    literal = literal_path.split("/")
    if len(pattern) != len(literal):
        return False
    if not any(part.startswith("{") for part in pattern):
        return False
    return all(
        _segment_matches(pattern_part, literal_part)
        for pattern_part, literal_part in zip(pattern, literal)
    )


def test_no_literal_route_is_shadowed_by_an_earlier_parametric_route():
    """A literal path must never be answered by a wildcard registered earlier."""
    routes = [
        route
        for route in _live_routes()
        if isinstance(route, (APIRoute, APIWebSocketRoute))
    ]
    shadowed: list[str] = []
    for index, route in enumerate(routes):
        path = str(route.path)
        if "{" in path:
            continue
        for earlier in routes[:index]:
            earlier_path = str(earlier.path)
            if "{" not in earlier_path:
                continue
            if not _would_shadow(earlier_path, path):
                continue
            methods = set(getattr(earlier, "methods", None) or {"WS"})
            methods &= set(getattr(route, "methods", None) or {"WS"})
            if methods:
                shadowed.append(
                    f"{sorted(methods)} {path} is answered by the earlier "
                    f"{earlier_path} ({earlier.endpoint.__module__})"
                )
    assert shadowed == [], "\n".join(shadowed)


def test_the_call_search_and_dnc_paths_are_not_parsed_as_call_ids():
    """The three endpoints this batch rescued, asserted by their own paths.

    The order rule above is generic; this test states the concrete regression so
    a future re-ordering of ``app/main.py`` fails with the endpoint names in the
    message instead of a wildcard pattern.
    """
    from app.main import app

    table = {
        (str(route.path), method): route.endpoint.__module__
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or ())
    }
    for path, method in (
        ("/api/calls/search", "GET"),
        ("/api/calls/dnc", "GET"),
        ("/api/calls/policies/retry", "POST"),
        ("/api/integrations/salesforce/objects/describe", "GET"),
        ("/api/v1/public/use-cases/health", "GET"),
    ):
        assert (path, method) in table, f"{method} {path} is not registered"

    order = [str(route.path) for route in app.routes if isinstance(route, APIRoute)]
    assert order.index("/api/calls/search") < order.index("/api/calls/{call_id}") or (
        "/api/calls/{call_id}" not in order
    ), "the call search literal is registered after the /api/calls/{call_id} wildcard"
