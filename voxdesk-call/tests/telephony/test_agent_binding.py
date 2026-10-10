"""Integration tests for PhoneNumber-to-Agent binding & multi-agent routing (Sub-Phase 2E)."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import (
    Agent,
    AgentVersion,
    Call,
    CallDirection,
    CallStatus,
    Tenant,
    UserRole,
)
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony.number_provisioning import (
    PhoneNumber,
    PhoneNumberStatus,
    PhoneNumberType,
)
from tests.conftest import auth_headers, make_user


async def _seed_tenant_with_two_agents(db):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Multi-Agent Dental Clinic",
        twilio_number="+14155550101",
        system_prompt_extra="Legacy fallback tenant prompt",
        greeting="Legacy fallback greeting",
        voice_id="legacy-voice",
    )
    db.add(tenant)
    await db.flush()

    user = await make_user(db, tenant, UserRole.OWNER)

    agent_a = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        external_key="agent-sales",
        name="Sales Receptionist",
        status="published",
        current_draft_config={
            "system_prompt": "You are the Sales Agent A.",
            "greeting": "Welcome to Sales!",
            "voice_id": "voice-sales-a",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        },
    )
    agent_b = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        external_key="agent-support",
        name="Support Specialist",
        status="published",
        current_draft_config={
            "system_prompt": "You are the Support Agent B.",
            "greeting": "Welcome to Support!",
            "voice_id": "voice-support-b",
            "llm_provider": "anthropic",
            "llm_model": "claude-haiku-4-5-20251001",
        },
    )
    db.add_all([agent_a, agent_b])
    await db.flush()

    ver_a1 = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent_a.id,
        version_number=1,
        status="published",
        is_active=True,
        config_snapshot={
            "system_prompt": "You are the Sales Agent A (v1).",
            "greeting": "Welcome to Sales v1!",
            "voice_id": "voice-sales-a-v1",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        },
    )
    ver_a2 = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent_a.id,
        version_number=2,
        status="published",
        is_active=True,
        config_snapshot={
            "system_prompt": "You are the Sales Agent A (v2).",
            "greeting": "Welcome to Sales v2!",
            "voice_id": "voice-sales-a-v2",
            "llm_provider": "openai",
            "llm_model": "gpt-4o",
        },
    )
    ver_b1 = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent_b.id,
        version_number=1,
        status="published",
        is_active=True,
        config_snapshot={
            "system_prompt": "You are the Support Agent B (v1).",
            "greeting": "Welcome to Support v1!",
            "voice_id": "voice-support-b-v1",
            "llm_provider": "anthropic",
            "llm_model": "claude-haiku-4-5-20251001",
        },
    )
    db.add_all([ver_a1, ver_a2, ver_b1])
    await db.flush()

    agent_a.published_version_id = ver_a2.id
    agent_a.published_version_number = 2
    agent_b.published_version_id = ver_b1.id
    agent_b.published_version_number = 1

    num_1 = PhoneNumber(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        e164="+14155550101",
        friendly_name="Sales Line",
        country_code="US",
        number_type=PhoneNumberType.LOCAL,
        provider="twilio",
        provider_sid="PN_SALES_001",
        status=PhoneNumberStatus.ACTIVE,
        is_primary=True,
    )
    num_2 = PhoneNumber(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        e164="+14155550102",
        friendly_name="Support Line",
        country_code="US",
        number_type=PhoneNumberType.LOCAL,
        provider="twilio",
        provider_sid="PN_SUPPORT_002",
        status=PhoneNumberStatus.ACTIVE,
        is_primary=False,
    )
    db.add_all([num_1, num_2])
    await db.commit()
    return tenant, user, agent_a, agent_b, ver_a1, ver_a2, ver_b1, num_1, num_2


@pytest.mark.asyncio
async def test_patch_phone_number_binds_agents_and_routes_calls_distinctly(client, db):
    (
        tenant,
        user,
        agent_a,
        agent_b,
        ver_a1,
        ver_a2,
        ver_b1,
        num_1,
        num_2,
    ) = await _seed_tenant_with_two_agents(db)

    headers = await auth_headers(client, user)

    # Bind num_1 to Agent A (pinned to version 1)
    r1 = await client.patch(
        f"/api/v1/telephony/phone-numbers/{num_1.id}",
        headers=headers,
        json={
            "inbound_agent_id": str(agent_a.id),
            "inbound_agent_version": 1,
            "outbound_agent_id": str(agent_a.id),
        },
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["inbound_agent_id"] == str(agent_a.id)
    assert body1["inbound_agent_version"] == 1
    assert body1["outbound_agent_id"] == str(agent_a.id)

    # Bind num_2 to Agent B (latest published version)
    r2 = await client.patch(
        f"/api/v1/telephony/phone-numbers/{num_2.id}",
        headers=headers,
        json={
            "inbound_agent_id": str(agent_b.id),
            "outbound_agent_id": str(agent_b.id),
        },
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["inbound_agent_id"] == str(agent_b.id)
    assert body2["inbound_agent_version"] is None

    # Inbound call to num_1 (+14155550101) -> resolves Agent A v1
    call_1 = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_BIND_001",
        from_number="+14155559999",
        to_number="+14155550101",
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call_1)
    await db.flush()
    cfg_1 = await resolve_runtime_config(db, call_1, tenant)
    assert cfg_1.agent_id == agent_a.id
    assert cfg_1.agent_version_id == ver_a1.id
    assert cfg_1.system_prompt == "You are the Sales Agent A (v1)."
    assert cfg_1.voice_id == "voice-sales-a-v1"
    assert call_1.agent_id == agent_a.id
    assert call_1.agent_version_id == ver_a1.id

    # Inbound call to num_2 (+14155550102) -> resolves Agent B v1
    call_2 = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_BIND_002",
        from_number="+14155559999",
        to_number="+14155550102",
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call_2)
    await db.flush()
    cfg_2 = await resolve_runtime_config(db, call_2, tenant)
    assert cfg_2.agent_id == agent_b.id
    assert cfg_2.agent_version_id == ver_b1.id
    assert cfg_2.system_prompt == "You are the Support Agent B (v1)."
    assert cfg_2.llm_provider == "anthropic"
    assert call_2.agent_id == agent_b.id
    assert call_2.agent_version_id == ver_b1.id


@pytest.mark.asyncio
async def test_patch_phone_number_rejects_cross_tenant_agent(client, db):
    (
        tenant,
        user,
        agent_a,
        _agent_b,
        _ver_a1,
        _ver_a2,
        _ver_b1,
        num_1,
        _num_2,
    ) = await _seed_tenant_with_two_agents(db)

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Tenant",
        twilio_number="+14155550999",
    )
    db.add(other_tenant)
    await db.flush()

    foreign_agent = Agent(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        external_key="foreign-agent",
        name="Foreign Agent",
        status="published",
    )
    db.add(foreign_agent)
    await db.commit()

    headers = await auth_headers(client, user)

    res = await client.patch(
        f"/api/v1/telephony/phone-numbers/{num_1.id}",
        headers=headers,
        json={"inbound_agent_id": str(foreign_agent.id)},
    )
    status = res.status_code
    assert status in (403, 404), res.text
