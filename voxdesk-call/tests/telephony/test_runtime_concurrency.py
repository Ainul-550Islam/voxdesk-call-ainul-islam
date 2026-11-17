from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Agent, AgentVersion, Call, Environment
from app.db.telephony_models import TelephonyCallSession, TelephonySipConnection
from app.telephony import concurrency_policy
from app.telephony.exceptions import TelephonyConfigurationError, TelephonyRuntimeError
from app.telephony.providers.base import CallResult
from app.telephony.runtime import TelephonyRuntimeService
from app.telephony.schemas import OutboundCallCreateRequest
from app.telephony.concurrency_policy import Limit, save_limit
from app.telephony.enums import (
    SipConnectionStatus,
    TelephonyCallState,
    TelephonyProviderName,
)


async def _seed_runtime_agent(db, tenant):
    environment = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id,
            Environment.kind == "production",
        )
    )
    assert environment is not None
    agent = Agent(
        tenant_id=tenant.id,
        environment_id=environment.id,
        external_key=f"concurrency-{uuid.uuid4().hex[:10]}",
        name="Concurrency Test Agent",
        status="published",
        current_draft_config={
            "greeting": "Hello.",
            "system_prompt": "Be concise and helpful.",
            "voice_id": "test-voice",
        },
    )
    db.add(agent)
    await db.flush()
    version = AgentVersion(
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_snapshot=dict(agent.current_draft_config),
        published_environment_id=environment.id,
    )
    db.add(version)
    await db.flush()
    agent.published_version_id = version.id
    agent.published_version_number = 1
    tenant.outbound_enabled = True
    await db.commit()
    return environment, agent


@pytest.mark.asyncio
async def test_outbound_runtime_leases_and_releases_slots(db, tenant_a, monkeypatch):
    environment, agent = await _seed_runtime_agent(db, tenant_a)
    tenant_id = tenant_a.id
    environment_id = environment.id
    agent_id = str(agent.id)
    await save_limit(db, tenant_id, Limit(base=1, reserved_inbound=0))
    await db.commit()
    concurrency_policy.configure_counter_store(concurrency_policy.InMemoryCounterStore())
    answer_urls: list[str] = []
    status_urls: list[str] = []

    class FakeAdapter:
        def configured(self) -> bool:
            return True

        async def create_outbound(self, *, to_number: str, from_number: str, **kwargs):
            answer_urls.append(str(kwargs.get("answer_url") or ""))
            status_urls.append(str(kwargs.get("status_callback_url") or ""))
            return CallResult(
                provider="twilio",
                external_id=f"CA{uuid.uuid4().hex[:32]}",
                status="dialing",
                to_number=to_number,
                from_number=from_number,
            )

    monkeypatch.setattr("app.telephony.runtime.get_provider_adapter", lambda _provider: FakeAdapter())
    service = TelephonyRuntimeService(db)
    created = []
    try:
        for index in range(3):
            result = await service.initiate_outbound_call(
                tenant_id=tenant_id,
                environment_id=environment_id,
                payload=OutboundCallCreateRequest(
                    to_number=f"+1415555010{index}",
                    from_number="+14155550999",
                    agent_id=agent_id,
                    provider=TelephonyProviderName.TWILIO,
                    idempotency_key=f"runtime-concurrency-{index}-{uuid.uuid4().hex[:8]}",
                ),
            )
            created.append(result)

        assert len(answer_urls) == len(created)
        assert len(status_urls) == len(created)
        for result, answer_url, status_url in zip(
            created, answer_urls, status_urls, strict=True
        ):
            assert f"call_session_id={result.id}" in answer_url
            assert f"call_session_id={result.id}" in status_url
            session_row = await db.get(TelephonyCallSession, result.id)
            assert session_row is not None
            legacy_call = await db.scalar(
                select(Call).where(Call.call_sid == result.provider_call_id)
            )
            assert legacy_call is not None
            assert session_row.legacy_call_id == legacy_call.id
            assert legacy_call.tenant_id == tenant_id
            assert legacy_call.environment_id == environment_id

        assert created[0].metadata["concurrency_lease"]["burst"] is False
        assert created[1].metadata["concurrency_lease"]["burst"] is True
        assert created[2].metadata["concurrency_lease"]["billable_overage"] is True
        snapshot = await concurrency_policy.get_concurrency_manager().snapshot(
            tenant_id, Limit(base=1, reserved_inbound=0)
        )
        assert snapshot["in_use"] == {"total": 3, "inbound": 0, "outbound": 3}

        with pytest.raises(TelephonyRuntimeError) as refused:
            await service.initiate_outbound_call(
                tenant_id=tenant_id,
                environment_id=environment_id,
                payload=OutboundCallCreateRequest(
                    to_number="+14155550104",
                    from_number="+14155550999",
                    agent_id=agent_id,
                    provider=TelephonyProviderName.TWILIO,
                    idempotency_key=f"runtime-concurrency-refused-{uuid.uuid4().hex[:8]}",
                ),
            )
        assert refused.value.status_code == 429

        for result in created:
            await service.hangup_call(tenant_id=tenant_id, call_id=result.id)
        snapshot = await concurrency_policy.get_concurrency_manager().snapshot(
            tenant_id, Limit(base=1, reserved_inbound=0)
        )
        assert snapshot["in_use"] == {"total": 0, "inbound": 0, "outbound": 0}
    finally:
        concurrency_policy.configure_counter_store(None)


