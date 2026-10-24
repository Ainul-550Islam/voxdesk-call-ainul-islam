"""Live checks against the running SIP gateway.

Every test here is marked ``live``. Without a configured gateway the module
skips. The checks are read-only probes of the control API: readiness,
liveness, and the authenticated/unauthenticated boundary of the control
plane. No call is placed, so the live suite never spends money.
"""

from __future__ import annotations

import pytest

from app.carriers_v2.settings import get_carrier_v2_settings

pytestmark = pytest.mark.live


def _require_gateway() -> tuple[str, str]:
    settings = get_carrier_v2_settings()
    url = (settings.sip_gateway_url or "").strip()
    token = (settings.sip_gateway_token or "").strip()
    if not url or not token:
        pytest.skip(
            "the live SIP gateway is not configured (SIP_GATEWAY_URL, SIP_GATEWAY_TOKEN)"
        )
    return url, token


async def test_the_gateway_reports_itself_ready() -> None:
    """The gateway must answer the readiness probe with a real answer."""
    url, token = _require_gateway()

    from app.telephony.sip.sip_gateway_client import build_gateway_client

    client = build_gateway_client()
    body = await client.ready()
    assert isinstance(body, dict)
    assert body.get("status") in {"ready", "ok", "serving"}, body


async def test_the_gateway_reports_itself_alive() -> None:
    """The liveness probe must answer as well."""
    _require_gateway()

    from app.telephony.sip.sip_gateway_client import build_gateway_client

    client = build_gateway_client()
    body = await client.health()
    assert isinstance(body, dict)
    assert body.get("status") in {"ok", "ready", "serving"}, body


async def test_the_control_api_refuses_a_request_without_a_token() -> None:
    """The control plane must not answer an unauthenticated caller."""
    url, _token = _require_gateway()

    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{url.rstrip('/')}/calls/not-a-real-call")
    assert response.status_code in {401, 403}, response.text[:300]


async def test_the_control_api_refuses_an_unknown_call() -> None:
    """An authenticated caller gets a clean 404 for a call that never existed."""
    _require_gateway()

    from app.telephony.provider_errors import ProviderNotFoundError
    from app.telephony.sip.sip_gateway_client import build_gateway_client

    client = build_gateway_client()
    with pytest.raises(ProviderNotFoundError):
        await client.call_status("00000000-0000-4000-8000-000000000000")
