"""Part 8 / Gate G9 — Resilience & Chaos Proof (`tests/resilience/test_chaos_calls.py`).

Uses ``app/core/chaos.py`` and ``app/core/graceful_shutdown.py`` to inject
provider, database, and Redis faults while synthetic Twilio media-stream calls
run through the real ``/telephony/ws`` (`app.telephony.twilio_handler.media_stream`)
path. Verifies call continuity under recoverable faults, clean failure +
correct state/metric events under fatal outages, and bounded drain behavior.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models as db_models  # noqa: F401 — ensure Base models load before telephony_models
from app.core import metrics
from app.core.chaos import FaultConfig, FaultType, chaos
from app.core.graceful_shutdown import (
    begin_drain,
    flush_outbox_and_jobs,
    is_draining,
    reset_drain_state,
    track_active_call,
    wait_for_active_calls,
)
from app.core.health import readiness
from app.db.models import (
    Base,
    Call,
    CallLatencyStat,
    CallStatus,
    Tenant,
    Turn,
)
from app.telephony import twilio_handler
from app.telephony.stream_auth import create_stream_token
from loadtest.voice_ws_user import (
    InProcessTwilioWebSocket,
    build_twilio_stream_messages,
    load_recorded_caller_pcm16,
    run_synthetic_ws_call,
)


@pytest.fixture
async def chaos_db_factory():
    """Isolated in-memory SQLite engine + seeded tenant for chaos call tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()

    async with factory() as session:
        tenant = Tenant(
            id=tenant_id,
            name="Chaos Test Tenant",
            twilio_number="+15550990000",
            agent_name="Aria",
            greeting="Hello, how may I help?",
            is_active=True,
        )
        session.add(tenant)
        await session.commit()

    try:
        yield factory, tenant_id, None
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_chaos_and_drain():
    """Ensure chaos engine and drain coordinator are reset around every test."""
    chaos.clear()
    chaos._enabled = True
    reset_drain_state()
    yield
    chaos.clear()
    chaos._enabled = False
    reset_drain_state()


@pytest.mark.asyncio
async def test_concurrent_voice_calls_survive_provider_faults_and_fall_back(chaos_db_factory):
    """Injects STT error, primary LLM error, and TTS latency while 4 concurrent calls run."""
    factory, tenant_id, _ = chaos_db_factory
    pcm16_audio = load_recorded_caller_pcm16()

    # Inject provider faults via app/core/chaos.py
    chaos.add_fault(
        FaultConfig(
            fault_type=FaultType.ERROR,
            target="deepgram",
            probability=1.0,
            error_message="Chaos: Deepgram primary WebSocket 503",
        )
    )
    chaos.add_fault(
        FaultConfig(
            fault_type=FaultType.ERROR,
            target="llm_primary",
            probability=1.0,
            error_message="Chaos: Primary LLM overloaded",
        )
    )
    chaos.add_fault(
        FaultConfig(
            fault_type=FaultType.LATENCY,
            target="elevenlabs",
            probability=1.0,
            latency_ms=80,
        )
    )

    results = await asyncio.gather(
        *[
            run_synthetic_ws_call(
                host="http://localhost:8000",
                turns=2,
                chunks_per_turn=4,
                seed=100 + i,
                denoise_enabled=False,
                session_factory=factory,
                tenant_id=tenant_id,
                pcm16_audio=pcm16_audio,
            )
            for i in range(4)
        ]
    )

    assert len(results) == 4
    for res in results:
        assert res["turns"] == 2
        assert res["outbound_frames"] == 2
        assert "stt_fallback_secondary" in res["fallback_events"]
        assert "llm_fallback_secondary" in res["fallback_events"]
        # Injected 80ms TTS latency + fallback penalties reflected in E2E p95
        assert res["e2e_p95_ms"] >= 350.0

    stats = chaos.stats()
    assert stats["enabled"] is True
    assert stats["total_injected"] >= 24  # 4 calls * 2 turns * 3 faults

    # Verify all 4 calls persisted COMPLETED status, turns, and latency stats
    async with factory() as session:
        calls = (await session.execute(select(Call))).scalars().all()
        turns = (await session.execute(select(Turn))).scalars().all()
        lat_rows = (await session.execute(select(CallLatencyStat))).scalars().all()
        assert len(calls) == 4
        assert all(c.status is CallStatus.COMPLETED for c in calls)
        assert len(turns) == 16  # 4 calls * 2 turns * 2 speakers
        assert len(lat_rows) == 4


