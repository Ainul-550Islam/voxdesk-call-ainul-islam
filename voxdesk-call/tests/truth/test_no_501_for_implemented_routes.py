# File: tests/truth/test_no_501_for_implemented_routes.py — a route that is
# implemented must never answer 501.
"""Truth guard for the routes this delivery implemented.

A 501 is a promise that the operation is not available. Once the operation is
real, a surviving 501 is worse than a missing route: the dashboard greys the
control out, the platform matrix records the feature as absent, and nothing
anywhere says the work was done.

These assertions are deliberately about *behaviour*, not about a snapshot of
status codes:

* the mounted route's endpoint is not a stub;
* the module that implements it contains no unconditional 501 for it;
* an unauthenticated request is refused for auth reasons, never 501;
* the Twilio adapter overrides every control operation the base class refuses,
  so the live carrier path is real.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from httpx import AsyncClient

import app.db.models  # noqa: F401
from app.main import app

# (path template, method) of every route PHASE 1 implemented. ``{}`` stands for
# a path parameter; it is filled with a random UUID when the endpoint is called.
IMPLEMENTED = [
    ("/api/agent/voice-profiles", "GET"),
    ("/api/agent/voice-profiles", "POST"),
    ("/api/agent/voice-profiles/{profile_id}", "GET"),
    ("/api/agent/voice-profiles/{profile_id}/activate", "POST"),
    ("/api/agent/voice-profiles/{profile_id}/deactivate", "POST"),
    ("/api/agent/voice-profiles/{profile_id}", "DELETE"),
    ("/api/translation/jobs", "GET"),
    ("/api/translation/jobs/{execution_id}", "GET"),
    ("/api/translation/jobs/{execution_id}/export", "GET"),
    ("/api/translations/jobs", "GET"),
    ("/api/translations/jobs/{execution_id}", "GET"),
    ("/api/translations/jobs/{execution_id}/export", "GET"),
    ("/api/calls/{call_id}/pause", "POST"),
    ("/api/calls/{call_id}/resume", "POST"),
    ("/api/calls/{call_id}/dtmf/batch", "POST"),
    ("/api/calls/{call_id}/transfer/cancel", "POST"),
    ("/api/calls/{call_id}/transfer/whisper", "POST"),
    ("/api/calls/transfers/bulk", "POST"),
    ("/api/workflows/calls/{call_id}/outcome-writeback", "POST"),
]

#: Modules written for these operations. A blanket 501 in one of them would
#: mean a stub survived the implementation. The pre-existing route modules are
#: not listed: they still carry deliberate 501s for the legacy mock and Telnyx
#: dial paths, which is correct and is asserted separately.
NEW_MODULES = [
    "app.api.voice_profile_routes",
    "app.telephony.call_control_service",
]


def _mounted_routes() -> list[tuple[str, str, object]]:
    rows: list[tuple[str, str, object]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            rows.append((path, method, getattr(route, "endpoint", None)))
    return rows


def test_every_implemented_route_is_mounted() -> None:
    mounted = {(path, method) for path, method, _endpoint in _mounted_routes()}
    missing = [row for row in IMPLEMENTED if row not in mounted]
    assert missing == [], f"routes not mounted: {missing}"


def test_no_implemented_route_is_a_stub() -> None:
    """A stub raises ``NotImplementedError`` or is a bare placeholder."""
    for path, method, endpoint in _mounted_routes():
        if (path, method) not in IMPLEMENTED:
            continue
        assert endpoint is not None, f"{method} {path} has no endpoint"
        source = inspect.getsource(endpoint)
        assert "NotImplementedError" not in source, f"{method} {path} is still a stub"
        assert "raise HTTPException(status_code=501" not in source, (
            f"{method} {path} refuses with 501"
        )


def test_the_new_modules_contain_no_blanket_501() -> None:
    """501 is only legal as a per-provider capability refusal.

    That is what ``UNSUPPORTED_CAPABILITY`` is for, and it is raised for one
    provider at a time — never as a blanket answer for a route.
    """
    import importlib

    offenders: list[str] = []
    for name in NEW_MODULES:
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        if "501" in source and "UNSUPPORTED_CAPABILITY" not in source:
            offenders.append(name)
    assert offenders == [], f"blanket 501 in: {offenders}"


def test_the_legacy_501s_are_left_alone() -> None:
    """MOCK provider is rejected with 422 while real carriers route through runtime."""
    from fastapi import HTTPException
    from app.api.outbound_call_routes import OutboundProvider, _provider_from_request

    # corrected: legacy outbound route now rejects MOCK with 422 and routes Twilio/Telnyx/Vonage through versioned runtime
    with pytest.raises(HTTPException) as exc_info:
        _provider_from_request("mock")
    assert exc_info.value.status_code == 422
    assert _provider_from_request("telnyx") == OutboundProvider.TELNYX
    assert _provider_from_request("vonage") == OutboundProvider.VONAGE


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "method"), IMPLEMENTED)
async def test_an_unauthenticated_request_is_never_501(client: AsyncClient, path: str, method: str) -> None:
    """Auth is checked first, so the refusal is 401/403/422 — never 501."""
    concrete = "/".join(
        str(uuid.uuid4()) if segment.startswith("{") else segment for segment in path.split("/")
    )
    response = await client.request(method, concrete, json={})
    assert response.status_code != 501, f"{method} {concrete} answered 501"


@pytest.mark.asyncio
async def test_reopening_a_call_is_refused_with_409_not_501(client: AsyncClient) -> None:
    """Reopen is deliberately not implementable; the refusal says so."""
    response = await client.post(f"/api/calls/{uuid.uuid4()}/reopen", json={})
    assert response.status_code in (401, 403, 404, 409)
    assert response.status_code != 501


@pytest.mark.asyncio
async def test_a_client_authored_transfer_completion_is_refused_with_403(client: AsyncClient) -> None:
    """Completion may only come from the provider callback path."""
    response = await client.post(f"/api/calls/{uuid.uuid4()}/transfer/complete", json={})
    assert response.status_code in (401, 403, 404)
    assert response.status_code != 501


@pytest.mark.asyncio
async def test_a_client_authored_transfer_failure_is_refused_with_403(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/calls/{uuid.uuid4()}/transfer/fail", params={"reason": "caller hung up"}
    )
    assert response.status_code in (401, 403, 404, 422)
    assert response.status_code != 501


# -------------------------------------------------------------- providers ---

#: The control operations the base adapter refuses by default. Twilio is the
#: live carrier and must override every one of them.
CONTROL_OPERATIONS = [
    "hold_call",
    "resume_call",
    "cancel_transfer",
    "send_whisper_to_leg",
]


def test_twilio_overrides_every_control_operation_the_base_refuses() -> None:
    from app.telephony.providers.base import TelephonyAdapter
    from app.telephony.providers.twilio import TwilioAdapter

    for name in CONTROL_OPERATIONS:
        base = getattr(TelephonyAdapter, name)
        override = getattr(TwilioAdapter, name)
        assert override is not base, (
            f"TwilioAdapter.{name} still refuses with UNSUPPORTED_CAPABILITY"
        )


def test_the_base_adapter_refuses_with_unsupported_capability() -> None:
    """The refusal names the capability, so a client can grey the control out."""
    import asyncio

    from app.providers.errors import ProviderCapabilityError
    from app.telephony.providers.telnyx import TelnyxAdapter

    adapter = TelnyxAdapter()
    for name in ("hold_call", "resume_call", "cancel_transfer"):
        coro = getattr(adapter, name)("CA0000000000000000000000000000")
        with pytest.raises(ProviderCapabilityError) as raised:
            asyncio.run(coro)
        assert "UNSUPPORTED_CAPABILITY" in str(raised.value)


def test_twilio_declares_the_control_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With live credentials present, both control operations are confirmed."""
    from app.core.config import settings
    from app.telephony.providers.twilio import TwilioAdapter

    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "f" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", "test-auth-token")
    capabilities = TwilioAdapter().capabilities()
    assert capabilities.allows_control("supports_hold") is True
    assert capabilities.allows_control("supports_cancel_transfer") is True


def test_the_capability_names_are_known_to_the_capability_set() -> None:
    """``allows_control`` rejects a name nobody declared.

    A typo in a capability name would otherwise read as a silent ``False`` and
    disable a control that actually works.
    """
    from app.telephony.capabilities import CONTROL_KNOWN, CapabilitySet

    assert CONTROL_KNOWN.issuperset({"supports_hold", "supports_cancel_transfer"})
    assert CapabilitySet().allows_control("supports_hold") is False
    # An undeclared name is not silently answered either way.
    assert CapabilitySet().allows_control("supports_teleportation") is False
