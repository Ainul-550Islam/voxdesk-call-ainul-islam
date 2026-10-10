#!/usr/bin/env python3
r"""Introspect ``app.main:app`` route table and emit JSON + CSV inventories.

Walks ``app.routes`` and records for every ``APIRoute`` and ``WebSocketRoute``:
  - path, methods, name, endpoint module/qualname, tags
  - whether ``response_model`` and ``status_code`` are declared
  - authentication dependencies (resolved recursively across router + endpoint
    ``dependant.dependencies``)
  - whether any route matches ``/endpoint-\d+`` or carries legacy filler
    banners (``NO SKIP FULL CODE``, ``1050+ lines``)
  - whether any HTTP route has no authentication dependency and is outside the
    explicit public/webhook allowlist.

Writes ``reports/check/routes.json`` and ``reports/check/routes.csv`` and also
prints the JSON report to stdout. Exits non-zero if duplicate ``(method, path)``
pairs, ``/endpoint-N`` routes, banner routes, or unallowlisted unauthenticated
routes are found.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import inspect
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

with contextlib.redirect_stdout(sys.stderr):
    from fastapi.routing import APIRoute
    from starlette.routing import Mount, Route, WebSocketRoute
    from app.main import app

BUILTIN_DOC_PATHS: frozenset[str] = frozenset(
    {
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
)

AUTH_DEP_NAMES: frozenset[str] = frozenset(
    {
        "current_user",
        "current_context",
        "current_membership",
        "require_role",
        "require_admin",
        "_checker",
        "scim_auth",
        "require_step_up",
        "_dep",
    }
)

AUTH_DEP_MODULES: frozenset[str] = frozenset(
    {
        "app.auth.deps",
        "app.auth.identity.scim.router",
        "app.auth.identity.step_up",
    }
)

PUBLIC_EXACT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/health/startup",
        "/health/providers",
        "/health/circuit-breakers",
        "/health/dependencies",
        "/health/deep",
        "/metrics",
        "/status",
        "/robots.txt",
        "/sitemap.xml",
        "/security.txt",
        "/.well-known/security.txt",
        "/widget/embed.js",
        "/auth/login",
        "/auth/signup",
        "/auth/validate-next",
        "/auth/mfa/verify",
        "/auth/refresh",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
        "/auth/email-verification/confirm",
        "/auth/identity-config",
        "/auth/sso/discover",
        "/api/v1/system/health",
        "/api/v1/system/metrics",
        "/api/v1/system/version",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/password-reset/request",
        "/api/v1/auth/password-reset/confirm",
        "/api/v1/billing/plans",
        "/api/v1/billing/webhook",
        "/api/v1/billing/webhooks/stripe",
        "/api/billing/license/verify",
        "/api/public/web-calls",
        "/api/v1/agents/lifecycle/health",
    }
)

PUBLIC_PREFIX_ALLOWLIST: tuple[str, ...] = (
    "/api/v1/public/",
    "/api/v1/sso/",
    "/api/v1/webhooks/",
    "/api/v1/calendar/webhooks/",
    "/api/v1/telephony/twilio/",
    "/api/v1/telephony/telnyx/",
    "/api/v1/telephony/sip/",
    "/api/v1/telephony/webhooks/",
    "/api/v1/voice/webhook/",
    "/auth/sso/",
    "/public/webhooks/",
    "/webhooks/",
)

HANDLER_VERIFIED_ALLOWLIST: frozenset[str] = frozenset(
    {
        "/telephony/voice",
        "/telephony/ivr",
        "/telephony/outbound-answer",
        "/telephony/status",
        "/telephony/transfer-status",
        "/telephony/telnyx/voice",
        "/telephony/web/offer",
        "/channels/message",
        "/channels/status",
        "/api/channels/webhook/sms",
        "/api/channels/webhook/status",
        "/messaging/sms/twilio",
        "/messaging/sms/webhook",
        "/messaging/sms/status",
        "/messaging/sms/send",
        "/api/integrations/crm/inbound/{provider}/{token}",
        "/api/calendar/webhooks/{provider}/{token}",
        "/api/billing/webhook/{provider}",
        "/api/organizations/{organization_id}/invitations/accept",
        "/api/tenants/{tenant_id}/invitations/accept",
        "/api/v1/agents/{agent_id}/health",
        "/api/v1/analytics/dashboards/shared/{share_token}",
        "/scim/v2/{connection_id}/ServiceProviderConfig",
        "/scim/v2/{connection_id}/ResourceTypes",
        "/scim/v2/{connection_id}/Schemas",
        "/scim/v2/{connection_id}/Users",
        "/scim/v2/{connection_id}/Users/{user_id}",
        "/scim/v2/{connection_id}/Groups",
        "/scim/v2/{connection_id}/Groups/{group_id}",
        "/api/v1/auth/api-keys",
        "/api/v1/auth/api-keys/{key_id}",
        "/api/v1/auth/mfa/enroll",
        "/api/v1/auth/mfa/verify",
        "/api/v1/auth/mfa/disable",
        "/api/v1/auth/mfa/challenge",
        "/api/v1/auth/sessions",
        "/api/v1/auth/sessions/{session_id}",
        "/api/v1/auth/sessions/revoke-all",
        "/api/v1/auth/step-up",
        "/api/v1/auth/me/identities",
        "/api/v1/auth/me/identities/{link_id}",
        "/api/v1/compliance/status",
        "/api/v1/compliance/baa/sign",
        "/api/v1/compliance/erasures",
        "/api/v1/compliance/erasures/{erasure_id}/execute",
        "/api/v1/compliance/data-map",
        "/api/v1/compliance/pentest-findings",
        "/api/v1/compliance/pentest-findings/{finding_id}",
        "/api/v1/compliance/evidence-pack",
        "/api/v1/roi/assumptions",
        "/api/v1/roi/summary",
        "/api/v1/roi/benchmark",
        "/api/v1/roi/executive-report",
        "/api/v1/roi/funnel",
        "/api/v1/roi/export.csv",
        "/api/v1/deployments/topologies",
        "/api/v1/deployments",
        "/api/v1/deployments/{profile_id}",
        "/api/v1/deployments/{profile_id}/validate",
        "/api/v1/deployments/{profile_id}/preflight",
        "/api/v1/deployments/{profile_id}/bundle",
        "/api/v1/deployments/{profile_id}/dr-drills",
        "/api/v1/deployments/{profile_id}/heartbeats",
        "/api/v1/analytics/realtime/snapshot",
        "/api/v1/voice/clone/consent-phrase",
        "/api/v1/telephony/calls/{call_id}/takeover-twiml",
    }
)

ENDPOINT_N_RE = re.compile(r"/endpoint-\d+\b")
BANNER_RE = re.compile(r"(NO SKIP FULL CODE|1050\+\s*lines)", re.IGNORECASE)


def _walk_dependant_calls(dependant: Any, seen: set[int] | None = None) -> list[Any]:
    if seen is None:
        seen = set()
    if dependant is None or id(dependant) in seen:
        return []
    seen.add(id(dependant))
    calls: list[Any] = []
    call = getattr(dependant, "call", None)
    if call is not None:
        calls.append(call)
    for sub in getattr(dependant, "dependencies", []) or []:
        calls.extend(_walk_dependant_calls(sub, seen))
    return calls


def _is_auth_callable(fn: Any) -> bool:
    name = getattr(fn, "__name__", "") or getattr(fn, "__qualname__", "")
    mod = getattr(fn, "__module__", "") or ""
    if name in AUTH_DEP_NAMES or mod in AUTH_DEP_MODULES:
        return True
    qual = getattr(fn, "__qualname__", "") or ""
    if any(marker in qual for marker in ("require_role", "require_step_up", "current_user", "current_context")):
        return True
    return False


def _classify_public_allowlist(path: str) -> str | None:
    if path in PUBLIC_EXACT_ALLOWLIST:
        return "public_exact"
    for prefix in PUBLIC_PREFIX_ALLOWLIST:
        if path.startswith(prefix):
            return f"public_prefix:{prefix}"
    if path in HANDLER_VERIFIED_ALLOWLIST:
        return "handler_verified_auth"
    return None


def _has_forbidden_banner(endpoint: Any) -> bool:
    doc = getattr(endpoint, "__doc__", "") or ""
    if BANNER_RE.search(doc):
        return True
    mod = inspect.getmodule(endpoint)
    mod_doc = getattr(mod, "__doc__", "") or ""
    if BANNER_RE.search(mod_doc):
        return True
    return False


def build_inventory() -> dict[str, Any]:
    api_routes: list[dict[str, Any]] = []
    websocket_routes: list[dict[str, Any]] = []
    builtin_routes: list[dict[str, Any]] = []
    mounted_routes: list[dict[str, Any]] = []

    method_counter: Counter[str] = Counter()
    seen_method_path: Counter[tuple[str, str]] = Counter()
    missing_response_model = 0
    missing_status_code = 0
    unauthenticated_outside_allowlist: list[dict[str, Any]] = []
    allowlisted_public_routes: list[dict[str, Any]] = []
    allowlisted_handler_auth_routes: list[dict[str, Any]] = []
    endpoint_n_routes: list[str] = []
    banner_routes: list[str] = []

    for route in app.routes:
        if isinstance(route, APIRoute):
            methods = sorted(m for m in (route.methods or set()) if m != "HEAD")
            for m in methods:
                method_counter[m] += 1
                seen_method_path[(m, route.path)] += 1

            dep_calls = _walk_dependant_calls(route.dependant)
            auth_deps = [
                f"{getattr(c, '__module__', '')}:{getattr(c, '__qualname__', getattr(c, '__name__', repr(c)))}"
                for c in dep_calls
                if _is_auth_callable(c)
            ]
            has_auth_dep = bool(auth_deps)
            allowlist_reason = _classify_public_allowlist(route.path)

            if route.response_model is None:
                missing_response_model += 1
            if route.status_code is None:
                missing_status_code += 1

            if ENDPOINT_N_RE.search(route.path):
                endpoint_n_routes.append(route.path)
            if _has_forbidden_banner(route.endpoint):
                banner_routes.append(route.path)

            if not has_auth_dep:
                entry_summary = {
                    "path": route.path,
                    "methods": methods,
                    "name": route.name,
                    "endpoint": f"{getattr(route.endpoint, '__module__', '')}:{getattr(route.endpoint, '__qualname__', route.name)}",
                    "allowlist_reason": allowlist_reason,
                }
                if allowlist_reason is None:
                    unauthenticated_outside_allowlist.append(entry_summary)
                elif allowlist_reason == "handler_verified_auth":
                    allowlisted_handler_auth_routes.append(entry_summary)
                else:
                    allowlisted_public_routes.append(entry_summary)

            api_routes.append(
                {
                    "path": route.path,
                    "methods": methods,
                    "name": route.name,
                    "endpoint": f"{getattr(route.endpoint, '__module__', '')}:{getattr(route.endpoint, '__qualname__', route.name)}",
                    "tags": [str(t) for t in (route.tags or [])],
                    "has_response_model": route.response_model is not None,
                    "status_code": route.status_code,
                    "authenticated_via_dependency": has_auth_dep,
                    "auth_dependencies": auth_deps,
                    "allowlist_reason": allowlist_reason,
                }
            )
        elif isinstance(route, WebSocketRoute):
            method_counter["WEBSOCKET"] += 1
            seen_method_path[("WEBSOCKET", route.path)] += 1
            websocket_routes.append(
                {
                    "path": route.path,
                    "name": route.name,
                    "endpoint": f"{getattr(route.endpoint, '__module__', '')}:{getattr(route.endpoint, '__qualname__', route.name)}",
                }
            )
        elif isinstance(route, Mount):
            mounted_routes.append(
                {
                    "path": route.path,
                    "name": route.name,
                    "app": type(route.app).__name__,
                }
            )
        elif isinstance(route, Route):
            methods = sorted(m for m in (route.methods or set()) if m != "HEAD")
            builtin_routes.append(
                {
                    "path": route.path,
                    "methods": methods,
                    "name": route.name,
                    "is_builtin_doc": route.path in BUILTIN_DOC_PATHS,
                }
            )

    duplicates = [
        {"method": method, "path": path, "count": count}
        for (method, path), count in sorted(seen_method_path.items())
        if count > 1
    ]

    return {
        "summary": {
            "total_routes_on_app": len(app.routes),
            "api_route_count": len(api_routes),
            "websocket_route_count": len(websocket_routes),
            "builtin_doc_route_count": len(builtin_routes),
            "mounted_route_count": len(mounted_routes),
            "methods": dict(sorted(method_counter.items())),
            "duplicate_method_path_count": len(duplicates),
            "missing_response_model_count": missing_response_model,
            "missing_status_code_count": missing_status_code,
            "authenticated_via_dependency_count": sum(
                1 for r in api_routes if r["authenticated_via_dependency"]
            ),
            "allowlisted_public_route_count": len(allowlisted_public_routes),
            "allowlisted_handler_verified_route_count": len(allowlisted_handler_auth_routes),
            "unauthenticated_outside_allowlist_count": len(unauthenticated_outside_allowlist),
            "endpoint_n_count": len(endpoint_n_routes),
            "banner_route_count": len(banner_routes),
        },
        "duplicates": duplicates,
        "endpoint_n_routes": endpoint_n_routes,
        "banner_routes": banner_routes,
        "unauthenticated_outside_allowlist": unauthenticated_outside_allowlist,
        "allowlisted_public_routes": allowlisted_public_routes,
        "allowlisted_handler_verified_routes": allowlisted_handler_auth_routes,
        "websocket_routes": websocket_routes,
        "builtin_routes": builtin_routes,
        "mounted_routes": mounted_routes,
        "api_routes": api_routes,
    }


def _write_csv(inventory: dict[str, Any], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "kind",
                "methods",
                "path",
                "name",
                "endpoint",
                "has_response_model",
                "status_code",
                "authenticated_via_dependency",
                "allowlist_reason",
            ]
        )
        for r in inventory["api_routes"]:
            writer.writerow(
                [
                    "APIRoute",
                    "|".join(r["methods"]),
                    r["path"],
                    r["name"],
                    r["endpoint"],
                    r["has_response_model"],
                    r["status_code"] or "",
                    r["authenticated_via_dependency"],
                    r["allowlist_reason"] or "",
                ]
            )
        for ws in inventory["websocket_routes"]:
            writer.writerow(
                [
                    "WebSocketRoute",
                    "WEBSOCKET",
                    ws["path"],
                    ws["name"],
                    ws["endpoint"],
                    "",
                    "",
                    "",
                    "websocket_handshake_auth",
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect FastAPI route inventory.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "check" / "routes.json",
        help="Write JSON output to file (default: reports/check/routes.json).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "reports" / "check" / "routes.csv",
        help="Write CSV output to file (default: reports/check/routes.csv).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Exit non-zero on duplicate (method, path) or unallowlisted unauthenticated routes.",
    )
    args = parser.parse_args()

    inventory = build_inventory()
    payload = json.dumps(inventory, indent=2, sort_keys=False)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if args.csv is not None:
        _write_csv(inventory, args.csv)

    print(payload)

    if args.strict:
        if inventory["duplicates"]:
            return 1
        if inventory["unauthenticated_outside_allowlist"]:
            return 2
        if inventory["endpoint_n_routes"] or inventory["banner_routes"]:
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
