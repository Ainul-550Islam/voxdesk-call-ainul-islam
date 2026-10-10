# File: tests/telephony/test_takeover.py — Supervisor takeover, TwiML replacement, pipeline disconnect guard, and AI rollback tests (Part 4 / Gate G5)
"""Tests for ``app.telephony.takeover``, ``app.agent.pipeline.handle_pipeline_disconnect``,
and ``app.telephony.providers`` takeover capability.

Verifies:
  1. ``call.takeover_pending`` prevents ``handle_pipeline_disconnect`` from finalizing the call
     as a caller hang-up when the AI media stream closes during supervisor takeover.
  2. ``takeover()`` sends a ``<Dial>`` / ``<Conference>`` TwiML replacement with recording
     continuity to the telephony provider fake and records ``takeover_started`` /
     ``takeover_completed`` state-machine events.
  3. When the supervisor leg fails, ``takeover()`` clears ``call.takeover_pending`` and
     rolls the call back to the AI media stream (``<Connect><Stream .../></Connect>``).
  4. Providers without takeover support (Telnyx/Vonage) have ``supports_takeover=False``
     and raise ``UnsupportedCapability``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.agent.pipeline import handle_pipeline_disconnect
from app.db.models import Call, CallDirection, CallStatus, TransferState
from app.telephony.provider import FakeTelephonyProvider, RedirectResult, set_provider
from app.telephony.provider_errors import UnsupportedCapability
from app.telephony.providers.telnyx import TelnyxAdapter
from app.telephony.providers.twilio import TwilioAdapter
from app.telephony.providers.vonage import VonageAdapter
from app.telephony.takeover import (
    is_takeover_pending,
    set_takeover_pending,
    takeover,
)
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_takeover_pending_prevents_pipeline_disconnect_finalization(
    db: AsyncSession,
) -> None:
    """A pipeline WS disconnect while takeover_pending=True must NOT finalize the call."""
    tenant = await make_tenant(db, name="takeover-guard-org")
    call = Call(
        tenant_id=tenant.id,
        call_sid="CA_TAKEOVER_GUARD_01",
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
        from_number="+15550101111",
        to_number="+15550102222",
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)

    set_takeover_pending(call, True, destination="+15550109999", reason="supervisor_takeover")
    await db.commit()
    await db.refresh(call)
    assert call.takeover_pending is True
    assert is_takeover_pending(call) is True

    finalized = await handle_pipeline_disconnect(call, session=db)
    assert finalized is False
    await db.refresh(call)
    assert call.status == CallStatus.IN_PROGRESS
    assert call.ended_at is None

    # Once takeover_pending is False, normal disconnect finalizes the call as COMPLETED
    set_takeover_pending(call, False)
    await db.commit()
    finalized_after = await handle_pipeline_disconnect(call, session=db)
    assert finalized_after is True
    await db.refresh(call)
    assert call.status == CallStatus.COMPLETED
    assert call.ended_at is not None


@pytest.mark.asyncio
async def test_takeover_sends_twiml_replacement_to_provider_fake(
    db: AsyncSession,
) -> None:
    """takeover() updates the active call with <Dial> TwiML and preserves recording continuity."""
    fake_provider = FakeTelephonyProvider()
    set_provider(fake_provider)
    try:
        tenant = await make_tenant(db, name="takeover-twiml-org")
        call = Call(
            tenant_id=tenant.id,
            call_sid="CA_TAKEOVER_TWIML_01",
            direction=CallDirection.INBOUND,
            status=CallStatus.IN_PROGRESS,
            from_number="+15550103333",
            to_number="+15550104444",
        )
        db.add(call)
        await db.commit()
        await db.refresh(call)

        result = await takeover(
            call,
            "+15550199999",
            session=db,
            tenant=tenant,
            whisper_text="VIP customer escalation",
            reason="Supervisor requested live intervention",
        )
        await db.commit()
        await db.refresh(call)

        assert result.ok is True
        assert result.state == "completed"
        assert result.recording_continuity is True
        assert result.rolled_back_to_ai is False
        assert "<Dial" in result.twiml
        assert 'record="record-from-answer-dual"' in result.twiml
        assert "<Number>+15550199999</Number>" in result.twiml
        assert "VIP customer escalation" in result.twiml

        assert len(fake_provider.redirects) == 1
        sid, sent_twiml = fake_provider.redirects[0]
        assert sid == "CA_TAKEOVER_TWIML_01"
        assert sent_twiml == result.twiml

        assert call.takeover_pending is True
        assert call.transfer_state == TransferState.CONNECTED
        assert call.transfer_destination == "+15550199999"
        events = [e["event"] for e in call.transfer_context.get("takeover_events", [])]
        assert "takeover_started" in events
        assert "takeover_completed" in events
    finally:
        set_provider(None)


@pytest.mark.asyncio
async def test_takeover_failure_rolls_back_to_ai(
    db: AsyncSession,
) -> None:
    """When the provider bridge fails, takeover() clears takeover_pending and rolls back to AI."""

    class FailingBridgeFakeProvider(FakeTelephonyProvider):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def redirect_call(self, call_sid: str, twiml: str) -> RedirectResult:
            self.attempts += 1
            self.redirects.append((call_sid, twiml))
            # First call is the supervisor <Dial> bridge -> simulate failure.
            # Second call is the AI <Connect><Stream> rollback -> succeed.
            if self.attempts == 1:
                return RedirectResult(False, "20404", "Supervisor bridge failed")
            return RedirectResult(True)

    fake_provider = FailingBridgeFakeProvider()
    set_provider(fake_provider)
    try:
        tenant = await make_tenant(db, name="takeover-rollback-org")
        call = Call(
            tenant_id=tenant.id,
            call_sid="CA_TAKEOVER_FAIL_01",
            direction=CallDirection.INBOUND,
            status=CallStatus.IN_PROGRESS,
            from_number="+15550105555",
            to_number="+15550106666",
        )
        db.add(call)
        await db.commit()
        await db.refresh(call)

        result = await takeover(
            call,
            "+15550198888",
            session=db,
            tenant=tenant,
            reason="Attempted supervisor takeover",
        )
        await db.commit()
        await db.refresh(call)

        assert result.ok is False
        assert result.state == "failed"
        assert result.rolled_back_to_ai is True
        assert call.takeover_pending is False
        assert call.status == CallStatus.IN_PROGRESS
        assert call.transfer_state == TransferState.FAILED

        # Two TwiML updates: 1st = supervisor <Dial>, 2nd = AI <Connect><Stream> rollback
        assert len(fake_provider.redirects) == 2
        _, bridge_twiml = fake_provider.redirects[0]
        _, rollback_twiml = fake_provider.redirects[1]
        assert "<Dial" in bridge_twiml
        assert "<Connect><Stream" in rollback_twiml

        events = [e["event"] for e in call.transfer_context.get("takeover_events", [])]
        assert "takeover_started" in events
        assert "takeover_failed" in events
        assert "takeover_rolled_back_to_ai" in events
    finally:
        set_provider(None)


@pytest.mark.asyncio
async def test_provider_supports_takeover_capability_flags() -> None:
    """TwilioAdapter supports takeover; TelnyxAdapter and VonageAdapter raise UnsupportedCapability."""
    assert TwilioAdapter.supports_takeover is True
    assert TelnyxAdapter.supports_takeover is False
    assert VonageAdapter.supports_takeover is False

    telnyx = TelnyxAdapter()
    vonage = VonageAdapter()

    with pytest.raises(UnsupportedCapability):
        await telnyx.bridge_to("call_ctrl_123", "+15550199999")

    with pytest.raises(UnsupportedCapability):
        await vonage.bridge_to("uuid_123", "+15550199999")
