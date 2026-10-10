"""Deterministic failure injection (`app/core/chaos.py`).

Purpose: prove that SLO dashboards, alerts, incident runbooks, and active voice
calls handle faults deterministically during a drill, load test, or resilience
test.

* **No randomness by default.** HTTP rules (`CHAOS_RULES`) match exact paths
  with a fixed effect (`latency_ms` or `error`). Runtime target faults
  (`FaultConfig`) deterministically inject latency, errors, or connection drops
  for named targets (`deepgram`, `elevenlabs`, `llm_primary`, `database`,
  `redis`, etc.).
* **Production-disabled.** `add_chaos_middleware` and `chaos.inject(...)` are
  no-ops when `settings.is_production` is True.
* **No secrets, no side effects.** Injected HTTP responses return a fixed
  `{"detail": "injected failure"}` body; latency is a fixed `asyncio.sleep`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings

#: The only HTTP rule effects understood. Anything else is ignored, never guessed.
_EFFECTS = frozenset({"latency_ms", "error"})


class FaultType(str, Enum):
    """Supported runtime fault injection modes for voice/DB/Redis targets."""

    LATENCY = "latency"
    ERROR = "error"
    CONNECTION_DROP = "connection_drop"


@dataclass
class FaultConfig:
    """Configuration for a deterministic runtime fault on a named dependency."""

    fault_type: FaultType
    target: str
    probability: float = 1.0
    latency_ms: float = 0.0
    error_message: str = "injected failure"


class ChaosInjector:
    """In-process fault coordinator for provider, database, and Redis targets."""

    def __init__(self) -> None:
        self._enabled: bool = False
        self._faults: dict[str, FaultConfig] = {}
        self._total_injected: int = 0

    @property
    def is_active(self) -> bool:
        if settings.is_production:
            return False
        return bool(self._enabled or settings.chaos_enabled)

    def add_fault(self, config: FaultConfig) -> None:
        if settings.is_production:
            return
        self._faults[config.target] = config

    def remove_fault(self, target: str) -> None:
        self._faults.pop(target, None)

    def clear(self) -> None:
        self._faults.clear()
        self._total_injected = 0

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.is_active,
            "active_faults": sorted(self._faults.keys()),
            "total_injected": self._total_injected,
        }

    async def inject(self, target: str) -> None:
        """Execute any configured fault for ``target``. No-op in production."""
        if not self.is_active:
            return
        fault = self._faults.get(target)
        if fault is None:
            rule = _matching_rule(target)
            if rule is None:
                return
            effect = rule.get("effect")
            if effect == "latency_ms":
                fault = FaultConfig(
                    fault_type=FaultType.LATENCY,
                    target=target,
                    latency_ms=float(rule.get("value", 0) or 0),
                )
            elif effect == "error":
                fault = FaultConfig(
                    fault_type=FaultType.ERROR,
                    target=target,
                    error_message="injected failure",
                )
            else:
                return

        if fault.probability <= 0.0:
            return

        self._total_injected += 1
        if fault.fault_type is FaultType.LATENCY:
            ms = max(0.0, float(fault.latency_ms))
            if ms > 0:
                await asyncio.sleep(ms / 1000.0)
            return
        if fault.fault_type is FaultType.CONNECTION_DROP:
            raise ConnectionError(fault.error_message or f"injected connection drop: {target}")
        if fault.fault_type is FaultType.ERROR:
            raise RuntimeError(fault.error_message or f"injected error: {target}")


#: Module-level fault injector singleton used by runtime probes and chaos tests.
chaos = ChaosInjector()


def _matching_rule(path: str) -> dict | None:
    for rule in settings.chaos_rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("effect") not in _EFFECTS:
            continue
        if rule.get("path") == path:
            return rule
    return None


async def _apply(request: Request, call_next, rule: dict):
    effect = rule.get("effect")
    if effect == "latency_ms":
        try:
            ms = max(0.0, float(rule.get("value", 0)))
        except (TypeError, ValueError):
            ms = 0.0
        if ms:
            await asyncio.sleep(ms / 1000.0)
        return await call_next(request)
    if effect == "error":
        try:
            status = int(rule.get("value", 503))
        except (TypeError, ValueError):
            status = 503
        return JSONResponse(
            status_code=status, content={"detail": "injected failure"}
        )
    return await call_next(request)


def add_chaos_middleware(app: FastAPI) -> None:
    """Attach the injection middleware. A no-op outside test environments."""
    if not settings.chaos_enabled or settings.is_production:
        return

    @app.middleware("http")
    async def _chaos_middleware(request: Request, call_next):
        rule = _matching_rule(request.url.path)
        if rule is None:
            return await call_next(request)
        return await _apply(request, call_next, rule)
