"""Evidence-backed, read-only aggregation for the final parity surface.

The service reads the live FastAPI route table and the tenant's persisted
integration configuration. It never calls an external provider, treats route
registration as E2E verification, or returns credential material.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.enterprise_models import SalesforceConnection
from app.db.models import CalendarIntegration, CrmIntegration
from app.schemas.parity import (
    CapabilityItem,
    CapabilityInventoryResponse,
    IntegrationInventoryResponse,
    IntegrationStatusItem,
    RouteEvidence,
)


# Each capability is anchored to a concrete route family. `PARTIAL` means that
# the public surface is broader than the evidence represented by those routes;
# it is not a judgement that the registered handler is broken.
_CAPABILITY_SPECS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "public_site",
        "Public website and sales intake",
        ("/api/v1/public/site/", "/api/v1/public/contact-sales"),
        "Public manifest, pricing, status, route boundary, and contact-sales APIs are registered; responsive/browser journeys are not measured here.",
    ),
    (
        "voice_agents",
        "Voice agents and versioned builder",
        ("/api/v1/agents", "/api/agents"),
        "Tenant-scoped agent, draft, validation, publish, version, rollback, and test routes are registered; provider reachability is separate.",
    ),
    (
        "prompt_voice_model",
        "Prompt, voice, model, tools, and dynamic variables",
        ("/api/v1/agents/", "/api/agents/voices", "/api/agents/models", "/api/agents/tools/catalog", "/api/dynamic-variables"),
        "Configuration APIs and runtime catalogues exist; a selectable provider is not proof of live provider credentials or latency.",
    ),
    (
        "chat",
        "Chat agents and durable chat sessions",
        ("/api/chat-agents", "/api/chat-sessions", "/api/v1/public/widget/sessions"),
        "Chat agent/session/message records persist, but replies are deterministic local text rather than evidence of a connected LLM provider.",
    ),
    (
        "sms",
        "SMS and multichannel messaging",
        ("/api/channels", "/channels/message", "/api/multichannel", "/api/inbox/threads/"),
        "Channel registration is persisted, but POST /api/channels/send and provider health/receipt operations currently fail closed with 501; no delivery is claimed.",
    ),
    (
        "knowledge",
        "Knowledge ingestion, indexing, and retrieval",
        ("/api/knowledge/", "/api/knowledge-base", "/api/knowledge/documents"),
        "Knowledge APIs and retrieval paths exist; source parsing and cross-agent answer quality require their dedicated integration tests.",
    ),
    (
        "tools_webhooks",
        "Tools, functions, connectors, and webhooks",
        ("/api/agents/tools/catalog", "/api/connectors/", "/api/webhooks", "/public/webhooks/"),
        "Tool catalog, connector, outbound webhook, and public callback surfaces are present; individual integrations remain separately configured.",
    ),
    (
        "simulation_testing",
        "Simulation, agent testing, and regression runs",
        ("/api/simulations", "/api/v1/testing/", "/api/v1/agent-tests/"),
        "Persisted simulation and test-run APIs exist; a simulation is intentionally distinct from a production telephony call.",
    ),
    (
        "telephony_phone_numbers",
        "Phone numbers, SIP, inbound, and outbound telephony",
        ("/api/v1/telephony/phone-numbers", "/api/v1/telephony/sip-connections", "/api/v1/telephony/calls/", "/api/v1/telephony/webhooks/"),
        "Number, SIP, call-session, and provider-webhook paths exist. Live carrier operation requires credentials; deterministic synthetic media is restricted to explicit simulations, while live LLM/TTS response handling is NOT_CONFIGURED.",
    ),
    (
        "call_transfer_dtmf",
        "Call control, transfer, agent transfer, DTMF, and IVR",
        ("/api/v1/telephony/calls/", "/api/calls/"),
        "Call-control route families exist; per-call state, permissions, and provider support must still be verified for a specific session.",
    ),
    (
        "calls_transcripts_analysis",
        "Call history, transcripts, summaries, and analysis",
        ("/api/calls", "/api/analytics/", "/api/conversations/", "/api/v1/telephony/calls/"),
        "Call and analysis APIs are registered. Analytics availability does not imply a completed live call or populated data.",
    ),
    (
        "live_monitoring_takeover",
        "Live monitoring, listen/whisper, and takeover",
        ("/api/calls/{call_id}/monitor", "/api/calls/{call_id}/takeover", "/api/calls/monitor/"),
        "Monitor/takeover APIs persist control-plane sessions and audit metadata. They do not establish live audio listen, whisper, barge, or takeover media connectivity.",
    ),
    (
        "analytics_dashboards",
        "Analytics and custom dashboards",
        ("/api/analytics/", "/api/v1/public/analytics/"),
        "Operational analytics APIs exist. Saved/custom dashboards are not claimed from an aggregate analytics endpoint alone.",
    ),
    (
        "built_in_crm",
        "Contacts, contact memory, CRM, and CRM writeback",
        ("/api/contacts", "/api/contact-memory", "/api/integrations/crm", "/api/integrations/salesforce/"),
        "Tenant contact and CRM integration paths exist. Two-way provider synchronization requires tenant credentials and a successful provider check.",
    ),
    (
        "calendar_integrations",
        "Calendar and appointment integrations",
        ("/api/calendar/integrations", "/api/appointments", "/api/calendar/webhooks/"),
        "Calendar configuration and appointment paths exist; external calendar credentials and booking availability are tenant-specific.",
    ),
    (
        "campaigns_batch",
        "Campaigns and batch outbound calls",
        ("/api/campaigns", "/api/batch-calls", "/api/tenants/{tenant_id}/campaigns"),
        "Campaign and batch-call APIs are registered; execution can spend provider usage and depends on consent, audience, and carrier configuration.",
    ),
    (
        "workflows",
        "Workflow orchestration and post-call actions",
        ("/api/workflows", "/api/workflows/triggers", "/api/workflows/calls/"),
        "Workflow definitions, executions, triggers, and call context/writeback APIs are registered; configured actions are not inferred.",
    ),
    (
        "conductor",
        "Conductor diagnosis, simulation, proposal, and approval",
        ("/api/v1/conductor/",),
        "Conductor session and review lifecycle routes are registered; applying a proposal remains an explicit, authorized operation.",
    ),
    (
        "guardrails_pii",
        "Input/output guardrails and PII handling",
        ("/api/v1/agents/", "/api/calls/search", "/api/v1/audit/events"),
        "Runtime guardrail and redaction code exists, but detection is not complete PII coverage and per-agent storage behavior is not represented by one route family.",
    ),
    (
        "data_retention",
        "Data retention, privacy, and deletion controls",
        ("/api/retention", "/api/tenants/{tenant_id}/governance/retention", "/api/gdpr/"),
        "Retention and privacy routes/configuration exist; database-wide retention execution and audit-log immutability have separate limits.",
    ),
    (
        "billing_usage",
        "Usage, billing, invoices, and plan changes",
        ("/api/billing", "/api/analytics/usage", "/api/usage"),
        "Billing and usage APIs are registered. No payment result is asserted unless a persisted invoice/provider response exists.",
    ),
    (
        "enterprise_security",
        "Enterprise roles, API keys, SSO, and sessions",
        ("/api/identity/", "/api/scim/", "/api/sso/", "/api/api-keys", "/api/v1/public-keys"),
        "Identity, SSO/SCIM, session, and key-management surfaces exist; production IdP setup is configuration-dependent.",
    ),
)

_PARTIAL_CAPABILITIES = frozenset(
    {
        "public_site",
        "voice_agents",
        "prompt_voice_model",
        "chat",
        "sms",
        "knowledge",
        "tools_webhooks",
        "telephony_phone_numbers",
        "call_transfer_dtmf",
        "calls_transcripts_analysis",
        "live_monitoring_takeover",
        "analytics_dashboards",
        "built_in_crm",
        "calendar_integrations",
        "campaigns_batch",
        "workflows",
        "conductor",
        "guardrails_pii",
        "data_retention",
        "billing_usage",
        "enterprise_security",
    }
)

def _iter_api_operations(app: Any) -> list[tuple[str, str, str]]:
    operations: set[tuple[str, str, str]] = set()
    for route in getattr(app, "routes", ()):
        path = str(getattr(route, "path", "") or "")
        if not path.startswith("/api/"):
            continue
        endpoint = getattr(route, "endpoint", None)
        module = str(getattr(endpoint, "__module__", "unknown"))
        methods = getattr(route, "methods", None) or ()
        for method in methods:
            method_name = str(method).upper()
            if method_name not in {"HEAD", "OPTIONS"}:
                operations.add((method_name, path, module))
    return sorted(operations)


def capability_inventory(app: Any) -> CapabilityInventoryResponse:
    """Return route-backed evidence without converting presence into a test pass."""
    operations = _iter_api_operations(app)
    capability_items: list[CapabilityItem] = []
    for key, label, fragments, summary in _CAPABILITY_SPECS:
        matched = [
            operation
            for operation in operations
            if any(fragment in operation[1] for fragment in fragments)
        ]
        evidence = [
            RouteEvidence(method=method, path=path, module=module)
            for method, path, module in matched[:8]
        ]
        if not matched:
            status = "MISSING"
        elif key in _PARTIAL_CAPABILITIES:
            status = "PARTIAL"
        else:
            status = "IMPLEMENTED"
        capability_items.append(
            CapabilityItem(
                key=key,
                label=label,
                status=status,
                summary=summary,
                evidence_routes=evidence,
            )
        )

    state = getattr(app, "state", None)
    return CapabilityInventoryResponse(
        generated_at=datetime.now(timezone.utc),
        registered_api_operations=len(operations),
        suppressed_generated_placeholder_routes=int(
            getattr(state, "suppressed_generated_placeholder_routes", 0)
        ),
        suppressed_generic_placeholder_routes=int(
            getattr(state, "suppressed_generic_placeholder_routes", 0)
        ),
        capabilities=capability_items,
        limitation=(
            "Route registration is implementation evidence only. This endpoint does not execute calls, "
            "test providers, read frontend state, or claim feature verification/production readiness."
        ),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _provider_status(
    *,
    enabled: bool,
    credentials_present: bool,
    key_id_present: bool,
    health_checked_at: datetime | None,
    health_ok: bool | None,
    explicit_sandbox: bool = False,
) -> tuple[str, str]:
    if not enabled:
        return "DISABLED", "The persisted integration is disabled."
    if not credentials_present or not key_id_present:
        return "AUTH_REQUIRED", "Encrypted credentials or their key reference are absent."
    if explicit_sandbox:
        return "SANDBOX", "The persisted non-secret configuration explicitly selects sandbox mode."
    checked_at = _as_utc(health_checked_at)
    if checked_at is None:
        return "UNVERIFIED", "Configuration exists, but no provider health-check timestamp is stored."
    age = datetime.now(timezone.utc) - checked_at
    if age < timedelta(0) or age > timedelta(minutes=15):
        return "UNVERIFIED", "Configuration exists, but the stored provider health check is not recent."
    if health_ok is False:
        return "ERROR", "The latest recent persisted health check failed; its error text is intentionally withheld."
    if health_ok is True:
        return "CONNECTED", "A successful persisted health check occurred within the last 15 minutes."
    return "UNVERIFIED", "Configuration exists, but the persisted provider health-check result is unknown."


def _explicit_sandbox(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    mode = str(config.get("mode") or config.get("environment") or "").strip().lower()
    return config.get("sandbox") is True or mode in {"sandbox", "test"}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


async def integration_inventory(
    session: AsyncSession,
    tenant_id: Any,
) -> IntegrationInventoryResponse:
    """Return status derived from tenant records and deployment config, never secrets."""
    return await _integration_inventory_async(session, tenant_id)


async def _integration_inventory_async(
    session: AsyncSession,
    tenant_id: Any,
) -> IntegrationInventoryResponse:
    crm_rows = (
        await session.execute(
            select(CrmIntegration)
            .where(CrmIntegration.tenant_id == tenant_id)
            .order_by(CrmIntegration.provider)
        )
    ).scalars().all()
    calendar_rows = (
        await session.execute(
            select(CalendarIntegration)
            .where(CalendarIntegration.tenant_id == tenant_id)
            .order_by(CalendarIntegration.provider)
        )
    ).scalars().all()
    salesforce_row = (
        await session.execute(
            select(SalesforceConnection).where(SalesforceConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    items: list[IntegrationStatusItem] = []
    for row in crm_rows:
        credentials_present = bool((row.credentials_encrypted or "").strip())
        status, basis = _provider_status(
            enabled=bool(row.is_enabled),
            credentials_present=credentials_present,
            key_id_present=bool((row.credentials_key_id or "").strip()),
            health_checked_at=row.last_health_check_at,
            health_ok=row.last_health_ok,
            explicit_sandbox=_explicit_sandbox(row.config),
        )
        items.append(
            IntegrationStatusItem(
                integration_type="crm",
                provider=_enum_value(row.provider),
                status=status,
                configured=credentials_present,
                enabled=bool(row.is_enabled),
                credentials_present=credentials_present,
                last_health_check_at=_as_utc(row.last_health_check_at),
                last_health_ok=row.last_health_ok,
                status_basis=basis,
            )
        )

    for row in calendar_rows:
        credentials_present = bool((row.credentials_encrypted or "").strip())
        status, basis = _provider_status(
            enabled=bool(row.is_enabled),
            credentials_present=credentials_present,
            key_id_present=bool((row.credentials_key_id or "").strip()),
            health_checked_at=row.last_health_check_at,
            health_ok=row.last_health_ok,
            explicit_sandbox=_explicit_sandbox(row.config),
        )
        items.append(
            IntegrationStatusItem(
                integration_type="calendar",
                provider=_enum_value(row.provider),
                status=status,
                configured=credentials_present,
                enabled=bool(row.is_enabled),
                credentials_present=credentials_present,
                last_health_check_at=_as_utc(row.last_health_check_at),
                last_health_ok=row.last_health_ok,
                status_basis=basis,
            )
        )

    if salesforce_row is None:
        items.append(
            IntegrationStatusItem(
                integration_type="crm",
                provider="salesforce",
                status="NOT_CONFIGURED",
                configured=False,
                enabled=False,
                credentials_present=False,
                status_basis="No tenant Salesforce connection row exists.",
            )
        )
    else:
        credentials_present = bool(
            (salesforce_row.access_token_encrypted or "").strip()
            and (salesforce_row.refresh_token_encrypted or "").strip()
        )
        if not salesforce_row.is_active:
            status = "DISABLED"
            basis = "The persisted Salesforce connection is inactive."
        elif not credentials_present:
            status = "AUTH_REQUIRED"
            basis = "The encrypted Salesforce credential bundle is incomplete."
        else:
            status = "UNVERIFIED"
            basis = "A Salesforce connection row exists, but no provider health-check timestamp is stored on it."
        items.append(
            IntegrationStatusItem(
                integration_type="crm",
                provider="salesforce",
                status=status,
                configured=credentials_present,
                enabled=bool(salesforce_row.is_active),
                credentials_present=credentials_present,
                last_health_check_at=None,
                last_health_ok=None,
                status_basis=basis,
            )
        )

    # These are deployment-wide credential-presence checks, not tenant-specific
    # connections and not successful connectivity tests.
    deployment_providers = (
        (
            "telephony",
            "twilio",
            bool((getattr(settings, "twilio_account_sid", "") or "").strip()),
            bool((getattr(settings, "twilio_auth_token", "") or "").strip()),
        ),
        (
            "telephony",
            "telnyx",
            bool((getattr(settings, "telnyx_api_key", "") or "").strip()),
            bool((getattr(settings, "telnyx_connection_id", "") or "").strip()),
        ),
        (
            "payments",
            "stripe",
            bool((getattr(settings, "stripe_secret_key", "") or "").strip()),
            bool((getattr(settings, "stripe_webhook_secret", "") or "").strip()),
        ),
    )
    for integration_type, provider, primary_present, secondary_present in deployment_providers:
        configured = primary_present and secondary_present
        if configured:
            status = "UNVERIFIED"
            basis = "Required deployment settings are present; this endpoint did not contact the provider."
        elif primary_present or secondary_present:
            status = "AUTH_REQUIRED"
            basis = "Only part of the required deployment credential/configuration pair is present."
        else:
            status = "NOT_CONFIGURED"
            basis = "Required deployment credential/configuration settings are absent."
        items.append(
            IntegrationStatusItem(
                integration_type=integration_type,
                provider=provider,
                status=status,
                configured=configured,
                enabled=None,
                credentials_present=primary_present or secondary_present,
                status_basis=basis,
            )
        )

    return IntegrationInventoryResponse(
        tenant_id=tenant_id,
        generated_at=datetime.now(timezone.utc),
        items=items,
        limitation=(
            "Credential fields and provider error details are never returned. CONNECTED is emitted only "
            "for a tenant integration with a successful stored check no older than 15 minutes; "
            "deployment-wide credentials remain UNVERIFIED until a real health check is recorded."
        ),
    )
