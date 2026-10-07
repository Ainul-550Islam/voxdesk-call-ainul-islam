"""Unified runtime signals over the existing Prometheus registry and structured logs."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Iterator

from app.core import observability
from app.core import metrics
from app.core.logging import log


@dataclass(frozen=True)
class RuntimeObservation:
    component: str
    ok: bool
    configured: bool
    latency_ms: float | None = None
    reason: str | None = None


def observe_dependency(component: str, *, ok: bool, configured: bool, latency_ms: float | None = None, reason: str | None = None) -> RuntimeObservation:
    allowed = {"database", "redis", "deepgram", "elevenlabs", "llm", "deployment_adapter", "artifact_registry"}
    key = component if component in allowed else "deployment_adapter"
    if key == "database":
        metrics.set_db_up(ok)
    elif key == "redis":
        observability.set_redis_up(ok)
    metrics.record_runtime_event("provider" if key in {"deepgram", "elevenlabs", "llm"} else "deployment" if key in {"deployment_adapter", "artifact_registry"} else "api", "dependency", "success" if ok else "unavailable")
    log.info("runtime.dependency_observed", component=key, ok=bool(ok), configured=bool(configured), latency_ms=round(latency_ms, 2) if latency_ms is not None else None, reason=reason)
    return RuntimeObservation(key, bool(ok), bool(configured), latency_ms, reason)


def observe_runtime_event(component: str, event: str, outcome: str, *, duration_ms: float | None = None, reason_code: str | None = None, **safe_ids) -> None:
    """Record a bounded metric plus structured correlation fields; never log provider text."""
    bounded_component = component if component in metrics.RUNTIME_COMPONENTS else "api"
    bounded_event = event if event in metrics.RUNTIME_EVENTS else "execution"
    bounded_outcome = outcome if outcome in metrics.RUNTIME_OUTCOMES else "other"
    seconds = duration_ms / 1000.0 if duration_ms is not None and duration_ms >= 0 else None
    metrics.record_runtime_event(bounded_component, bounded_event, bounded_outcome, duration_seconds=seconds)
    allowed_ids = {key: str(value)[:128] for key, value in safe_ids.items() if key in {"tenant_id", "environment_id", "request_id", "trace_id", "execution_id", "revision_id", "job_id"} and value is not None}
    log.info("runtime.event", component=bounded_component, event_name=bounded_event, outcome=bounded_outcome, duration_ms=round(duration_ms, 2) if duration_ms is not None else None, reason_code=(reason_code or "")[:64] or None, **allowed_ids)


def observe_provider_error(provider: str, exc: BaseException) -> None:
    providers = {"deepgram", "elevenlabs", "openai", "anthropic", "google", "llm", "unknown"}
    categories = {"configuration_error", "authentication_error", "authorization_error", "rate_limit", "timeout", "unavailable", "invalid_request", "unsupported_feature", "provider_error"}
    provider = provider if provider in providers else "unknown"
    category = getattr(exc, "category", "provider_error")
    category = category if category in categories else "provider_error"
    metrics.PROVIDER_ERRORS.labels(provider, category).inc()
    observe_runtime_event("provider", "execution", "failure", reason_code=category)
    log.warning("provider.error", provider=provider, category=category, error_type=type(exc).__name__)


@contextmanager
def timed_event(component: str, event: str, **context) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    except Exception as exc:
        observe_runtime_event(component, event, "failure", duration_ms=(perf_counter() - started) * 1000, reason_code=getattr(exc, "category", type(exc).__name__), **context)
        raise
    else:
        observe_runtime_event(component, event, "success", duration_ms=(perf_counter() - started) * 1000, **context)


class TimedObservation:
    def __init__(self):
        self.started = perf_counter()

    def latency_ms(self) -> float:
        return (perf_counter() - self.started) * 1000
