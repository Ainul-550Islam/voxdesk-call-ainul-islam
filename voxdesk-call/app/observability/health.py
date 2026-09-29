"""Runtime dependency health, separated from static configuration capabilities."""
from __future__ import annotations

import asyncio
from pathlib import Path
from time import perf_counter

from app.core import health as existing_health
from app.core.config import settings
from app.core.dependency_health import configuration_health
from app.observability.runtime import observe_dependency
from app.jobs.types import registered_job_types


def _state(configured: bool, reachable: bool | None = None) -> str:
    if not configured:
        return "not_configured"
    if reachable is None:
        return "not_verified"
    return "healthy" if reachable else "unavailable"


async def dependency_health() -> dict:
    config = configuration_health()
    checks: dict[str, dict] = {}
    started = perf_counter()
    db_ok = await existing_health.check_database()
    db_latency = round((perf_counter() - started) * 1000, 2)
    checks["database"] = {"state": _state(config["configured"]["database"], db_ok), "configured": config["configured"]["database"], "importable": True, "reachable": db_ok, "latency_ms": db_latency}
    observe_dependency("database", ok=db_ok, configured=config["configured"]["database"], latency_ms=db_latency, reason=None if db_ok else "database probe failed")

    if config["configured"]["redis"]:
        started = perf_counter()
        try:
            redis_ok = bool(await asyncio.wait_for(existing_health.check_redis(), timeout=2.0))
        except Exception:
            redis_ok = False
        redis_latency = round((perf_counter() - started) * 1000, 2)
        checks["cache"] = {"state": _state(True, redis_ok), "configured": True, "reachable": redis_ok, "latency_ms": redis_latency}
        observe_dependency("redis", ok=redis_ok, configured=True, latency_ms=redis_latency, reason=None if redis_ok else "cache probe failed")
    else:
        checks["cache"] = {"state": "not_configured", "configured": False, "reachable": None, "reason": "process-local cache mode"}

    storage_configured = config["configured"]["object_storage"]
    if settings.knowledge_storage_backend == "local":
        path = Path(settings.knowledge_local_path)
        local_ok = path.exists() and path.is_dir()
        checks["storage"] = {"state": _state(storage_configured, local_ok), "configured": storage_configured, "backend": "local", "reachable": local_ok, "reason": None if local_ok else "configured local storage directory is unavailable"}
    else:
        checks["storage"] = {"state": "not_verified" if storage_configured else "not_configured", "configured": storage_configured, "backend": "s3", "reachable": None, "reason": "S3 reachability is not probed by this endpoint"}

    queue_configured = bool(settings.database_url)
    handlers = registered_job_types()
    checks["queue"] = {"state": "not_verified" if queue_configured else "not_configured", "configured": queue_configured, "registered_handlers": len(handlers), "worker_available": "not_verified", "reason": "worker process presence is not observable from this API process"}

    provider_config = existing_health.provider_config_ok()
    provider_matrix = config["sdk_capabilities"]
    checks["providers"] = {
        "state": "degraded" if provider_config["missing"] and settings.is_production else "not_verified",
        "configured": config["configured"],
        "sdk_capabilities": provider_matrix,
        "reachability": {name: "not_checked" for name in provider_matrix},
        "authenticated": {name: "not_checked" for name in provider_matrix},
        "missing_credentials": provider_config["missing"],
        "reason": "No provider connectivity/authentication probes are configured.",
    }
    checks["deployment_adapters"] = config["deployment"]
    required = [checks["database"], checks["cache"]]
    failures = any(item["state"] == "unavailable" for item in required)
    if settings.is_production and provider_config["missing"]:
        failures = True
    overall = "unavailable" if failures else "degraded" if any(item.get("state") in {"not_verified", "not_configured", "degraded"} for item in checks.values()) else "healthy"
    return {"status": "ok" if overall == "healthy" else "degraded", "state": overall, "checks": checks, "external_reachability_verified": False}
