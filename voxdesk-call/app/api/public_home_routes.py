"""Public home, analytics-summary, and voice-demo endpoints.

The home endpoint returns marketing labels paired with route-registration evidence.
Route registration is not a claim that a provider is configured or that a workflow
has passed end-to-end verification. Public analytics never returns tenant metrics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.rate_limit import allow_identity_action, client_ip as trusted_client_ip
from app.core.logging import log

router = APIRouter(prefix="/api/v1/public", tags=["public-home"])

FeatureStatus = Literal["PARTIAL", "MISSING"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(event: str, **kwargs: Any) -> None:
    try:
        log.info(event, **kwargs)
    except Exception:
        # Public page telemetry is best-effort and is not an authorization control.
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        return


class Capability(_Strict):
    id: str
    title: str
    description: str
    icon: str
    href: str
    category: str
    status: FeatureStatus
    evidence_routes: List[str] = Field(default_factory=list)


class UseCase(_Strict):
    id: str
    title: str
    description: str
    icon: str
    href: str
    status: FeatureStatus
    evidence_routes: List[str] = Field(default_factory=list)


class SecurityItem(_Strict):
    id: str
    title: str
    description: str
    status: FeatureStatus
    evidence_routes: List[str] = Field(default_factory=list)


class DeveloperFeature(_Strict):
    id: str
    title: str
    description: str
    docs_href: str
    status: FeatureStatus
    evidence_routes: List[str] = Field(default_factory=list)


class HomeData(_Strict):
    capabilities: List[Capability]
    use_cases: List[UseCase]
    security_items: List[SecurityItem]
    developer_features: List[DeveloperFeature]


class HomeResponse(_Strict):
    status: Literal["ok"] = "ok"
    data: HomeData
    meta: Dict[str, Any] = Field(default_factory=dict)


class AnalyticsSummary(_Strict):
    calls: Optional[int] = None
    successful_calls: Optional[int] = None
    average_duration_seconds: Optional[float] = None
    average_latency_ms: Optional[float] = None
    cost: Optional[float] = None
    status: Literal["ok", "empty", "not_configured", "error"] = "not_configured"
    message: Optional[str] = None


class AnalyticsSummaryResponse(_Strict):
    status: Literal["ok"] = "ok"
    data: AnalyticsSummary


class VoiceDemoSessionCreate(_Strict):
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VoiceDemoEventIn(_Strict):
    event: Literal["start", "stop", "interrupt", "error"]
    timestamp: str
    data: Dict[str, Any] = Field(default_factory=dict)


def _registered_api_operations(app: Any) -> Set[str]:
    """Return registered method/path pairs without claiming they are all usable."""
    operations: Set[str] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if not isinstance(path, str) or not path.startswith("/api/"):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            normalized_method = str(method).upper()
            if normalized_method in {"HEAD", "OPTIONS"}:
                continue
            operations.add(f"{normalized_method} {path}")
    return operations


def _matching_routes(operations: Set[str], markers: tuple[str, ...]) -> List[str]:
    paths = sorted({operation.split(" ", 1)[1] for operation in operations})
    return [path for path in paths if any(marker in path.lower() for marker in markers)]


def _status_for(routes: List[str]) -> FeatureStatus:
    # Registered routes alone cannot substantiate end-to-end or production readiness.
    return "PARTIAL" if routes else "MISSING"


def _get_real_capabilities(app: Any) -> List[Capability]:
    operations = _registered_api_operations(app)
    definitions = [
        ("build_agents", "Agent configuration", "Agent configuration endpoints are registered. Model, voice, tools, knowledge, publication, and provider readiness must be verified for the selected deployment.", "bot", "/agent", "core", ("/agents",)),
        ("test_agents", "Testing and simulation", "Testing routes are inventoried where registered. A route listing does not prove a simulation run or result persistence.", "flask", "/agent", "core", ("/simulation", "/simulations", "/testing", "/test-runs", "/agent-tests")),
        ("deploy_phone", "Phone numbers and telephony", "Phone-number and telephony routes are inventoried where registered. Carrier credentials and number eligibility are deployment-specific.", "phone", "/phone-numbers", "core", ("/phone-numbers", "/telephony", "/sip")),
        ("monitor_calls", "Live monitoring and takeover", "Monitoring routes are inventoried where registered. A UI or route does not prove an active media session or operator authorization.", "eye", "/calls", "core", ("/monitor", "/takeover", "/whisper", "/listen")),
        ("analyze_conversations", "Calls and analytics", "Call and analytics routes are inventoried where registered. Public analytics does not expose tenant-private call metrics.", "chart", "/analytics", "core", ("/calls", "/analytics")),
        ("knowledge_base", "Knowledge sources", "Knowledge-source routes are inventoried where registered. Parsing, indexing, retrieval, and tenant isolation require separate workflow verification.", "book", "/knowledge", "core", ("/knowledge", "/knowledge-base", "/knowledge_bases")),
        ("integrations", "Integrations and webhooks", "Integration and webhook routes are inventoried where registered. Credentials and successful remote synchronization are not inferred.", "plug", "/integrations", "core", ("/integrations", "/webhooks", "/crm", "/calendar")),
        ("batch_campaigns", "Campaigns and outbound calling", "Campaign routes are inventoried where registered. Calling permissions, consent, carrier configuration, and a successful campaign run are not inferred.", "megaphone", "/campaigns", "core", ("/campaigns", "/batch-calls", "/outbound")),
    ]
    capabilities: List[Capability] = []
    for identifier, title, description, icon, href, category, markers in definitions:
        evidence = _matching_routes(operations, markers)
        capabilities.append(Capability(
            id=identifier,
            title=title,
            description=description,
            icon=icon,
            href=href,
            category=category,
            status=_status_for(evidence),
            evidence_routes=evidence[:8],
        ))
    return capabilities


def _get_real_use_cases(app: Any) -> List[UseCase]:
    operations = _registered_api_operations(app)
    definitions = [
        ("customer_support", "Customer Support", "A workflow category for support calls; deployment-specific agent, knowledge, and transfer configuration is not implied.", "headset", "/use-cases/support", ("/agents", "/calls", "/knowledge", "/transfer")),
        ("appointment_booking", "Appointment Booking", "A workflow category for scheduling; calendar credentials, availability, and booking completion are not implied.", "calendar", "/use-cases/appointments", ("/calendar", "/appointments", "/integrations")),
        ("lead_qualification", "Lead Qualification", "A workflow category for lead intake; CRM connection and field-write behavior require configuration and verification.", "filter", "/use-cases/leads", ("/leads", "/crm", "/agents")),
        ("outbound_campaigns", "Outbound Campaigns", "A workflow category for outbound calls; campaign execution depends on consent, carrier, and tenant configuration.", "outbound", "/use-cases/outbound", ("/campaigns", "/batch-calls", "/outbound")),
        ("receptionist", "AI Receptionist", "A workflow category for inbound routing; phone-number, schedule, and transfer behavior must be configured for a tenant.", "reception", "/use-cases/receptionist", ("/phone-numbers", "/calls", "/transfer")),
        ("collections", "Collections and Follow-up", "A workflow category; disposition, task creation, and external write-back are not inferred from the route inventory.", "collections", "/use-cases/collections", ("/campaigns", "/tasks", "/crm")),
        ("custom_workflows", "Custom Workflows", "A workflow category; registered workflow APIs do not establish that a particular workflow has been configured or run.", "workflow", "/use-cases/workflows", ("/workflows", "/tools", "/webhooks")),
    ]
    result: List[UseCase] = []
    for identifier, title, description, icon, href, markers in definitions:
        evidence = _matching_routes(operations, markers)
        result.append(UseCase(
            id=identifier,
            title=title,
            description=description,
            icon=icon,
            href=href,
            status=_status_for(evidence),
            evidence_routes=evidence[:8],
        ))
    return result


def _get_security_items(app: Any) -> List[SecurityItem]:
    operations = _registered_api_operations(app)
    definitions = [
        ("authentication", "Authentication and authorization", "Authentication and permission enforcement are implemented across protected paths to varying extents. This inventory is not a route-by-route authorization test or an identity-provider configuration check.", ("/auth", "/login", "/sessions", "/api-keys")),
        ("tenant_scope", "Tenant and environment scope", "Tenant and environment context exist in the application. Database-level isolation and every module's cross-tenant behavior require separate evidence; no universal row-level-security claim is made.", ("/organizations", "/environments", "/tenant")),
        ("secret_handling", "Credentials and privacy controls", "Credential-bearing operations are server-side. Key-management, provider connectivity, retention, and certification state are deployment-specific and are not asserted here.", ("/integrations", "/provider", "/secrets", "/api-keys")),
    ]
    result: List[SecurityItem] = []
    for identifier, title, description, markers in definitions:
        evidence = _matching_routes(operations, markers)
        result.append(SecurityItem(
            id=identifier,
            title=title,
            description=description,
            status=_status_for(evidence),
            evidence_routes=evidence[:8],
        ))
    return result


def _get_developer_features(app: Any) -> List[DeveloperFeature]:
    operations = _registered_api_operations(app)
    definitions = [
        ("api_first", "HTTP API surface", "Registered API operations are listed as route evidence only; authentication, schema correctness, and successful data effects require separate checks.", "/docs", ("/api/",)),
        ("webhooks", "Webhooks", "Webhook endpoints are inventoried where registered. Delivery, signatures, retry behavior, and replay protection require workflow tests.", "/docs#webhooks", ("/webhooks",)),
        ("tool_calling", "Tools and integrations", "Tool and integration endpoints are inventoried where registered. A listed endpoint does not prove a configured external connection.", "/docs#tools", ("/tools", "/integrations")),
    ]
    result: List[DeveloperFeature] = []
    for identifier, title, description, docs_href, markers in definitions:
        evidence = _matching_routes(operations, markers)
        result.append(DeveloperFeature(
            id=identifier,
            title=title,
            description=description,
            docs_href=docs_href,
            status=_status_for(evidence),
            evidence_routes=evidence[:8],
        ))
    return result


@router.get("/home", response_model=HomeResponse)
async def get_public_home(request: Request) -> HomeResponse:
    """Return public labels and route-registration evidence, not runtime readiness."""
    operations = _registered_api_operations(request.app)
    data = HomeData(
        capabilities=_get_real_capabilities(request.app),
        use_cases=_get_real_use_cases(request.app),
        security_items=_get_security_items(request.app),
        developer_features=_get_developer_features(request.app),
    )
    _audit(
        "public.home.viewed",
        ip=request.client.host if request.client else "unknown",
        capability_count=len(data.capabilities),
    )
    return HomeResponse(
        data=data,
        meta={
            "generated_at": _now_iso(),
            "registered_api_operations": len(operations),
            "evidence_scope": "registered method/path pairs; not end-to-end verification",
        },
    )


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
async def get_public_analytics_summary() -> AnalyticsSummaryResponse:
    """Keep tenant-private metrics out of the public endpoint; no synthetic zeros."""
    summary = AnalyticsSummary(
        calls=None,
        successful_calls=None,
        average_duration_seconds=None,
        average_latency_ms=None,
        cost=None,
        status="not_configured",
        message="Public analytics are not configured. Sign in to view workspace-scoped call analytics.",
    )
    return AnalyticsSummaryResponse(data=summary)


async def _check_demo_rate_limit(ip: str, max_per_minute: int = 10) -> bool:
    return await allow_identity_action(
        action="public:voice_demo",
        who=ip,
        limit=max_per_minute,
        window=60.0,
    )


@router.post("/voice-demo/session", status_code=503)
async def create_voice_demo_session(
    payload: VoiceDemoSessionCreate,
    request: Request,
) -> None:
    """Fail closed until a public, provider-backed demo session is implemented."""
    del payload
    ip = trusted_client_ip(request)
    if not await _check_demo_rate_limit(ip, max_per_minute=10):
        raise HTTPException(status_code=429, detail="Too many demo sessions. Please wait a moment.")
    raise HTTPException(
        status_code=503,
        detail={
            "code": "voice_demo_not_configured",
            "message": "No provider-backed public voice demo is configured; no session was created.",
        },
    )


@router.post("/voice-demo/session/{session_id}/event", status_code=501)
async def post_voice_demo_event(
    session_id: str,
    payload: VoiceDemoEventIn,
) -> None:
    """Do not accept client-authored state transitions as provider events."""
    del session_id, payload
    raise HTTPException(
        status_code=501,
        detail={
            "code": "voice_demo_events_not_implemented",
            "message": "A verified server-side demo session is required before events can be accepted.",
        },
    )


@router.get("/voice-demo/session/{session_id}", status_code=501)
async def get_voice_demo_session(session_id: str) -> None:
    """Do not serve worker-local demo session state."""
    del session_id
    raise HTTPException(
        status_code=501,
        detail={
            "code": "voice_demo_sessions_not_implemented",
            "message": "No durable provider-backed public demo session store is configured.",
        },
    )


@router.get("/health", response_model=dict)
async def public_health(request: Request) -> Dict[str, Any]:
    """Report this endpoint's liveness without implying DB/cache/provider readiness."""
    return {
        "status": "ok",
        "scope": "public-home API process response",
        "service": "public-home",
        "at": _now_iso(),
        "registered_api_operations": len(_registered_api_operations(request.app)),
        "database": "not_checked",
        "cache": "not_checked",
        "providers": "not_checked",
    }
