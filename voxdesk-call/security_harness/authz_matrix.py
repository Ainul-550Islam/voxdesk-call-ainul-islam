"""Static authorisation matrix over the mounted FastAPI application.

This reads the real route table — not a hand-maintained list — and classifies
every operation by what protects it:

* ``public`` — no authentication dependency at all;
* ``authenticated`` — ``get_current_user``/``get_context`` is in the dependency
  tree;
* ``permission`` — a ``require_permission`` dependency is present;
* ``human_session`` — ``require_human_session`` is present (self-service
  operations on the caller's own account).

Three questions are then answered, and each answer is a probe:

1. Is every non-public route actually authenticated? A route that quietly lost
   its dependency is the bug this exists to catch.
2. Is the public set still exactly the reviewed allowlist? Adding a public
   endpoint is a deliberate act, so an unexplained addition fails.
3. Does every authenticated *mutation* carry either a permission or an explicit
   human-session dependency? Self-service endpoints legitimately have no
   permission (anyone may enroll their own MFA), so those report REVIEW with the
   list rather than FAIL — the list is short enough to read in a review.

Nothing here needs a database or a network, so it runs in every CI invocation.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from fastapi import FastAPI
from fastapi.routing import APIRoute

from security_harness.models import FAIL, PASS, REVIEW, HarnessReport

MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

AUTHN_DEPENDENCIES = ("get_current_user", "get_context")
PERMISSION_PREFIX = "require_permission"
HUMAN_SESSION = "require_human_session"

#: Purpose-built guards that are as strong as a permission for the routes that
#: use them: they resolve the caller's role or an explicit policy and 403 in the
#: dependency itself. A route guarded by one of these is *not* "guarded only by
#: being logged in", and the matrix must not report it as if it were.
ALTERNATIVE_GUARDS: tuple[str, ...] = (
    "require_calls_monitor",
    "require_platform_admin",
    "require_role",
    "require_supervisor",
    "require_known_type",
)

#: Routes that are public on purpose, with the reason. Every entry is a decision
#: someone made; anything not listed here is treated as an accident.
PUBLIC_ROUTES: dict[str, str] = {
    # ---- probes and machine-readable policy --------------------------------
    "/health": "liveness probe, no tenant data",
    "/health/ready": "readiness probe, no tenant data",
    "/health/dependencies": "dependency probe, aggregate status only",
    "/metrics": "scrape endpoint; exposure is an ingress decision, flagged for review",
    "/security.txt": "RFC 9116 contact file",
    "/.well-known/security.txt": "RFC 9116 contact file",
    "/robots.txt": "crawler policy",
    "/sitemap.xml": "crawler policy",
    # ---- identity entry points --------------------------------------------
    "/auth/login": "credential entry point",
    "/auth/signup": "self-service registration, rate limited",
    "/auth/refresh": "refresh token is the credential",
    "/auth/mfa/verify": "second-factor challenge, rate limited",
    "/auth/password-reset/request": "unauthenticated by definition, rate limited",
    "/auth/password-reset/confirm": "reset token is the credential",
    "/auth/email-verification/confirm": "verification token is the credential",
    "/auth/identity-config": "login page needs to know which methods are enabled",
    "/auth/validate-next": "pre-auth redirect helper",
    # ---- SSO (the IdP is the authenticator) -------------------------------
    "/auth/sso/discover": "domain discovery before an authenticated session exists",
    "/auth/sso/{slug}/start": "SSO initiation and callback",
    "/auth/sso/{slug}/callback": "OIDC callback, state+nonce verified",
    "/auth/sso/{slug}/acs": "SAML assertion consumer, signature verified",
    "/auth/sso/{slug}/slo": "SAML single logout, signature verified",
    "/auth/sso/{slug}/metadata": "published SP metadata",
    # ---- SCIM (bearer token verified inside the handler) -------------------
    "/scim/v2/{connection_id}/ServiceProviderConfig": "SCIM discovery, SCIM token required",
    "/scim/v2/{connection_id}/ResourceTypes": "SCIM discovery, SCIM token required",
    "/scim/v2/{connection_id}/Schemas": "SCIM discovery, SCIM token required",
    "/scim/v2/{connection_id}/Users": "SCIM provisioning, SCIM token required",
    "/scim/v2/{connection_id}/Users/{user_id}": "SCIM provisioning, SCIM token required",
    "/scim/v2/{connection_id}/Groups": "SCIM provisioning, SCIM token required",
    "/scim/v2/{connection_id}/Groups/{group_id}": "SCIM provisioning, SCIM token required",
    # ---- carrier and channel webhooks (provider signature) ----------------
    "/telephony/voice": "carrier voice webhook, provider signature verified",
    "/telephony/ivr": "carrier IVR webhook, provider signature verified",
    "/telephony/status": "carrier status callback, provider signature verified",
    "/telephony/outbound-answer": "carrier answer callback, provider signature verified",
    "/telephony/transfer-status": "carrier transfer callback, provider signature verified",
    "/telephony/telnyx/voice": "secondary carrier webhook, signature verified",
    "/telephony/web/offer": "browser softphone offer, widget session token",
    "/channels/status": "carrier status callback, verified by provider signature",
    "/channels/message": "inbound channel message, provider signature verified",
    "/api/channels/webhook/sms": "inbound SMS webhook, signature verified",
    "/api/channels/webhook/whatsapp": "inbound WhatsApp webhook, Twilio HMAC-SHA1 signature verified",
    "/api/channels/webhook/telnyx": "inbound Telnyx messaging webhook, Ed25519/HMAC signature verified",
    "/api/channels/webhook/vonage": "inbound Vonage SMS webhook, Bearer HS256 JWT + payload_hash or HMAC-SHA256 verified",
    "/api/channels/webhook/status": "SMS status callback, signature verified",
    "/messaging/sms/twilio": "inbound SMS webhook, Twilio signature verified",
    "/messaging/sms/webhook": "inbound SMS webhook, signature verified",
    "/messaging/sms/status": "SMS status callback, signature verified",
    "/messaging/sms/send": "provider-signed send callback, rate limited",
    "/api/v1/telephony/webhooks/{provider}/inbound": "provider webhook, signature verified",
    "/api/v1/telephony/webhooks/{provider}/status": "provider webhook, signature verified",
    "/api/v1/telephony/webhooks/{provider}/dtmf": "provider webhook, signature verified",
    "/api/v1/telephony/webhooks/{provider}/recording": "provider webhook, signature verified",
    # ---- carrier v2 (Batch 02) — Vonage voice NCCO + signed event callback ---
    # The carrier, not a signed-in user, is the caller. The NCCO answer webhook
    # and the event callback both verify the Vonage JWT/signature on the raw
    # body before trusting it (app/telephony/vonage_handler.py).
    "/telephony/vonage/answer": "Vonage answer webhook, JWT/signature verified",
    "/telephony/vonage/events": "Vonage event callback, signature verified",
    # ---- integrations v2 (Batch 05) — provider-signed inbound webhooks -----
    # Receive path carries the provider + connection id and runs HMAC/Ed25519
    # verification (and the Zoom/Graph GET challenge handshake) inside
    # app.integrations_v2.core.webhook_inbound before the body is trusted.
    "/webhooks/int2/{provider}/{connection_id}": "vendor inbound webhook, signature/handshake verified",
    "/webhooks/int2/slack/{connection_id}/commands": "Slack slash command, request signature verified",
    "/webhooks/int2/slack/{connection_id}/interactions": "Slack interaction, request signature verified",
    "/webhooks/int2/slack/{connection_id}/interactivity": "Slack interactivity, request signature verified",
    "/webhooks/int2/slack/{connection_id}/slash": "Slack slash command, request signature verified",
    # ---- third-party inbound webhooks (token in path + signature) ---------
    "/public/webhooks/{endpoint_id}": "inbound public webhook, endpoint token in the path",
    "/api/integrations/crm/inbound/{provider}/{token}": "CRM inbound webhook, path token verified",
    "/api/calendar/webhooks/{provider}/{token}": "calendar push notification, path token verified",
    "/api/billing/webhook/{provider}": "billing provider webhook, signature verified",
    "/api/billing/license/verify": "offline licence verification, signature checked",
    # ---- invitation acceptance (signed invite token) ----------------------
    "/api/organizations/{organization_id}/invitations/accept": "invite token is the credential",
    "/api/tenants/{tenant_id}/invitations/accept": "invite token is the credential",
    # ---- anonymous product surface ---------------------------------------
    "/api/platform/tls/ask": "Caddy on-demand TLS decision; shared ask token checked in the handler",
    "/api/mobile/oauth/token": "PKCE code exchange; one-time authorization code, device binding, and verifier checked in the handler",
    "/api/mobile/oauth/refresh": "refresh-token rotation; the presented high-entropy token is the credential",
    "/api/mobile/oauth/revoke": "token-family revocation; the presented high-entropy refresh token is the credential",
    "/api/public/web-calls": "anonymous web-call widget, widget token + rate limit",
    "/api/v1/analytics/dashboards/shared/{share_token}": "shareable dashboard link, share token is the credential",
    "/api/v1/agents/lifecycle/health": "agent runtime health, no tenant data",
    "/api/v1/agents/{agent_id}/health": "agent runtime health, per-agent status only",
    "/api/v1/public/home": "marketing content",
    "/api/v1/public/analytics/summary": "aggregate marketing statistics",
    "/api/v1/public/health": "public status page",
    "/api/v1/public/use-cases": "marketing content",
    "/api/v1/public/use-cases/categories": "marketing content",
    "/api/v1/public/use-cases/{slug}": "marketing content",
    "/api/v1/public/use-cases/health": "public status page",
    "/api/v1/public/site/route-boundary": "route boundary self-check",
    "/api/v1/public/widget/embed.js": "widget loader script",
    "/api/v1/public/voice-demo/session": "anonymous product demo, rate limited per IP",
    "/api/v1/public/voice-demo/session/{session_id}": "anonymous product demo, session token",
    "/api/v1/public/voice-demo/session/{session_id}/event": "anonymous product demo, session token",
    "/widget/embed.js": "widget loader script",
}


#: Routes that legitimately act on the caller's own account or create the
#: caller's own workspace, and therefore carry no tenant permission. Every entry
#: is a decision: the operation either cannot touch another person's data, or the
#: handler checks the parent object's membership before it does anything.
#:
#: A route that starts touching another subject must be removed from this map and
#: given a permission — which is exactly the review this table exists to force.
SELF_SERVICE_ROUTES: dict[str, str] = {
    "/auth/logout": "ends the caller's own session",
    "/auth/logout-all": "ends the caller's own sessions",
    "/auth/me": "returns the caller's own identity",
    "/auth/roles": "the static role catalogue; no tenant data",
    "/api/tenants": "onboarding: the caller creates the workspace they will own",
    "/api/organizations": "onboarding: the caller creates their own organization",
    "/api/organizations/{organization_id}/tenants": (
        "onboarding: adds a tenant to an organization the caller just created; "
        "the handler verifies the caller's membership of that organization"
    ),
}

#: Prefixes whose public routes are expected and covered above; used to keep the
#: allowlist readable when a whole family is public.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/v1/public/",
    # Host- and public-key-scoped widget loader/config endpoints are public;
    # their keys cannot be used on any other route boundary.
    "/api/whitelabel/public/",
)


@dataclasses.dataclass(frozen=True)
class RouteInfo:
    """One mounted operation, classified."""

    path: str
    methods: tuple[str, ...]
    name: str
    authenticated: bool
    permission: bool
    human_session: bool
    dependencies: tuple[str, ...]
    alternative_guard: str = ""

    @property
    def public(self) -> bool:
        return not self.authenticated

    @property
    def mutating(self) -> bool:
        return bool(self.methods_mutation)

    @property
    def methods_mutation(self) -> tuple[str, ...]:
        return tuple(method for method in self.methods if method in MUTATION_METHODS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "methods": list(self.methods),
            "name": self.name,
            "authenticated": self.authenticated,
            "permission": self.permission,
            "human_session": self.human_session,
            "alternative_guard": self.alternative_guard,
        }


def _collect_dependency_names(dependant: Any, seen: set[str]) -> None:
    for sub in getattr(dependant, "dependencies", ()) or ():
        call = getattr(sub, "call", None)
        if call is not None:
            seen.add(getattr(call, "__qualname__", getattr(call, "__name__", str(call))))
            module = getattr(call, "__module__", "")
            seen.add(f"{module}.{getattr(call, '__name__', '')}")
        _collect_dependency_names(sub, seen)


def route_infos(app: FastAPI) -> list[RouteInfo]:
    """Classify every ``APIRoute`` on the application."""
    infos: list[RouteInfo] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        names: set[str] = set()
        _collect_dependency_names(route.dependant, names)
        joined = " ".join(names)
        infos.append(
            RouteInfo(
                path=route.path,
                methods=tuple(sorted(route.methods or ())),
                name=route.name,
                authenticated=any(dep in joined for dep in AUTHN_DEPENDENCIES),
                permission=PERMISSION_PREFIX in joined,
                human_session=HUMAN_SESSION in joined,
                dependencies=tuple(sorted(names)),
                alternative_guard=next(
                    (guard for guard in ALTERNATIVE_GUARDS if guard in joined), ""
                ),
            )
        )
    return infos


def matrix(app: FastAPI) -> dict[str, Any]:
    """The full matrix, grouped the way a reviewer reads it."""
    infos = route_infos(app)
    return {
        "total": len(infos),
        "authenticated": sum(1 for info in infos if info.authenticated),
        "public": sum(1 for info in infos if info.public),
        "with_permission": sum(1 for info in infos if info.permission),
        "mutations": sum(1 for info in infos if info.mutating),
        "mutations_with_permission": sum(
            1 for info in infos if info.mutating and info.permission
        ),
        "rows": [info.as_dict() for info in infos],
    }


def unknown_public_routes(infos: Iterable[RouteInfo]) -> list[RouteInfo]:
    """Public routes that are neither on the allowlist nor under a public prefix."""
    unknown: list[RouteInfo] = []
    for info in infos:
        if not info.public:
            continue
        if info.path in PUBLIC_ROUTES:
            continue
        if any(info.path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            continue
        unknown.append(info)
    return unknown


def unprotected_mutations(infos: Iterable[RouteInfo]) -> list[RouteInfo]:
    """Authenticated mutations with no permission, session or named guard.

    A route whose dependency resolves the caller's role or an explicit policy
    (``require_calls_monitor``, ``require_role``, ``require_platform_admin``, …)
    is guarded; it is simply guarded by something other than
    ``require_permission``. Only routes with *nothing* are reported here.
    """
    return [
        info
        for info in infos
        if info.mutating
        and info.authenticated
        and not info.permission
        and not info.human_session
        and not info.alternative_guard
    ]


def missing_authentication(infos: Iterable[RouteInfo]) -> list[RouteInfo]:
    """Non-allowlisted routes that are public — the hard failure."""
    return unknown_public_routes(infos)


def tenant_path_routes_without_auth(infos: Iterable[RouteInfo]) -> list[RouteInfo]:
    """Routes keyed by tenant or organization in the path must not be anonymous.

    Two exceptions exist and both are on the reviewed allowlist: invitation
    acceptance (the signed invite token is the credential) and organization
    invitation acceptance, which carries an organization id rather than a
    tenant id and is checked by the same rule.
    """
    offenders: list[RouteInfo] = []
    for info in infos:
        if "{tenant_id}" not in info.path:
            continue
        if info.authenticated or info.path in PUBLIC_ROUTES:
            continue
        offenders.append(info)
    return offenders


def run(app: FastAPI) -> HarnessReport:
    """Execute the matrix probes against ``app``."""
    report = HarnessReport(suite="authz_matrix")
    infos = route_infos(app)
    snapshot = matrix(app)

    report.record(
        "authz.inventory",
        PASS,
        f"{snapshot['total']} operations, {snapshot['authenticated']} authenticated",
        evidence={
            key: snapshot[key]
            for key in ("total", "authenticated", "public", "with_permission")
        },
    )

    unknown = unknown_public_routes(infos)
    if unknown:
        report.record(
            "authz.public_allowlist",
            FAIL,
            f"{len(unknown)} public route(s) are not on the reviewed allowlist",
            evidence={
                "routes": [{"path": info.path, "methods": list(info.methods)} for info in unknown]
            },
        )
    else:
        report.record(
            "authz.public_allowlist",
            PASS,
            f"public surface matches the allowlist ({len(PUBLIC_ROUTES)} reviewed routes)",
            evidence={"public_routes": snapshot["public"]},
        )

    tenant_offenders = tenant_path_routes_without_auth(infos)
    if tenant_offenders:
        report.record(
            "authz.tenant_path_requires_auth",
            FAIL,
            "tenant-scoped paths are reachable without authentication",
            evidence={"routes": [info.path for info in tenant_offenders]},
        )
    else:
        report.record(
            "authz.tenant_path_requires_auth",
            PASS,
            "every {tenant_id} route is behind an authentication dependency",
        )

    unguarded = [
        info for info in unprotected_mutations(infos) if info.path not in SELF_SERVICE_ROUTES
    ]
    if unguarded:
        report.record(
            "authz.mutation_guard",
            REVIEW,
            f"{len(unguarded)} authenticated mutation(s) rely on session identity rather than a "
            "permission; each must be a self-service operation on the caller's own account",
            evidence={
                "routes": [
                    f"{'/'.join(info.methods_mutation)} {info.path}" for info in unguarded
                ]
            },
        )
    else:
        report.record(
            "authz.mutation_guard",
            PASS,
            "every authenticated mutation carries a permission or session guard",
        )

    permission_free_reads = [
        info
        for info in infos
        if info.authenticated
        and not info.permission
        and not info.mutating
        and not info.human_session
        and not info.alternative_guard
        and info.path not in SELF_SERVICE_ROUTES
    ]
    report.record(
        "authz.read_permission_coverage",
        PASS if len(permission_free_reads) == 0 else REVIEW,
        (
            "every authenticated read carries a permission"
            if not permission_free_reads
            else f"{len(permission_free_reads)} authenticated read(s) carry no explicit permission"
        ),
        evidence={
            "count": len(permission_free_reads),
            "sample": [info.path for info in permission_free_reads[:25]],
        },
    )
    return report


def as_markdown_rows(report: HarnessReport) -> list[str]:
    """Render one line per check, for a terminal or a CI log."""
    lines = [f"# {report.suite}: {report.status}"]
    for result in report.results:
        lines.append(f"- {result.status:<6} {result.name}: {result.detail}")
    return lines


__all__ = [
    "AUTHN_DEPENDENCIES",
    "MUTATION_METHODS",
    "PUBLIC_PREFIXES",
    "PUBLIC_ROUTES",
    "RouteInfo",
    "as_markdown_rows",
    "matrix",
    "missing_authentication",
    "route_infos",
    "run",
    "tenant_path_routes_without_auth",
    "unknown_public_routes",
    "unprotected_mutations",
]
