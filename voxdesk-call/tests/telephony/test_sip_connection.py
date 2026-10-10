"""Unit and integration tests for SIP Trunk Configuration, Digest Auth, IP ACL & Invite Routing (Sub-Phase 2F)."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import Agent, AgentVersion
from app.telephony.enums import SipConnectionStatus, SipTransportProtocol, TelephonyProviderName
from app.telephony.exceptions import SipConfigurationError
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.schemas import SipConnectionCreateRequest, SipConnectionTestRequest
from app.telephony.sip import (
    SipConnectionService,
    check_sip_ip_acl,
    compute_sip_digest_response,
    verify_sip_digest_auth,
)
from tests.conftest import make_tenant


def test_sip_digest_auth_and_cidr_ip_acl():
    nonce = "dcd98b7102dd2f0e8b11d0f600bfb0c093"
    resp_hex = compute_sip_digest_response(
        username="trunk_user_1",
        realm="voxdesk.sip",
        password="super-secret-sip-pass",
        method="INVITE",
        uri="sip:+14155550155@sip.voxdesk.ai",
        nonce=nonce,
    )
    assert verify_sip_digest_auth(
        username="trunk_user_1",
        realm="voxdesk.sip",
        password="super-secret-sip-pass",
        method="INVITE",
        uri="sip:+14155550155@sip.voxdesk.ai",
        nonce=nonce,
        response_hex=resp_hex,
    )
    assert not verify_sip_digest_auth(
        username="trunk_user_1",
        realm="voxdesk.sip",
        password="wrong-password",
        method="INVITE",
        uri="sip:+14155550155@sip.voxdesk.ai",
        nonce=nonce,
        response_hex=resp_hex,
    )

    cidrs = ["192.0.2.0/24", "198.51.100.10/32"]
    assert check_sip_ip_acl("192.0.2.44", cidrs) is True
    assert check_sip_ip_acl("198.51.100.10", cidrs) is True
    assert check_sip_ip_acl("203.0.113.5", cidrs) is False


@pytest.mark.asyncio
async def test_sip_connection_lifecycle_and_inbound_invite_routing(db):
    tenant = await make_tenant(db, name="SIP BYOC Enterprise")
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="SIP Receptionist",
        external_key="sip-receptionist",
        status="published",
        current_draft_config={"system_prompt": "Hello over SIP."},
    )
    db.add(agent)
    await db.flush()

    ver = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        is_active=True,
        config_snapshot={"system_prompt": "Hello over SIP."},
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    agent.published_version_number = 1

    phone_row = PhoneNumber(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        e164="+14155550155",
        provider="sip",
        inbound_agent_id=agent.id,
    )
    db.add(phone_row)
    await db.flush()

    svc = SipConnectionService(db)
    created = await svc.create_sip_connection(
        tenant_id=tenant.id,
        payload=SipConnectionCreateRequest(
            name="Primary Cisco CUBE Trunk",
            provider=TelephonyProviderName.SIP,
            phone_number="+14155550155",
            termination_uri="sips:cube.enterprise.example.com:5061",
            origination_uri="sips:ingress.voxdesk.ai:5061",
            username="cube_trunk",
            password_secret="sip-pass-999",
            transport=SipTransportProtocol.TLS,
            metadata={"allowed_cidrs": ["192.0.2.0/24"]},
        ),
    )
    assert created.status == SipConnectionStatus.CONFIGURED.value
    assert created.has_credentials is True
    assert "sip-pass-999" not in (created.credential_reference or "")

    tested = await svc.test_sip_connection(
        tenant_id=tenant.id,
        sip_connection_id=created.id,
        payload=SipConnectionTestRequest(),
    )
    assert tested.status == SipConnectionStatus.READY.value

    nonce = "nonce-sip-12345"
    uri = "sip:+14155550155@ingress.voxdesk.ai"
    digest_hex = compute_sip_digest_response(
        username="cube_trunk",
        realm="voxdesk.sip",
        password="sip-pass-999",
        method="INVITE",
        uri=uri,
        nonce=nonce,
    )

    routed = await svc.route_inbound_sip_invite(
        to_uri=uri,
        from_uri="sip:+14155550199@cube.enterprise.example.com",
        sip_call_id="SIP-CALL-ID-777@cube",
        source_ip="192.0.2.50",
        digest_response={
            "realm": "voxdesk.sip",
            "method": "INVITE",
            "uri": uri,
            "nonce": nonce,
            "response": digest_hex,
        },
        expected_password="sip-pass-999",
    )
    assert routed["ok"] is True
    assert routed["agent_id"] == str(agent.id)
    assert routed["serializer"] == "SIPBridgeFrameSerializer"

    # Unauthorized source IP must fail closed with 403
    with pytest.raises(SipConfigurationError) as exc_info:
        await svc.route_inbound_sip_invite(
            to_uri=uri,
            from_uri="sip:+14155550199@attacker.example.com",
            sip_call_id="SIP-CALL-ID-888@attacker",
            source_ip="203.0.113.99",
        )
    assert exc_info.value.status_code == 403