@pytest.mark.asyncio
async def test_outbound_answer_callback_recovers_pending_session_call_link(
    client, db, tenant_a, monkeypatch
):
    environment, agent = await _seed_runtime_agent(db, tenant_a)

    class FakeAdapter:
        def configured(self) -> bool:
            return True

        async def create_outbound(self, *, to_number: str, from_number: str, **kwargs):
            return CallResult(
                provider="twilio",
                external_id=f"CA-callback-race-{uuid.uuid4().hex[:12]}",
                status="queued",
                to_number=to_number,
                from_number=from_number,
            )

    monkeypatch.setattr(
        "app.telephony.runtime.get_provider_adapter", lambda _provider: FakeAdapter()
    )
    result = await TelephonyRuntimeService(db).initiate_outbound_call(
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        payload=OutboundCallCreateRequest(
            to_number="+14155550100",
            from_number="+14155550999",
            agent_id=str(agent.id),
            provider=TelephonyProviderName.TWILIO,
            idempotency_key=f"runtime-answer-race-{uuid.uuid4().hex[:8]}",
        ),
    )

    call_session = await db.get(TelephonyCallSession, result.id)
    assert call_session is not None
    previous_call = await db.scalar(
        select(Call).where(Call.call_sid == result.provider_call_id)
    )
    assert previous_call is not None
    call_session.legacy_call_id = None
    call_session.provider_call_id = f"pending_{uuid.uuid4().hex}"
    call_session.status = TelephonyCallState.CREATED.value
    call_session.started_at = None
    await db.delete(previous_call)
    await db.commit()

    response = await client.post(
        f"/telephony/outbound-answer?call_session_id={result.id}",
        data={"CallSid": result.provider_call_id, "AnsweredBy": "human"},
    )
    assert response.status_code == 200
    assert "/telephony/ws?token=" in response.text

    await db.refresh(call_session)
    legacy_call = await db.scalar(
        select(Call).where(Call.call_sid == result.provider_call_id)
    )
    assert legacy_call is not None
    assert call_session.provider_call_id == result.provider_call_id
    assert call_session.status == TelephonyCallState.IN_PROGRESS.value
    assert call_session.legacy_call_id == legacy_call.id
    assert legacy_call.status.value == "in_progress"

    status_response = await client.post(
        f"/telephony/status?call_session_id={result.id}",
        data={
            "CallSid": result.provider_call_id,
            "CallStatus": "completed",
            "CallDuration": "24",
        },
    )
    assert status_response.status_code == 200
    await db.refresh(call_session)
    await db.refresh(legacy_call)
    assert call_session.status == TelephonyCallState.COMPLETED.value
    assert legacy_call.status.value == "completed"


@pytest.mark.asyncio
async def test_status_callback_reconciles_pending_twilio_session_by_callback_id(
    client, db, tenant_a, monkeypatch
):
    environment, agent = await _seed_runtime_agent(db, tenant_a)

    class FakeAdapter:
        def configured(self) -> bool:
            return True

        async def create_outbound(self, *, to_number: str, from_number: str, **kwargs):
            return CallResult(
                provider="twilio",
                external_id=f"CA-status-race-{uuid.uuid4().hex[:12]}",
                status="queued",
                to_number=to_number,
                from_number=from_number,
            )

    monkeypatch.setattr(
        "app.telephony.runtime.get_provider_adapter", lambda _provider: FakeAdapter()
    )
    result = await TelephonyRuntimeService(db).initiate_outbound_call(
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        payload=OutboundCallCreateRequest(
            to_number="+14155550101",
            from_number="+14155550999",
            agent_id=str(agent.id),
            provider=TelephonyProviderName.TWILIO,
            idempotency_key=f"runtime-status-race-{uuid.uuid4().hex[:8]}",
        ),
    )

    call_session = await db.get(TelephonyCallSession, result.id)
    assert call_session is not None
    previous_call = await db.scalar(
        select(Call).where(Call.call_sid == result.provider_call_id)
    )
    assert previous_call is not None
    call_session.legacy_call_id = None
    call_session.provider_call_id = f"pending_{uuid.uuid4().hex}"
    call_session.status = TelephonyCallState.CREATED.value
    call_session.started_at = None
    await db.delete(previous_call)
    await db.commit()

    response = await client.post(
        f"/telephony/status?call_session_id={result.id}",
        data={
            "CallSid": result.provider_call_id,
            "CallStatus": "no-answer",
            "CallDuration": "0",
        },
    )
    assert response.status_code == 200

    await db.refresh(call_session)
    legacy_call = await db.scalar(
        select(Call).where(Call.call_sid == result.provider_call_id)
    )
    assert legacy_call is not None
    assert call_session.provider_call_id == result.provider_call_id
    assert call_session.status == TelephonyCallState.NO_ANSWER.value
    assert call_session.legacy_call_id == legacy_call.id
    assert legacy_call.status.value == "no_answer"


