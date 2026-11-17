"""Authenticated per-INVITE SIP tenant/trunk resolution."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api import sip_trunk_routes
from app.db.telephony_models import TelephonyPhoneNumber, TelephonySipConnection
from app.telephony.enums import SipConnectionStatus
from app.telephony.sip.sip_gateway_client import SipGatewayClient
from tests.conftest import make_tenant


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/internal/telephony/sip-inbound-route",
            "raw_path": b"/internal/telephony/sip-inbound-route",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "server": ("api.internal", 443),
            "client": ("sip-gateway", 8080),
        }
    )


def _payload(**overrides) -> sip_trunk_routes.InboundSIPRouteRequest:
    values = {
        "to_uri": '"Support" <sip:+15550001111@ingress.example>;tag=to-tag',
        "from_uri": "<sip:+15559998888@carrier.example>;tag=from-tag",
        "call_id": "sip-call-0001@carrier.example",
        "source_ip": "192.0.2.44",
    }
    values.update(overrides)
    return sip_trunk_routes.InboundSIPRouteRequest(**values)


@pytest.mark.asyncio
async def test_internal_route_requires_the_gateway_bearer_before_database_access(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sip_trunk_routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="gateway-route-secret"),
    )
    with pytest.raises(HTTPException) as rejected:
        await sip_trunk_routes.sip_inbound_route(
            _payload(), _request("wrong-secret"), Response(), session=object()
        )
    assert rejected.value.status_code == 401
    assert rejected.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_internal_route_stays_disabled_until_tls_srtp_capability_is_verified(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sip_trunk_routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="gateway-route-secret"),
    )
    monkeypatch.setattr(SipGatewayClient, "supports_tenant_trunks", False)

    with pytest.raises(HTTPException) as rejected:
        await sip_trunk_routes.sip_inbound_route(
            _payload(), _request("gateway-route-secret"), Response(), session=object()
        )
    assert rejected.value.status_code == 503
    assert rejected.value.detail["code"] == "SIP_TENANT_TRUNK_UNSUPPORTED"


@pytest.mark.asyncio
async def test_route_binds_one_ready_sip_did_to_tenant_and_trunk(db, monkeypatch) -> None:
    from sqlalchemy import select

    from app.db.models import Environment

    tenant = await make_tenant(db, name="Inbound SIP Route Tenant")
    production_environment = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    trunk = TelephonySipConnection(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Verified test trunk",
        provider="sip",
        phone_number_e164="+15550001111",
        termination_uri="sips:carrier.example:5061",
        transport="TLS",
        status=SipConnectionStatus.READY.value,
        metadata_json={"allowed_cidrs": ["192.0.2.0/24"]},
    )
    db.add(trunk)
    await db.flush()
    phone = TelephonyPhoneNumber(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        number="+15550001111",
        e164_number="+15550001111",
        provider="sip",
        sip_connection_id=trunk.id,
        sip_enabled=True,
        inbound_enabled=True,
        status="READY",
    )
    db.add(phone)
    await db.commit()

    monkeypatch.setattr(
        sip_trunk_routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="gateway-route-secret"),
    )
    monkeypatch.setattr(SipGatewayClient, "supports_tenant_trunks", True)
    response = Response()
    result = await sip_trunk_routes.sip_inbound_route(
        _payload(), _request("gateway-route-secret"), response, session=db
    )

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert result == {
        "ok": True,
        "call_id": "sip-call-0001@carrier.example",
        "sip_call_id": "sip-call-0001@carrier.example",
        "tenant_id": str(tenant.id),
        "trunk_id": str(trunk.id),
        "environment_id": str(production_environment.id),
        "phone_number_id": str(phone.id),
        "from_number": "+15559998888",
        "to_number": "+15550001111",
    }

    # Resolution is side-effect free; the media WebSocket creates history only
    # after it repeats this binding and authenticates its own gateway session.
    from app.db.models import Call
    from sqlalchemy import select

    calls = (
        (await db.execute(select(Call).where(Call.call_sid == result["call_id"]))).scalars().all()
    )
    assert calls == []


@pytest.mark.asyncio
async def test_route_denies_unknown_or_non_allowlisted_sources(db, monkeypatch) -> None:
    tenant = await make_tenant(db, name="Restricted SIP Route Tenant")
    trunk = TelephonySipConnection(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Restricted test trunk",
        provider="sip",
        phone_number_e164="+15550001111",
        termination_uri="sips:carrier.example:5061",
        transport="TLS",
        status=SipConnectionStatus.READY.value,
        metadata_json={"allowed_cidrs": ["192.0.2.0/24"]},
    )
    db.add(trunk)
    db.add(
        TelephonyPhoneNumber(
            tenant_id=tenant.id,
            number="+15550001111",
            e164_number="+15550001111",
            provider="sip",
            sip_connection_id=trunk.id,
            sip_enabled=True,
            inbound_enabled=True,
            status="READY",
        )
    )
    await db.commit()
    monkeypatch.setattr(
        sip_trunk_routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="gateway-route-secret"),
    )
    monkeypatch.setattr(SipGatewayClient, "supports_tenant_trunks", True)

    with pytest.raises(HTTPException) as denied:
        await sip_trunk_routes.sip_inbound_route(
            _payload(source_ip="203.0.113.77"),
            _request("gateway-route-secret"),
            Response(),
            session=db,
        )
    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "SIP_IP_ACL_DENIED"


@pytest.mark.asyncio
async def test_route_rejects_an_environment_owned_by_another_tenant(db, monkeypatch) -> None:
    from sqlalchemy import select

    from app.db.models import Environment

    tenant = await make_tenant(db, name="SIP Route Environment Owner")
    other_tenant = await make_tenant(db, name="Foreign SIP Environment Owner")
    foreign_environment = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == other_tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    trunk = TelephonySipConnection(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        environment_id=foreign_environment.id,
        name="Cross-tenant environment trunk",
        provider="sip",
        phone_number_e164="+15550001111",
        termination_uri="sips:carrier.example:5061",
        transport="TLS",
        status=SipConnectionStatus.READY.value,
        metadata_json={"allowed_cidrs": ["192.0.2.0/24"]},
    )
    db.add(trunk)
    db.add(
        TelephonyPhoneNumber(
            tenant_id=tenant.id,
            environment_id=foreign_environment.id,
            number="+15550001111",
            e164_number="+15550001111",
            provider="sip",
            sip_connection_id=trunk.id,
            sip_enabled=True,
            inbound_enabled=True,
            status="READY",
        )
    )
    await db.commit()
    monkeypatch.setattr(
        sip_trunk_routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="gateway-route-secret"),
    )
    monkeypatch.setattr(SipGatewayClient, "supports_tenant_trunks", True)

    with pytest.raises(HTTPException) as denied:
        await sip_trunk_routes.sip_inbound_route(
            _payload(), _request("gateway-route-secret"), Response(), session=db
        )
    assert denied.value.status_code == 503
    assert denied.value.detail["code"] == "SIP_ENVIRONMENT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_route_refuses_a_missing_per_connection_source_allowlist(db, monkeypatch) -> None:
    tenant = await make_tenant(db, name="SIP Route Without ACL")
    trunk = TelephonySipConnection(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Trunk without ACL",
        provider="sip",
        phone_number_e164="+15550001111",
        termination_uri="sips:carrier.example:5061",
        transport="TLS",
        status=SipConnectionStatus.READY.value,
        metadata_json={},
    )
    db.add(trunk)
    db.add(
        TelephonyPhoneNumber(
            tenant_id=tenant.id,
            number="+15550001111",
            e164_number="+15550001111",
            provider="sip",
            sip_connection_id=trunk.id,
            sip_enabled=True,
            inbound_enabled=True,
            status="READY",
        )
    )
    await db.commit()
    monkeypatch.setattr(
        sip_trunk_routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="gateway-route-secret"),
    )
    monkeypatch.setattr(SipGatewayClient, "supports_tenant_trunks", True)

    with pytest.raises(HTTPException) as denied:
        await sip_trunk_routes.sip_inbound_route(
            _payload(), _request("gateway-route-secret"), Response(), session=db
        )
    assert denied.value.status_code == 403
    assert denied.value.detail["code"] == "SIP_SOURCE_ACL_REQUIRED"
