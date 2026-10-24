"""The SIP capability flag: the gateway is probed through its readiness
endpoint and the feature is only offered when the gateway answers."""

from __future__ import annotations

import pytest

from app.telephony.provider_errors import ProviderTimeoutError
from app.telephony.sip.sip_capabilities import (
    REASON_GATEWAY_NOT_READY,
    REASON_GATEWAY_UNREACHABLE,
    REASON_NOT_CONFIGURED,
    REASON_READY,
    REASON_TOKEN_INVALID,
    probe_sip_gateway,
    sip_media_capabilities,
)


@pytest.mark.asyncio
async def test_probe_reports_not_configured() -> None:
    capability = await probe_sip_gateway(base_url="", token="")
    assert capability.ready is False
    assert capability.reason == REASON_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_probe_reports_an_invalid_token() -> None:
    capability = await probe_sip_gateway(
        base_url="http://gateway.test:8080", token="change-me"
    )
    assert capability.ready is False
    assert capability.reason == REASON_TOKEN_INVALID


@pytest.mark.asyncio
async def test_probe_reports_an_unreachable_gateway() -> None:
    async def unreachable(*_args, **_kwargs):
        raise ProviderTimeoutError("timeout", provider="sip")

    capability = await probe_sip_gateway(
        base_url="http://gateway.test:8080", token="secret", transport=unreachable
    )
    assert capability.ready is False
    assert capability.reason == REASON_GATEWAY_UNREACHABLE


@pytest.mark.asyncio
async def test_probe_reports_a_gateway_that_is_not_ready() -> None:
    async def not_ready(*_args, **_kwargs):
        return 503, {"ready": False}

    capability = await probe_sip_gateway(
        base_url="http://gateway.test:8080", token="secret", transport=not_ready
    )
    assert capability.ready is False
    assert capability.reason == REASON_GATEWAY_NOT_READY


@pytest.mark.asyncio
async def test_probe_reports_a_ready_gateway() -> None:
    async def ready(*_args, **_kwargs):
        return 200, {"ready": True}

    capability = await probe_sip_gateway(
        base_url="http://gateway.test:8080", token="secret", transport=ready
    )
    assert capability.ready is True
    assert capability.reason == REASON_READY
    assert capability.supports_ai_media_stream is True


def test_static_capability_row_is_honest_without_a_gateway() -> None:
    row = sip_media_capabilities()["sip"]
    assert row["supported"] is False
    assert row["serializer"] is None


