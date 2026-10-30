"""Behavioural tests for ``SipOutboundAdapter`` (Capability 7)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderValidationError,
)
from app.telephony.runtime import get_provider_adapter
from app.telephony.sip.sip_outbound import SipOutboundAdapter


@pytest.mark.asyncio
async def test_sip_outbound_missing_config_raises_clean_not_configured() -> None:
    adapter = SipOutboundAdapter(base_url="", token="")
    assert adapter.configured() is False
    assert adapter.capabilities().voice is False
    assert adapter.capabilities().source == "unconfirmed"
    configured = SipOutboundAdapter(base_url="https://sip-gw.internal:8443", token="tok-1")
    assert configured.capabilities().voice is True
    assert configured.capabilities().source == "provider_config"
    assert "NOT_CONFIGURED" in adapter.health().reason
    with pytest.raises(ProviderConfigurationError, match="NOT_CONFIGURED"):
        await adapter.create_outbound(
            to_number="+14155550101",
            from_number="+14155550100",
        )


@pytest.mark.asyncio
async def test_sip_outbound_posts_v1_calls_with_bearer_hmac_and_idempotency_key() -> None:
    captured: list[dict] = []

    async def fake_transport(method: str, url: str, *, headers: dict, json: dict | None):
        captured.append({"method": method, "url": url, "headers": headers, "json": json})
        return 201, {"id": "sip_call_991", "status": "ringing"}

    adapter = SipOutboundAdapter(
        base_url="https://sip-gw.internal:8443",
        token="bearer-tok-xyz",
        hmac_secret="hmac-sec-123",
        backoff_seconds=(0.0, 0.0, 0.0),
        transport=fake_transport,
    )
    result = await adapter.create_outbound(
        to_number="+14155550199",
        from_number="+14155550100",
        idempotency_key="idem-sip-001",
    )
    assert result.provider == "sip"
    assert result.external_id == "sip_call_991"
    assert len(captured) == 1
    req = captured[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://sip-gw.internal:8443/v1/calls"
    assert req["headers"]["Authorization"] == "Bearer bearer-tok-xyz"
    assert req["headers"]["Idempotency-Key"] == "idem-sip-001"
    ts = req["headers"]["X-VoxDesk-Timestamp"]
    body_sorted = json.dumps(req["json"], separators=(",", ":"), sort_keys=True)
    expected_sig = "sha256=" + hmac.new(
        b"hmac-sec-123",
        f"{ts}.POST./v1/calls.{body_sorted}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert req["headers"]["X-VoxDesk-Signature"] == expected_sig


@pytest.mark.asyncio
async def test_sip_outbound_retries_5xx_and_does_not_retry_4xx() -> None:
    attempts_5xx = 0

    async def flaky_5xx_transport(method: str, url: str, *, headers: dict, json: dict | None):
        nonlocal attempts_5xx
        del method, url, headers, json
        attempts_5xx += 1
        if attempts_5xx < 3:
            return 503, {"error": "gateway overloaded"}
        return 200, {"id": "sip_recovered_3", "status": "dialing"}

    adapter_5xx = SipOutboundAdapter(
        base_url="https://sip-gw.internal",
        token="tok-1",
        backoff_seconds=(0.0, 0.0, 0.0),
        transport=flaky_5xx_transport,
    )
    res = await adapter_5xx.create_outbound(
        to_number="+14155550199",
        from_number="+14155550100",
        idempotency_key="idem-retry-5xx",
    )
    assert attempts_5xx == 3
    assert res.external_id == "sip_recovered_3"

    attempts_4xx = 0

    async def client_4xx_transport(method: str, url: str, *, headers: dict, json: dict | None):
        nonlocal attempts_4xx
        del method, url, headers, json
        attempts_4xx += 1
        return 422, {"error": "invalid codec"}

    adapter_4xx = SipOutboundAdapter(
        base_url="https://sip-gw.internal",
        token="tok-1",
        backoff_seconds=(0.0, 0.0, 0.0),
        transport=client_4xx_transport,
    )
    with pytest.raises(ProviderValidationError):
        await adapter_4xx.create_outbound(
            to_number="+14155550199",
            from_number="+14155550100",
            idempotency_key="idem-no-retry-4xx",
        )
    assert attempts_4xx == 1

    # Runtime registry returns SipOutboundAdapter (not 501)
    runtime_adapter = get_provider_adapter("SIP")
    assert isinstance(runtime_adapter, SipOutboundAdapter)
