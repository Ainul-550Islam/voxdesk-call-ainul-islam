"""Cost normalization from measured UsageEvent data into existing ROI persistence."""
from __future__ import annotations

import math
import re
from app.billing.cost import CostLine, cost_line, record_cost

_VALID_RESOURCES = frozenset({"voice_minute", "sms_segment", "llm_token", "tts_character"})
_UNITS = {"voice_minute": "minute", "sms_segment": "segment", "llm_token": "token", "tts_character": "character"}
_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CALCULATION_VERSION = "runtime-cost-v2"


def normalize_cost(resource: str, units: int | float, *, provider: str | None = None) -> dict:
    """Normalize measured units against configured operator prices; no price is invented."""
    if resource not in _VALID_RESOURCES:
        raise ValueError("unsupported measured cost resource")
    if isinstance(units, bool) or not isinstance(units, (int, float)) or not math.isfinite(float(units)) or units < 0:
        raise ValueError("measured units must be a finite non-negative number")
    line: CostLine = cost_line(resource, float(units), provider=provider)
    return {"resource": resource, "units": line.units, "unit_price_millicents": line.price_millicents, "rate": (line.price_millicents / 100_000.0) if line.price_millicents is not None else None, "cost_usd": line.cost_usd, "currency": "USD" if line.known else None, "status": "recorded_cost" if line.known else "NOT_AVAILABLE", "provider": provider}


def record_measured_cost(resource: str, units: int | float, *, provider: str | None = None) -> dict:
    normalized = normalize_cost(resource, units, provider=provider)
    line = record_cost(resource, float(units), provider=provider)
    return normalized | {"metric_recorded": float(units) > 0, "price_known": line.known}


def normalize_usage_event_cost(
    usage_event,
    *,
    scope,
    resource: str,
    provider: str | None = None,
    currency: str | None = "USD",
) -> dict:
    """Build required provenance fields from a scoped persisted usage row."""
    if usage_event.tenant_id != scope.tenant_id or usage_event.environment_id != scope.environment_id:
        raise PermissionError("usage event is outside the active tenant/environment scope")
    if resource not in _VALID_RESOURCES:
        raise ValueError("unsupported measured cost resource")
    quantity = usage_event.quantity
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(float(quantity)) or quantity < 0:
        raise ValueError("persisted usage quantity must be finite and non-negative")
    period = str(usage_event.billing_period or "")
    if not _PERIOD.fullmatch(period):
        raise ValueError("persisted usage event has no valid billing period")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3,8}", currency):
        return {
            "source_type": "usage_event", "provider": provider, "quantity": float(quantity),
            "unit": _UNITS[resource], "rate": None, "currency": None, "period": period,
            "tenant_id": str(scope.tenant_id), "environment_id": str(scope.environment_id),
            "source_event_id": str(usage_event.id), "calculation_version": CALCULATION_VERSION,
            "amount": None, "data_quality": "NOT_AVAILABLE", "reason": "currency is not recorded",
        }
    measured_units = float(quantity) / 60.0 if resource == "voice_minute" else float(quantity)
    normalized = normalize_cost(resource, measured_units, provider=provider)
    rate = normalized["rate"]
    amount = normalized["cost_usd"]
    ledger = (usage_event.event_metadata or {}).get("recorded_costs", {})
    recorded = ledger.get({"voice_minute": "telephony_cost", "sms_segment": "telephony_cost", "llm_token": "llm_cost", "tts_character": "tts_cost"}[resource]) if isinstance(ledger, dict) else None
    if not isinstance(recorded, dict) or recorded.get("amount") != amount or str(recorded.get("currency", "")).upper() != currency.upper():
        amount, rate = None, None
        quality, reason = "NOT_AVAILABLE", "matching measured cost is not present in the immutable usage-event metadata"
    elif amount is None or rate is None:
        quality, reason = "NOT_AVAILABLE", "operator rate is not configured"
    else:
        quality, reason = "MEASURED", None
    return {
        "source_type": "usage_event", "provider": provider or recorded.get("provider"),
        "quantity": float(quantity), "unit": _UNITS[resource], "rate": rate,
        "currency": currency.upper() if amount is not None else None, "period": period,
        "tenant_id": str(scope.tenant_id), "environment_id": str(scope.environment_id),
        "source_event_id": str(usage_event.id), "calculation_version": CALCULATION_VERSION,
        "amount": amount, "data_quality": quality, "reason": reason,
    }


async def persist_usage_event_cost(session, scope, actor_id, usage_event, *, component: str, resource: str, provider: str | None = None):
    """Project an already-recorded cost into ROI; ROIService enforces source idempotency."""
    currency_entry = None
    ledger = (usage_event.event_metadata or {}).get("recorded_costs", {})
    key = {"voice_minute": "telephony_cost", "sms_segment": "telephony_cost", "llm_token": "llm_cost", "tts_character": "tts_cost"}.get(resource)
    if isinstance(ledger, dict) and key and isinstance(ledger.get(key), dict):
        currency_entry = ledger[key].get("currency")
    item = normalize_usage_event_cost(usage_event, scope=scope, resource=resource, provider=provider, currency=currency_entry)
    if item["data_quality"] != "MEASURED":
        return None, item
    from app.roi.service import ROIService
    service = ROIService(session, scope, actor_id)
    row = await service.record_cost(period=item["period"], component=component, amount=item["amount"], currency=item["currency"], source_reference=item["source_event_id"])
    return row, item