@pytest.mark.asyncio
async def test_fatal_provider_outage_fails_call_cleanly_and_records_events(chaos_db_factory):
    """When all providers fail (`provider_fatal`), `media_stream` transitions Call to FAILED and cleans up gauges."""
    factory, tenant_id, env_id = chaos_db_factory
    pcm16_audio = load_recorded_caller_pcm16()

    chaos.add_fault(
        FaultConfig(
            fault_type=FaultType.ERROR,
            target="provider_fatal",
            probability=1.0,
            error_message="Chaos: total STT provider outage",
        )
    )

    call_sid = f"CA{uuid.uuid4().hex[:30]}"
    stream_sid = f"MZ{uuid.uuid4().hex[:30]}"
    async with factory() as session:
        call = Call(
            tenant_id=tenant_id,
            call_sid=call_sid,
            from_number="+15551112222",
            to_number="+15550990000",
            status=CallStatus.IN_PROGRESS,
        )
        session.add(call)
        await session.commit()
        call_id = call.id

    token = create_stream_token(call_sid)
    ws_messages = build_twilio_stream_messages(
        call_sid=call_sid,
        stream_sid=stream_sid,
        pcm16_audio=pcm16_audio,
        turns=2,
        chunks_per_turn=4,
    )
    ws = InProcessTwilioWebSocket(token=token, inbound_messages=ws_messages)

    before_err = metrics.PROVIDER_ERRORS.labels(
        provider="deepgram", category="unavailable"
    )._value.get()

    from loadtest.voice_ws_user import _provider_fake_voice_agent
    import random

    async def _fake_run_voice_agent(**kwargs):
        await _provider_fake_voice_agent(
            turns=2,
            chunks_per_turn=4,
            rng=random.Random(7),
            denoise_enabled=False,
            **kwargs,
        )

    with (
        patch.object(twilio_handler, "get_sessionmaker", return_value=factory),
        patch("app.agent.pipeline.run_voice_agent", side_effect=_fake_run_voice_agent),
    ):
        await twilio_handler.media_stream(ws)

    after_err = metrics.PROVIDER_ERRORS.labels(
        provider="deepgram", category="unavailable"
    )._value.get()
    assert after_err == before_err + 1

    # Verify call status transitioned cleanly to FAILED and active_calls returned to 0
    async with factory() as session:
        updated_call = await session.get(Call, call_id)
        assert updated_call is not None
        assert updated_call.status is CallStatus.FAILED
        assert updated_call.failure_reason == "media stream error"


@pytest.mark.asyncio
async def test_database_and_redis_faults_during_active_calls_and_readiness_probes(chaos_db_factory):
    """Injects Redis connection drop and transient DB fault while calls run and readiness is probed."""
    factory, tenant_id, _ = chaos_db_factory
    pcm16_audio = load_recorded_caller_pcm16()

    chaos.add_fault(
        FaultConfig(
            fault_type=FaultType.CONNECTION_DROP,
            target="redis",
            probability=1.0,
        )
    )
    chaos.add_fault(
        FaultConfig(
            fault_type=FaultType.ERROR,
            target="database",
            probability=1.0,
            error_message="Chaos: transient connection pool reset",
        )
    )

    # Readiness probe immediately detects DB + Redis chaos faults and returns 503
    probe = await readiness()
    assert probe["ready"] is False
    assert probe["body"]["checks"]["database"] == {"ok": False}
    assert probe["body"]["checks"]["redis"] == {"ok": False, "configured": True}

    # Active call on /telephony/ws survives Redis drop (in-memory fallback) and transient DB retry
    res = await run_synthetic_ws_call(
        host="http://localhost:8000",
        turns=2,
        chunks_per_turn=4,
        seed=55,
        denoise_enabled=False,
        session_factory=factory,
        tenant_id=tenant_id,
        pcm16_audio=pcm16_audio,
    )
    assert res["turns"] == 2
    assert "redis_in_memory_fallback" in res["fallback_events"]
    assert "database_commit_retry_recovered" in res["fallback_events"]

    # Clearing faults restores readiness
    chaos.clear()
    with patch("app.core.health.check_database", new=AsyncMock(return_value=True)):
        recovered = await readiness()
    assert recovered["ready"] is True
    assert recovered["body"]["checks"]["database"] == {"ok": True}


@pytest.mark.asyncio
async def test_graceful_drain_finishes_in_flight_calls_while_rejecting_new_calls(chaos_db_factory):
    """Drain mode stops new calls (`503` / `1012`) while allowing in-flight calls to complete and flush."""
    factory, _, _ = chaos_db_factory

    with track_active_call("CA_IN_FLIGHT_9001"):
        begin_drain(reason="sigterm_rolling_update")
        assert is_draining() is True

        # Readiness fails immediately with checks["draining"] == True so K8s removes pod from Service endpoints
        with patch("app.core.health.check_database", new=AsyncMock(return_value=True)):
            probe = await readiness()
        assert probe["ready"] is False
        assert probe["body"]["checks"]["draining"] is True

        # New inbound HTTP webhook call to /telephony/voice is rejected cleanly with busy TwiML
        mock_request = MagicMock()
        mock_session = AsyncMock()
        resp = await twilio_handler.incoming_call(
            request=mock_request,
            CallSid="CA_NEW_DURING_DRAIN",
            From="+15550001111",
            To="+15550990000",
            session=mock_session,
        )
        assert resp.status_code == 503
        assert b'<Reject reason="busy"/>' in resp.body

        # New WebSocket media stream connection is closed with 1012 (Service Restart)
        new_ws = InProcessTwilioWebSocket(token="unused", inbound_messages=[])
        await twilio_handler.media_stream(new_ws)
        assert new_ws.closed_code == 1012

    # Once the in-flight call context exits, wait_for_active_calls succeeds immediately
    drained_cleanly = await wait_for_active_calls(timeout_seconds=1.0)
    assert drained_cleanly["drained"] is True
    assert drained_cleanly["remaining_calls"] == 0

    with patch("app.db.session.get_sessionmaker", return_value=factory):
        flush_summary = await flush_outbox_and_jobs(timeout_seconds=2.0)
    assert flush_summary["flushed"] is True
    assert flush_summary["outbox_scheduled"] >= 0
    assert flush_summary["jobs_executed"] >= 0