@pytest.mark.asyncio
async def test_runtime_refuses_sip_without_verified_tenant_trunk_support(db, tenant_a, monkeypatch):
    environment, agent = await _seed_runtime_agent(db, tenant_a)
    tenant_id = tenant_a.id
    environment_id = environment.id
    agent_id = str(agent.id)
    monkeypatch.setattr(
        "app.telephony.runtime.get_provider_adapter",
        lambda _provider: pytest.fail("SIP must be rejected before adapter selection"),
    )

    service = TelephonyRuntimeService(db)
    with pytest.raises(TelephonyConfigurationError) as refused:
        await service.initiate_outbound_call(
            tenant_id=tenant_id,
            environment_id=environment_id,
            payload=OutboundCallCreateRequest(
                to_number="+14155550100",
                from_number="+14155550999",
                agent_id=agent_id,
                provider=TelephonyProviderName.SIP,
                idempotency_key=f"sip-fail-closed-{uuid.uuid4().hex[:8]}",
            ),
        )

    assert refused.value.code == "SIP_BYOC_NOT_READY"
    assert (await db.scalars(select(TelephonyCallSession))).all() == []


@pytest.mark.asyncio
async def test_health_does_not_report_unsupported_sip_as_ready(db, tenant_a, monkeypatch):
    db.add(
        TelephonySipConnection(
            tenant_id=tenant_a.id,
            name="Configured but unsupported SIP",
            termination_uri="sips:carrier.example.com:5061",
            transport="TLS",
            status=SipConnectionStatus.READY.value,
        )
    )
    await db.flush()
    monkeypatch.setattr(
        "app.telephony.sip.sip_gateway_client.SipGatewayClient.supports_tenant_trunks",
        False,
    )

    status = await TelephonyRuntimeService(db).get_health_status(tenant_id=tenant_a.id)

    assert status.configured_sip_connections == 1
    assert status.ready_sip_connections == 0
    assert status.default_provider != TelephonyProviderName.SIP.value


@pytest.mark.asyncio
async def test_hangup_does_not_release_lease_if_terminal_commit_fails(db, tenant_a, monkeypatch):
    call = TelephonyCallSession(
        tenant_id=tenant_a.id,
        provider=TelephonyProviderName.TWILIO.value,
        provider_call_id=f"CA-hangup-commit-{uuid.uuid4().hex[:12]}",
        direction="OUTBOUND",
        from_number="+14155550999",
        to_number="+14155550100",
        status=TelephonyCallState.IN_PROGRESS.value,
    )
    db.add(call)
    await db.commit()
    released: list[tuple[str, str]] = []

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    async def fake_release(tenant_id, call_id) -> None:
        released.append((str(tenant_id), str(call_id)))

    monkeypatch.setattr(db, "commit", fail_commit)
    monkeypatch.setattr(concurrency_policy, "release_call_slot", fake_release)

    with pytest.raises(RuntimeError, match="commit failed"):
        await TelephonyRuntimeService(db).hangup_call(
            tenant_id=tenant_a.id,
            call_id=call.id,
        )

    assert released == []


@pytest.mark.asyncio
async def test_idempotent_hangup_commits_before_releasing_stale_lease(db, tenant_a, monkeypatch):
    call = TelephonyCallSession(
        tenant_id=tenant_a.id,
        provider=TelephonyProviderName.TWILIO.value,
        provider_call_id=f"CA-hangup-terminal-{uuid.uuid4().hex[:12]}",
        direction="OUTBOUND",
        from_number="+14155550999",
        to_number="+14155550100",
        status=TelephonyCallState.COMPLETED.value,
    )
    db.add(call)
    await db.commit()
    timeline: list[tuple[str, str | None]] = []
    original_commit = db.commit

    async def marked_commit() -> None:
        await original_commit()
        timeline.append(("commit", None))

    async def fake_release(tenant_id, call_id) -> None:
        timeline.append(("release", str(call_id)))

    monkeypatch.setattr(db, "commit", marked_commit)
    monkeypatch.setattr(concurrency_policy, "release_call_slot", fake_release)

    result = await TelephonyRuntimeService(db).hangup_call(
        tenant_id=tenant_a.id,
        call_id=call.id,
    )

    assert result.status == TelephonyCallState.COMPLETED.value
    assert timeline == [("commit", None), ("release", str(call.id))]
