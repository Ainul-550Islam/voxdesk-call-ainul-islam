"""Prometheus metrics for VoxDesk.

Exposes:

    voxdesk_http_requests_total{method,path,status}
    voxdesk_http_request_duration_seconds{method,path}
    voxdesk_active_calls            (gauge, tracked by the telephony layer)
    voxdesk_provider_errors_total{provider,category}
    voxdesk_db_up                   (gauge, 1/0)
    voxdesk_voice_e2e_latency_seconds{tenant_plan,llm_provider,tts_provider}
    voxdesk_voice_ttfb_seconds{stage,provider}
    voxdesk_voice_interruptions_total{tenant_plan}

The /metrics endpoint is disabled unless METRICS_ENABLED=true. When
METRICS_TOKEN is set the scrape must present it (Bearer or ?token=), which
keeps the endpoint off the public internet while the Prometheus server in the
compose network scrapes it.
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.core.config import settings

REQUESTS = Counter(
    "voxdesk_http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
DURATION = Histogram(
    "voxdesk_http_request_duration_seconds",
    "Request latency",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
ACTIVE_CALLS = Gauge("voxdesk_active_calls", "Live voice calls in flight")
#: Voice-provider failures, labelled by provider (closed set: deepgram /
#: elevenlabs / openai / anthropic / google / llm / unknown) and category
#: (the closed ProviderError.CATEGORIES set). Bounded by construction — never
#: label it with anything open-ended like a call SID or a model string.
PROVIDER_ERRORS = Counter(
    "voxdesk_provider_errors_total",
    "Voice-provider (STT/TTS/LLM) failures by provider and category",
    ["provider", "category"],
)
DB_UP = Gauge("voxdesk_db_up", "Database reachability (1/0)")

# ---- Voice Runtime Latency & Interruption Metrics (2A) ----
VOICE_LATENCY_BUCKETS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.5, 2.0, 3.0)
VOICE_STAGES = frozenset({"stt", "llm", "tts"})
VOICE_PLANS = frozenset({"free", "starter", "growth", "pro", "business", "enterprise", "unknown"})
VOICE_PROVIDER_LABELS = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "groq",
        "azure_openai",
        "azure",
        "bedrock",
        "elevenlabs",
        "cartesia",
        "playht",
        "deepgram",
        "assemblyai",
        "whisper",
        "polly",
        "gemini_live",
        "openai_realtime",
        "custom",
        "unknown",
    }
)

VOICE_E2E_LATENCY = Histogram(
    "voxdesk_voice_e2e_latency_seconds",
    "End-to-end voice turn latency from caller speech end to first bot audio frame",
    ["tenant_plan", "llm_provider", "tts_provider"],
    buckets=VOICE_LATENCY_BUCKETS,
)
VOICE_TTFB = Histogram(
    "voxdesk_voice_ttfb_seconds",
    "Per-stage time-to-first-byte in seconds across STT, LLM, and TTS",
    ["stage", "provider"],
    buckets=VOICE_LATENCY_BUCKETS,
)
VOICE_INTERRUPTIONS = Counter(
    "voxdesk_voice_interruptions_total",
    "Voice barge-in / user interruptions during assistant speech",
    ["tenant_plan"],
)


def _norm_plan(plan: str | None) -> str:
    cleaned = (plan or "unknown").strip().lower()
    return cleaned if cleaned in VOICE_PLANS else "unknown"


def _norm_provider(provider: str | None) -> str:
    cleaned = (provider or "unknown").strip().lower()
    return cleaned if cleaned in VOICE_PROVIDER_LABELS else "unknown"


def observe_voice_e2e_latency(
    seconds: float,
    *,
    tenant_plan: str | None = "unknown",
    llm_provider: str | None = "unknown",
    tts_provider: str | None = "unknown",
) -> None:
    """Record a single turn's end-to-end voice latency in seconds."""
    if seconds < 0:
        return
    VOICE_E2E_LATENCY.labels(
        tenant_plan=_norm_plan(tenant_plan),
        llm_provider=_norm_provider(llm_provider),
        tts_provider=_norm_provider(tts_provider),
    ).observe(float(seconds))


def observe_voice_ttfb(
    stage: str,
    seconds: float,
    *,
    provider: str | None = "unknown",
) -> None:
    """Record a stage TTFB measurement (stage in {'stt', 'llm', 'tts'})."""
    stage_norm = (stage or "").strip().lower()
    if stage_norm not in VOICE_STAGES or seconds < 0:
        return
    VOICE_TTFB.labels(
        stage=stage_norm,
        provider=_norm_provider(provider),
    ).observe(float(seconds))


def record_voice_interruption(
    *,
    tenant_plan: str | None = "unknown",
    n: int = 1,
) -> None:
    """Increment the barge-in / interruption counter."""
    if n <= 0:
        return
    VOICE_INTERRUPTIONS.labels(tenant_plan=_norm_plan(tenant_plan)).inc(n)


