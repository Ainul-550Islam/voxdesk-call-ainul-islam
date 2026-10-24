"""Response header and CORS probes.

The middleware under test is ``app.core.security_headers``. This module checks
the properties a browser actually relies on:

* every response carries the fixed header set;
* the CSP forbids inline script and frames the app;
* authenticated responses are not cacheable;
* HSTS appears exactly when the deployment is served over TLS — not on plain
  HTTP, where it would poison a developer's browser;
* CORS never reflects an arbitrary ``Origin`` and never combines a wildcard
  origin with credentials.

Static checks run against a live ``TestClient`` response; the CORS check reads
the mounted middleware configuration *and* sends a preflight, because reading
configuration alone cannot prove the middleware order is right.
"""

from __future__ import annotations

from typing import Any

from security_harness.models import FAIL, PASS, REVIEW, SKIP, HarnessReport

REQUIRED_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-permitted-cross-domain-policies": "none",
    "cross-origin-resource-policy": "same-origin",
}

#: Directives that must be present in the CSP, with the value they must not be.
FORBIDDEN_CSP_TOKENS = ("'unsafe-inline'", "'unsafe-eval'", "*")

#: Paths probed for the general header set. The first is public, the second is
#: authenticated; both must be covered by the middleware.
PROBE_PATHS = ("/health", "/api/v1/agents")


def _header_map(response: Any) -> dict[str, str]:
    return {key.lower(): value for key, value in response.headers.items()}


def check_response_headers(response: Any, *, path: str) -> list[tuple[str, str, dict]]:
    """Header assertions for one response. Returns ``(name, status, evidence)`` rows."""
    rows: list[tuple[str, str, dict]] = []
    headers = _header_map(response)
    missing = {
        key: expected
        for key, expected in REQUIRED_HEADERS.items()
        if headers.get(key, "").lower() != expected.lower()
    }
    rows.append(
        (
            f"headers.fixed_set{_suffix(path)}",
            FAIL if missing else PASS,
            {"path": path, "missing_or_wrong": missing} if missing else {"path": path},
        )
    )

    csp = headers.get("content-security-policy", "")
    if not csp:
        rows.append((f"headers.csp{_suffix(path)}", FAIL, {"path": path, "csp": ""}))
    else:
        script_directive = _directive(csp, "script-src")
        problems = [token for token in FORBIDDEN_CSP_TOKENS if token in script_directive]
        if "frame-ancestors 'none'" not in csp:
            problems.append("frame-ancestors missing")
        if "object-src 'none'" not in csp:
            problems.append("object-src missing")
        rows.append(
            (
                f"headers.csp{_suffix(path)}",
                FAIL if problems else PASS,
                {"path": path, "problems": problems, "script_src": script_directive},
            )
        )

    cache = headers.get("cache-control", "").lower()
    authenticated = path.startswith("/api/")
    if authenticated and "no-store" not in cache:
        rows.append(
            (
                f"headers.cache_control{_suffix(path)}",
                FAIL,
                {"path": path, "cache_control": cache},
            )
        )
    else:
        rows.append(
            (f"headers.cache_control{_suffix(path)}", PASS, {"path": path, "cache_control": cache})
        )
    return rows


def _suffix(path: str) -> str:
    return "." + path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or ".root"


def _directive(csp: str, name: str) -> str:
    for part in csp.split(";"):
        stripped = part.strip()
        if stripped.startswith(name):
            return stripped
    return ""


def check_hsts(response: Any, *, uses_https: bool) -> tuple[str, str, dict]:
    """HSTS must be present over TLS and absent over plain HTTP."""
    headers = _header_map(response)
    present = "strict-transport-security" in headers
    if uses_https and not present:
        return "headers.hsts", FAIL, {"uses_https": True, "present": False}
    if not uses_https and present:
        return (
            "headers.hsts",
            REVIEW,
            {
                "uses_https": False,
                "present": True,
                "note": "HSTS on a plain-HTTP deployment will be ignored or poison localhost",
            },
        )
    return "headers.hsts", PASS, {"uses_https": uses_https, "present": present}


def check_cors(app: Any, client: Any, *, origin: str = "https://evil.example") -> list[tuple[str, str, dict]]:
    """CORS configuration plus a live preflight from an untrusted origin."""
    rows: list[tuple[str, str, dict]] = []
    allow_origins: list[str] = []
    allow_credentials = False
    for middleware in getattr(app, "user_middleware", []):
        if middleware.cls.__name__ == "CORSMiddleware":
            options = middleware.kwargs or {}
            allow_origins = list(options.get("allow_origins") or [])
            allow_credentials = bool(options.get("allow_credentials"))

    if "*" in allow_origins and allow_credentials:
        rows.append(
            ("cors.wildcard_with_credentials", FAIL, {"allow_origins": allow_origins})
        )
    elif "*" in allow_origins:
        rows.append(("cors.wildcard_with_credentials", REVIEW, {"allow_origins": allow_origins}))
    else:
        rows.append(
            ("cors.wildcard_with_credentials", PASS, {"allow_origins": allow_origins})
        )

    if client is None:
        rows.append(("cors.preflight", SKIP, {"reason": "no_client"}))
        return rows

    response = client.request(
        "OPTIONS",
        "/api/v1/agents",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    allow_origin = response.headers.get("access-control-allow-origin", "")
    if allow_origin in ("*", origin):
        rows.append(
            (
                "cors.preflight",
                FAIL,
                {"origin": origin, "allow_origin": allow_origin, "status": response.status_code},
            )
        )
    else:
        rows.append(
            (
                "cors.preflight",
                PASS,
                {"origin": origin, "allow_origin": allow_origin, "status": response.status_code},
            )
        )
    return rows


def run(app: Any, client: Any = None, *, uses_https: bool = False) -> HarnessReport:
    """Execute the header and CORS probes.

    ``client`` is a synchronous ``starlette.testclient.TestClient``; without one,
    the live checks report SKIP rather than a pass.
    """
    report = HarnessReport(suite="security_headers")
    if client is None:
        for path in PROBE_PATHS:
            report.record(
                f"headers_and_cors.headers{_suffix(path)}",
                SKIP,
                "no client supplied",
                evidence={"path": path},
            )
        report.record("cors", SKIP, "no client supplied")
        return report

    for path in PROBE_PATHS:
        response = client.get(path)
        for name, probe_status, evidence in check_response_headers(response, path=path):
            report.record(f"headers_and_cors.{name}", probe_status, evidence=evidence)
        name, probe_status, evidence = check_hsts(response, uses_https=uses_https)
        report.record(
            f"headers_and_cors.{name}", probe_status, evidence=dict(evidence, path=path)
        )

    for name, probe_status, evidence in check_cors(app, client):
        report.record(name, probe_status, evidence=evidence)
    return report


__all__ = [
    "FORBIDDEN_CSP_TOKENS",
    "PROBE_PATHS",
    "REQUIRED_HEADERS",
    "check_cors",
    "check_hsts",
    "check_response_headers",
    "run",
]
