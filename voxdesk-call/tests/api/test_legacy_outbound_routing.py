"""Behavioural tests for legacy ``/api/calls`` outbound route -> versioned runtime & MOCK->422 (Capability 8)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import outbound_call_routes as routes
from app.db.models import Agent, AgentVersion, Call, Environment
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
            "custom_fields": {"customer_name": "Dana"},
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

    legacy_call = await db.scalar(select(Call).where(Call.call_sid == resp.json()["call_sid"]))
    assert legacy_call is not None
    # Twilio's versioned runtime creates this globally unique legacy row before
    # the compatibility route returns; the route must enrich and return that
    # same durable record rather than attempting a duplicate call_sid insert.
    assert legacy_call.id == uuid.UUID(resp.json()["id"])
    assert legacy_call.agent_id == agent.id
    assert legacy_call.agent_version_id is not None
    assert legacy_call.transfer_context["dynamic_variables"]["customer_name"] == "Dana"
    assert legacy_call.transfer_context["dynamic_variables"]["call_type"] == "outbound"


@pytest.mark.asyncio
async def test_direct_twilio_call_accepts_dual_recording_and_returns_durable_ids(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env, agent = await _setup_outbound_tenant_and_agent(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    headers["Idempotency-Key"] = f"pstn-contract-{uuid.uuid4().hex[:16]}"
    observed: dict[str, Any] = {}

    class FakeCallResource:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            observed.update(kwargs)
            return SimpleNamespace(sid=f"CA{uuid.uuid4().hex[:32]}")

    async def no_op(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(routes, "_check_rate_limit", no_op)
    monkeypatch.setattr(routes, "_check_dnc", no_op)
    monkeypatch.setattr(routes, "_check_dialer_policy", no_op)
    monkeypatch.setattr(routes, "_require_provider_ready", lambda _provider: "https://voxdesk.example.test")
    monkeypatch.setattr(
        "app.telephony.outbound._twilio_client",
        lambda: SimpleNamespace(calls=FakeCallResource()),
    )

    async def no_event(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr("app.webhooks.call_event_bridge.publish_call_event", no_event)
    response = await client.post(
        "/api/calls",
        headers=headers,
        json={
            "to": "+14155552671",
            "from_number": tenant_a.twilio_number,
            "provider": "twilio",
            "timeout_seconds": 30,
            "record": True,
            "recording_channels": "dual",
            "custom_fields": {"campaign": "dual-audio-test"},
        },
    )

    assert response.status_code == 201, response.text
    call = response.json()
    assert call["id"]
    assert call["call_sid"].startswith("CA")
    assert observed["record"] is True
    assert observed["recording_channels"] == "dual"
    assert observed["status_callback_event"] == ["initiated", "ringing", "answered", "completed"]
    assert observed["url"] == "https://voxdesk.example.test/telephony/outbound-answer"
    assert observed["status_callback"] == "https://voxdesk.example.test/telephony/status"
    call_row = await db.scalar(select(Call).where(Call.call_sid == call["call_sid"]))
    assert call_row is not None
    assert call_row.agent_id == agent.id
    assert call_row.agent_version_id is not None
    assert call_row.transfer_context["dynamic_variables"]["campaign"] == "dual-audio-test"
