from __future__ import annotations
import math
import pytest
from app.billing import cost as billing_cost
from app.observability.cost import normalize_cost


def test_normalize_uses_existing_configured_provider_prices(monkeypatch):
    monkeypatch.setattr(billing_cost, "_prices", lambda: {"voice_minute": 1300})
    result = normalize_cost("voice_minute", 2.0)
    assert result["cost_usd"] == 0.026
    assert result["status"] == "recorded_cost"


def test_missing_price_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(billing_cost, "_prices", lambda: {})
    result = normalize_cost("llm_token", 500, provider="openai")
    assert result["cost_usd"] is None
    assert result["status"] == "NOT_AVAILABLE"


def test_cost_rejects_non_finite_measured_units():
    with pytest.raises(ValueError):
        normalize_cost("voice_minute", math.nan)