# Runtime lifecycle signals share this Prometheus registry and bounded labels.
RUNTIME_COMPONENTS = frozenset({"api", "ai", "workflow", "review", "specialized_job", "provider", "deployment", "queue"})
RUNTIME_EVENTS = frozenset({"execution", "preflight", "apply", "observation", "verification", "cost", "backlog", "dependency"})
RUNTIME_OUTCOMES = frozenset({"started", "success", "failure", "retrying", "unavailable", "not_verified", "observed", "other"})
RUNTIME_EVENT_COUNT = Counter("voxdesk_runtime_events_total", "Bounded runtime lifecycle events", ["component", "event", "outcome"])
RUNTIME_EVENT_DURATION = Histogram("voxdesk_runtime_event_duration_seconds", "Runtime execution duration", ["component", "event"], buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120, 900))
REVIEW_BACKLOG = Gauge("voxdesk_review_backlog", "Current count of open review cases")

#: External side-effect lifecycle (Step 6, scale-compliance). Both labels are
#: bounded closed sets -- `kind` is a fixed vocabulary and `outcome` is a fixed
#: vocabulary -- so this can never mint unbounded Prometheus series the way a
#: tenant id or call SID label would.
SIDE_EFFECT_KINDS = frozenset({
    "sms",              # Twilio SMS send (reminders / owner notifications)
    "outbound_call",    # Twilio outbound dial
    "reminder",         # reminder delivery (claim/lease + send)
    "crm_sync",         # CRM delivery worker
    "message_webhook",  # inbound SMS/WhatsApp webhook
    "knowledge_ingest", # knowledge document ingestion / embedding
})
SIDE_EFFECT_OUTCOMES = frozenset({
    "attempt", "success", "failure", "retry", "duplicate", "reconciled",
})
SIDE_EFFECTS = Counter(
    "voxdesk_external_side_effects_total",
    "External side-effect lifecycle by kind and outcome",
    ["kind", "outcome"],
)

PROVIDER_FAILOVER_TOTAL = Counter(
    "voxdesk_provider_failover_total",
    "Count of voice provider failovers by stage and provider pair",
    ["stage", "from_provider", "to_provider"],
)

IVR_NAVIGATION_TOTAL = Counter(
    "voxdesk_ivr_navigation_total",
    "Count of IVR navigation outcomes (digits_pressed, speech_spoken, wait, human_detected, goal_completed, fallback)",
    ["result"],
)


def record_provider_failover(
    stage: str, from_provider: str, to_provider: str, *, n: int = 1
) -> None:
    """Increment the provider failover counter (Sub-Phase 2C)."""
    PROVIDER_FAILOVER_TOTAL.labels(
        stage=str(stage or "unknown").lower(),
        from_provider=str(from_provider or "unknown").lower(),
        to_provider=str(to_provider or "unknown").lower(),
    ).inc(n)


def record_ivr_navigation(result: str, *, n: int = 1) -> None:
    """Increment `voxdesk_ivr_navigation_total{result}` (Sub-Phase 2D)."""
    IVR_NAVIGATION_TOTAL.labels(result=str(result or "unknown").lower()).inc(n)


def record_side_effect(kind: str, outcome: str, *, n: int = 1) -> None:
    """Bump a side-effect counter. Unknown labels are dropped, never added."""
    if kind not in SIDE_EFFECT_KINDS or outcome not in SIDE_EFFECT_OUTCOMES:
        return
    SIDE_EFFECTS.labels(kind=kind, outcome=outcome).inc(n)

_SKIP_PREFIXES = ("/metrics", "/health")

# High-cardinality paths collapse into a small set of labels.
_PATH_LABELS = {
    "/api/tenants": "/api/tenants",
    "/auth/login": "/auth/login",
    "/auth/refresh": "/auth/refresh",
    "/telephony/voice": "/telephony/voice",
    "/telephony/status": "/telephony/status",
    "/telephony/ws": "/telephony/ws",
    "/channels/message": "/channels/message",
    "/health/ready": "/health/ready",
}


def _label_for(path: str) -> str:
    return _PATH_LABELS.get(path, "other")


def add_metrics_middleware(app: FastAPI) -> None:
    if not settings.metrics_enabled:
        return

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        if request.url.path.startswith(_SKIP_PREFIXES):
            return await call_next(request)
        label = _label_for(request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            REQUESTS.labels(request.method, label, response.status_code).inc()
            return response
        finally:
            DURATION.labels(request.method, label).observe(
                time.perf_counter() - started
            )


def _scrape_authorized(request: Request) -> bool:
    if not settings.metrics_token:
        return True
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == settings.metrics_token
    return request.query_params.get("token") == settings.metrics_token


def add_metrics_endpoint(app: FastAPI) -> None:
    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        if not settings.metrics_enabled:
            return Response(status_code=404, content="metrics disabled")
        if not _scrape_authorized(request):
            return Response(status_code=401, content="unauthorized")
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_runtime_event(component: str, event: str, outcome: str = "other", *, duration_seconds: float | None = None) -> None:
    component = component if component in RUNTIME_COMPONENTS else "api"
    event = event if event in RUNTIME_EVENTS else "execution"
    outcome = outcome if outcome in RUNTIME_OUTCOMES else "other"
    RUNTIME_EVENT_COUNT.labels(component, event, outcome).inc()
    if duration_seconds is not None and duration_seconds >= 0:
        RUNTIME_EVENT_DURATION.labels(component, event).observe(duration_seconds)


def set_review_backlog(value: int) -> None:
    REVIEW_BACKLOG.set(max(0, int(value)))


def set_db_up(value: bool) -> None:
    DB_UP.set(1 if value else 0)
