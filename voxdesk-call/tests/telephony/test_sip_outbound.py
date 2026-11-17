"""Behavioural tests for ``SipOutboundAdapter`` (Capability 7)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from app.telephony.runtime import get_provider_adapter
from app.telephony.sip.sip_outbound import SipOutboundAdapter
from app.telephony.qos import DisconnectionReason
from app.telephony.sip.sip_gateway_client import SipGatewayClient
from app.telephony.sip_outbound import (
    SipOriginator,
    TrunkConfig,
    TrunkConfigError,
    describe_failure,
    parse_destination,
)


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
async def test_sip_outbound_does_not_retry_ambiguous_mutations_or_client_errors() -> None:
    attempts_5xx = 0

    async def flaky_5xx_transport(method: str, url: str, *, headers: dict, json: dict | None):
        nonlocal attempts_5xx
        del method, url, headers, json
        attempts_5xx += 1
        return 503, {"error": "gateway overloaded"}

    adapter_5xx = SipOutboundAdapter(
        base_url="https://sip-gw.internal",
        token="tok-1",
        backoff_seconds=(0.0, 0.0, 0.0),
        transport=flaky_5xx_transport,
    )
    with pytest.raises(ProviderUnavailableError):
        await adapter_5xx.create_outbound(
            to_number="+14155550199",
            from_number="+14155550100",
            idempotency_key="idem-retry-5xx",
        )
    assert attempts_5xx == 1

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

    attempts_timeout = 0

    async def timed_out_transport(method: str, url: str, *, headers: dict, json: dict | None):
        nonlocal attempts_timeout
        del method, url, headers, json
        attempts_timeout += 1
        raise ProviderTimeoutError("gateway response timed out", provider="sip")

    adapter_timeout = SipOutboundAdapter(
        base_url="https://sip-gw.internal",
        token="tok-1",
        backoff_seconds=(0.0, 0.0, 0.0),
        transport=timed_out_transport,
    )
    with pytest.raises(ProviderTimeoutError):
        await adapter_timeout.create_outbound(
            to_number="+14155550199",
            from_number="+14155550100",
            idempotency_key="idem-ambiguous-timeout",
        )
    assert attempts_timeout == 1

    # Runtime registry returns SipOutboundAdapter (not 501)
    runtime_adapter = get_provider_adapter("SIP")
    assert isinstance(runtime_adapter, SipOutboundAdapter)


# ===========================================================================
# PART 13 / P13-01 — SipOriginator (the trunk-aware control-plane path)
# ===========================================================================
#
# `SipOutboundAdapter` above is the dialect of the gateway's own API.
# `SipOriginator` is the layer that turns a tenant's `SipTrunk` row into a
# request: it validates the trunk, classifies the destination and maps the SIP
# final response back to a disconnection reason. These tests keep the fake at
# the transport boundary and assert on what the gateway is asked to do.

class _RecordingGateway:
    def __init__(self, response: dict | None = None, healthy: bool = True) -> None:
        self.requests: list[dict] = []
        self.response = response or {"call_id": "gw-call-1", "sip_status": 200}
        self.healthy = healthy

    async def originate(self, **kwargs):
        self.requests.append(kwargs)
        return self.response

    async def health(self) -> dict:
        return {"ok": self.healthy, "trunk": "registered"}


def _trunk(**overrides) -> TrunkConfig:
    base = dict(
        host="sip.carrier.example",
        port=5061,
        transport="tls",
        srtp_required=True,
        caller_id="+15550100",
        auth_username="carrier-user",
        auth_secret_ref="secret://telephony/trunk/acme",
        tenant_id="tenant-acme",
        trunk_id="trunk-acme",
        environment_id="env-prod",
    )
    base.update(overrides)
    return TrunkConfig(**base)


def test_a_trunk_without_a_credential_reference_is_refused() -> None:
    with pytest.raises(TrunkConfigError) as refused:
        _trunk(auth_secret_ref=None).validate()
    assert refused.value.field == "auth_secret_ref"


@pytest.mark.asyncio
async def test_production_gateway_refuses_unimplemented_tenant_trunk_routing() -> None:
    gateway = SipGatewayClient(base_url="http://sip-gateway:8080", token="test-token")
    with pytest.raises(TrunkConfigError, match="tenant-scoped trunk routing"):
        await SipOriginator(gateway).originate(
            trunk=_trunk(), to_number="+15550199", idempotency_key="must-not-dial"
        )


def test_plain_rtp_is_refused_outside_the_lab() -> None:
    # Over UDP/TCP the media law is the caller's choice, so plain RTP is the
    # thing being refused here.
    with pytest.raises(TrunkConfigError, match="plain RTP"):
        _trunk(transport="udp", srtp_required=False).validate()
    # The lab flag exists so the compose stack can run without SRTP.
    _trunk(transport="udp", srtp_required=False, allow_plain_rtp=True).validate()


def test_a_plaintext_password_cannot_be_mistaken_for_a_secret_reference() -> None:
    with pytest.raises(TrunkConfigError, match="opaque secret:// reference"):
        _trunk(auth_secret_ref="carrier-password-plaintext").validate()


def test_trunk_api_rejects_plaintext_passwords_in_reference_field() -> None:
    from pydantic import ValidationError

    from app.api.sip_trunk_routes import TrunkCreate

    with pytest.raises(ValidationError, match="opaque secret:// reference"):
        TrunkCreate(
            name="Carrier",
            host="sip.carrier.example",
            auth_secret_ref="carrier-password-plaintext",
        )


def test_tls_signalling_demands_srtp_media() -> None:
    with pytest.raises(TrunkConfigError) as refused:
        _trunk(transport="tls", srtp_required=False, allow_plain_rtp=True).validate()
    assert refused.value.field == "srtp_required"


def test_a_bad_transport_is_refused() -> None:
    with pytest.raises(TrunkConfigError, match="udp/tcp/tls"):
        _trunk(transport="carrier-pigeon").validate()


def test_an_e164_caller_id_is_required() -> None:
    with pytest.raises(TrunkConfigError, match="E.164"):
        _trunk(caller_id="555-0100").validate()


def test_the_trunk_dict_never_contains_a_password() -> None:
    payload = _trunk().as_dict()
    assert "password" not in payload
    assert payload["auth_secret_ref"].startswith("secret://")


def test_a_destination_is_either_e164_or_a_sip_uri() -> None:
    assert parse_destination("+15550199") == ("e164", "+15550199")
    assert parse_destination("sip:desk@pbx.example.com")[0] == "sip"
    with pytest.raises(TrunkConfigError):
        parse_destination("5550199")


@pytest.mark.asyncio
async def test_origination_returns_the_provider_call_id() -> None:
    gateway = _RecordingGateway()
    result = await SipOriginator(gateway).originate(
        trunk=_trunk(), to_number="+15550199", idempotency_key="idem-1"
    )
    assert result.provider_call_id == "gw-call-1"
    assert result.connected is True
    assert result.reason is DisconnectionReason.NORMAL
    assert gateway.requests[0]["to_number"] == "+15550199"
    assert gateway.requests[0]["idempotency_key"] == "idem-1"
    assert gateway.requests[0]["tenant_id"] == "tenant-acme"
    assert gateway.requests[0]["trunk_id"] == "trunk-acme"
    assert gateway.requests[0]["environment_id"] == "env-prod"


@pytest.mark.asyncio
async def test_the_trunk_caller_id_is_used_when_the_request_has_none() -> None:
    gateway = _RecordingGateway()
    await SipOriginator(gateway).originate(trunk=_trunk(), to_number="+15550199")
    assert gateway.requests[0]["from_number"] == "+15550100"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sip_status", "expected"),
    [
        (404, DisconnectionReason.INVALID_DESTINATION),
        (403, DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED),
        (503, DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE),
        (486, DisconnectionReason.USER_BUSY),
        (603, DisconnectionReason.USER_DECLINED),
        (606, DisconnectionReason.MARKED_AS_SPAM),
    ],
)
async def test_a_final_response_becomes_a_disconnection_reason(
    sip_status: int, expected: DisconnectionReason
) -> None:
    gateway = _RecordingGateway({"call_id": "", "sip_status": sip_status})
    result = await SipOriginator(gateway).originate(trunk=_trunk(), to_number="+15550199")
    assert result.connected is False
    assert result.reason is expected
    assert describe_failure(result) == {
        DisconnectionReason.INVALID_DESTINATION: "The destination number does not exist",
        DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED: (
            "The trunk credentials or caller ID were rejected"
        ),
        DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE: (
            "The carrier could not complete the call"
        ),
        DisconnectionReason.USER_BUSY: "The destination was busy",
        DisconnectionReason.USER_DECLINED: "The destination declined the call",
        DisconnectionReason.MARKED_AS_SPAM: "The call was marked as spam",
    }[expected]


@pytest.mark.asyncio
async def test_the_ping_reports_an_unhealthy_gateway() -> None:
    result = await SipOriginator(_RecordingGateway(healthy=False)).ping(_trunk())
    assert result["ok"] is False
    assert result["host"] == "sip.carrier.example"
    assert result["trunk_reached"] is False


@pytest.mark.asyncio
async def test_gateway_health_does_not_claim_the_carrier_trunk_is_ready() -> None:
    result = await SipOriginator(_RecordingGateway()).ping(_trunk())
    assert result["ok"] is True
    assert result["trunk_reached"] is False
    assert result["probe_scope"] == "gateway_health_only"


@pytest.mark.asyncio
async def test_a_gateway_without_health_does_not_claim_to_be_up() -> None:
    class _NoHealth:
        async def originate(self, **kwargs):  # pragma: no cover - not used here
            return {}

    result = await SipOriginator(_NoHealth()).ping(_trunk())
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# SIPp assets and the disposable Go gateway stack
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sipp_scenarios_are_present_and_well_formed() -> None:
    """SIPp scenarios are repository assets; parse them without a live stack."""
    from pathlib import Path
    import xml.etree.ElementTree as ElementTree

    scenario_dir = Path(__file__).with_name("sipp")
    scenarios = sorted(scenario_dir.glob("*.xml"))
    assert len(scenarios) >= 6, [path.name for path in scenarios]
    for path in scenarios:
        tree = ElementTree.parse(path)
        assert tree.getroot().tag == "scenario", path.name
        assert path.read_text(encoding="utf-8").count("<recv") >= 1, path.name


@pytest.mark.unit
def test_the_compose_file_declares_the_disposable_gateway_stack() -> None:
    """The Go gateway is SIP/RTP; the media peer is test-only, not the app."""
    from pathlib import Path

    compose = Path(__file__).resolve().parents[2] / "docker-compose.sip.yml"
    text = compose.read_text(encoding="utf-8")
    for service in ("media-test", "sip-gateway", "sipp", "bridge"):
        assert f"\n  {service}:" in text, service
    assert "VOXDESK_SIP_GATEWAY_TOKEN" in text
    assert "VOXDESK_SIP_MEDIA_WS_URL" in text
    assert "python-bridge" in text


@pytest.mark.stack
def test_sip_compose_config_is_valid_when_docker_is_installed() -> None:
    """Check Compose parsing only; actual SIPp calls are not exercised here."""
    import shutil
    import subprocess
    from pathlib import Path

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is not installed; SIP stack validation is opt-in")
    compose = Path(__file__).resolve().parents[2] / "docker-compose.sip.yml"
    result = subprocess.run(
        [docker, "compose", "-f", str(compose), "config", "--quiet"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
