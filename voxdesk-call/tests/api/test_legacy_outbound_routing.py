"""Behavioural tests for legacy ``/api/calls`` outbound route -> versioned runtime & MOCK->422 (Capability 8)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import outbound_call_routes as routes
from app.db.models import Agent, AgentVersion, Environment
from app.db.telephony_models import TelephonyCallSession
from app.telephony.providers.base import CallResult
from tests.conftest import auth_headers


async def _setup_outbound_tenant_and_agent(
    db: AsyncSession, tenant
) -> tuple[Environment, Agent]:
    env = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id,
            Environment.kind == "production",
        )
    )
    assert env is not None
    tenant.outbound_enabled = True
    if not tenant.twilio_number:
        tenant.twilio_number = "+14155550100"

    agent = Agent(
        tenant_id=tenant.id,
        environment_id=env.id,
        external_key=f"out_{uuid.uuid4().hex[:10]}",
        name="Legacy Routed Agent",
        agent_type="voice",
        status="published",
        current_draft_config={
            "greeting": "Hello from versioned runtime!",
            "system_prompt": "Be helpful.",
            "voice_id": "v1",
        },
    )
    db.add(agent)
    await db.flush()
    av = AgentVersion(
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_snapshot=dict(agent.current_draft_config),
        config_hash="hash1",
    )
    db.add(av)
    await db.flush()
    agent.published_version_id = av.id
    agent.published_version_number = 1
    await db.commit()
    await db.refresh(agent)
    return env, agent


@pytest.mark.asyncio
async def test_legacy_outbound_rejects_mock_with_422(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup_outbound_tenant_and_agent(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    monkeypatch.setattr(routes, "_check_calling_window", lambda _t: None, raising=False)

    resp = await client.post(
        "/api/calls",
        headers=headers,
        json={
            "to": "+14155552671",
            "from_number": tenant_a.twilio_number,
            "provider": "mock",
            "idempotency_key": f"mock-rej-{uuid.uuid4().hex[:12]}",
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "telephony_mock_not_supported"


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["twilio", "telnyx", "vonage"])
async def test_legacy_outbound_routes_twilio_telnyx_vonage_through_versioned_runtime(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    carrier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, agent = await _setup_outbound_tenant_and_agent(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    monkeypatch.setattr(routes, "_check_calling_window", lambda _t: None, raising=False)

    dialed_providers: list[str] = []

    class FakeLiveAdapter:
        name = carrier
        supports_takeover = False

        def configured(self) -> bool:
            return True

        async def create_outbound(self, **kwargs) -> CallResult:
            dialed_providers.append(carrier)
            return CallResult(
                provider=carrier,
                external_id=f"{carrier}_ext_{uuid.uuid4().hex[:16]}",
                status="dialing",
                to_number=kwargs["to_number"],
                from_number=kwargs["from_number"],
            )

    monkeypatch.setattr(
        "app.telephony.runtime.get_provider_adapter",
        lambda _prov: FakeLiveAdapter(),
    )

    idem = f"legacy-{carrier}-{uuid.uuid4().hex[:12]}"
    resp = await client.post(
        "/api/calls",
        headers=headers,
        json={
            "to": "+14155552671",
            "from_number": tenant_a.twilio_number,
            "agent_id": str(agent.id),
            "provider": carrier,
            "idempotency_key": idem,
        },
    )
    assert resp.status_code == 201, resp.text
    assert dialed_providers == [carrier]

    # Verify versioned TelephonyCallSession was created by TelephonyRuntimeService
    sess = await db.scalar(
        select(TelephonyCallSession).where(
            TelephonyCallSession.tenant_id == tenant_a.id,
            TelephonyCallSession.provider == carrier.upper(),
            TelephonyCallSession.agent_id == str(agent.id),
        )
    )
    assert sess is not None
    assert sess.agent_version_number == 1
