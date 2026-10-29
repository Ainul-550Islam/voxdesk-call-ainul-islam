# File: tests/telephony/test_call_pause_resume.py — live call hold and resume
# through the provider fake, with durable idempotency receipts.
"""Tests for ``app.telephony.call_control_service.pause_call`` / ``resume_call``.

The provider fake records every ``redirect_call`` it is given, so these tests
assert on the TwiML that was actually generated and sent, not on a return value
the code could have invented. Where a test needs to prove that *nothing* was
sent, it asserts the recorded redirect list is empty or unchanged.

Covered:

1. Pause builds hold TwiML from ``twilio_handler``, sends it, marks the call
   held and writes a ``call_events`` row.
2. Resume returns the call to the media-stream TwiML and clears the hold.
3. The state machine refuses a second pause, a resume of a call that was never
   held, and any control on a terminal call.
4. Replaying an Idempotency-Key returns the stored answer and never contacts
   the carrier a second time.
5. A provider that refuses the redirect is reported as a failure and the call
   is not marked held — success is only ever reported after the carrier
   accepted.
6. An unconfigured deployment answers 503 rather than a fabricated success.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    Environment,
    Tenant,
    UserRole,
)
from app.telephony.call_events import TelephonyCallbackEvent
from app.telephony.call_control_service import (
    CallControlError,
    CallControlService,
    control_state,
    is_paused,
)
from app.telephony.provider import FakeTelephonyProvider, set_provider
from app.telephony.provider_errors import ProviderConfigurationError
from app.telephony.providers.twilio import TwilioAdapter
from tests.conftest import make_tenant, make_user


# --------------------------------------------------------------- fixtures ---


@pytest.fixture
def twilio_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment with live Twilio credentials present.

    The capability flags are derived from configuration, not from an account
    probe, so a hold is only offered when the credentials that would perform it
    are actually configured.
    """
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "f" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", "test-auth-token")


@pytest.fixture
def provider() -> FakeTelephonyProvider:
    fake = FakeTelephonyProvider()
    set_provider(fake)
    yield fake
    set_provider(None)


async def _environment(db: AsyncSession, tenant: Tenant) -> Environment:
    """The tenant's production environment.

    Inserting a ``Tenant`` row creates it, so a test must read it back rather
    than adding a second one — the schema allows only one per kind.
    """
    row = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id,
            Environment.kind == "production",
        )
    )
    assert row is not None
    return row


async def _context(db: AsyncSession, tenant: Tenant) -> Any:
    from app.auth.dependencies import TenantContext

    user = await make_user(db, tenant, UserRole.OWNER, email=f"pause-{uuid.uuid4().hex[:8]}@example.test")
    return TenantContext(user=user, tenant=tenant)


async def _live_call(db: AsyncSession, tenant: Tenant, environment: Environment, **overrides: Any) -> Call:
    fields: dict[str, Any] = {
        "tenant_id": tenant.id,
        "environment_id": environment.id,
        "call_sid": f"CA{uuid.uuid4().hex}",
        "direction": CallDirection.INBOUND,
        "status": CallStatus.IN_PROGRESS,
        "from_number": "+15551230000",
        "to_number": tenant.twilio_number,
    }
    fields.update(overrides)
    call = Call(**fields)
    db.add(call)
    await db.commit()
    await db.refresh(call)
    return call


async def _service(db: AsyncSession, tenant: Tenant) -> CallControlService:
    return CallControlService(db, await _context(db, tenant))


# ------------------------------------------------------------------ pause ---


@pytest.mark.asyncio
async def test_pause_sends_hold_twiml_and_marks_the_call_held(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="pause-hold-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.pause_call(
        call.id, environment_id=environment.id, reason="supervisor_joining", idempotency_key="pause-key-1"
    )

    assert result.action == "pause"
    assert result.status == "paused"
    assert result.provider == "twilio"
    assert result.provider_status == "held"
    assert result.replayed is False
    # The carrier was really asked, and asked with hold TwiML.
    assert provider.redirects == [(call.call_sid, provider.last_twiml())]
    twiml = provider.last_twiml() or ""
    # Hold parks the caller in a queue rather than pausing: a <Pause> expires
    # and then ends the call, which would hang up on the caller.
    assert "<Enqueue>" in twiml

    await db.refresh(call)
    assert is_paused(call) is True
    assert control_state(call)["pause_reason"] == "supervisor_joining"
    assert control_state(call)["last_action"] == "pause"

    events = list(
        (
            await db.scalars(
                select(TelephonyCallbackEvent).where(
                    TelephonyCallbackEvent.external_id == call.call_sid,
                    TelephonyCallbackEvent.event_type == "call.pause",
                )
            )
        ).all()
    )
    assert len(events) == 1
    assert events[0].outcome == "paused"


@pytest.mark.asyncio
async def test_pausing_twice_is_refused_without_contacting_the_carrier_again(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="pause-twice-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-key-a")
    calls_after_first = provider.call_count

    with pytest.raises(CallControlError) as raised:
        await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-key-b")

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "call_already_paused"
    assert provider.call_count == calls_after_first


@pytest.mark.asyncio
async def test_pause_requires_an_in_progress_call(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="pause-terminal-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment, status=CallStatus.COMPLETED)
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-key-c")

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "call_pause_not_allowed"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_pause_is_scoped_to_the_tenant_that_owns_the_call(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    """A call id from another tenant is not found, not paused."""
    owner = await make_tenant(db, name="pause-owner-org")
    stranger = await make_tenant(db, name="pause-stranger-org")
    environment = await _environment(db, owner)
    call = await _live_call(db, owner, environment)
    service = await _service(db, stranger)

    with pytest.raises(CallControlError) as raised:
        await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-key-d")

    assert raised.value.status_code == 404
    assert raised.value.detail["code"] == "call_not_found"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_pause_replay_returns_the_stored_answer_and_does_not_redial(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="pause-replay-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    first = await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-replay-key")
    redirects_after_first = list(provider.redirects)

    # Same key, but the call is no longer holdable: a replay must answer from
    # the receipt rather than re-running the state machine.
    await db.refresh(call)
    service_two = await _service(db, tenant)
    second = await service_two.pause_call(
        call.id, environment_id=environment.id, idempotency_key="pause-replay-key"
    )

    assert second.idempotency_key == first.idempotency_key
    assert second.provider_status == first.provider_status
    assert provider.redirects == redirects_after_first


@pytest.mark.asyncio
async def test_a_provider_that_refuses_the_redirect_is_reported_and_leaves_no_hold(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="pause-refused-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)
    provider.fail_with = ("20404", "call is not in progress")

    with pytest.raises(CallControlError) as raised:
        await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-key-e")

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "call_pause_rejected"

    await db.refresh(call)
    assert is_paused(call) is False


@pytest.mark.asyncio
async def test_an_unconfigured_deployment_refuses_instead_of_reporting_a_hold(
    db: AsyncSession, provider: FakeTelephonyProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    tenant = await make_tenant(db, name="pause-unconfigured-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    with pytest.raises(ProviderConfigurationError):
        await service.pause_call(call.id, environment_id=environment.id, idempotency_key="pause-key-f")

    assert provider.call_count == 0
    await db.refresh(call)
    assert is_paused(call) is False


@pytest.mark.asyncio
async def test_capabilities_are_reported_from_configuration(
    db: AsyncSession, twilio_credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await make_tenant(db, name="pause-caps-org")
    service = await _service(db, tenant)
    caps = service.capabilities()
    assert caps.pause is True
    assert caps.resume is True

    monkeypatch.setattr(settings, "twilio_account_sid", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    blanked = (await _service(db, tenant)).capabilities()
    # An unconfigured provider confirms nothing, so nothing is offered.
    assert blanked.pause is False
    assert blanked.resume is False


# ----------------------------------------------------------------- resume ---


@pytest.mark.asyncio
async def test_resume_returns_the_call_to_its_media_stream(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="resume-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    await service.pause_call(call.id, environment_id=environment.id, idempotency_key="resume-pause-key")
    result = await service.resume_call(
        call.id, environment_id=environment.id, idempotency_key="resume-key-1"
    )

    assert result.action == "resume"
    assert result.status == "in_progress"
    assert result.provider_status == "in_progress"
    twiml = provider.last_twiml() or ""
    assert "<Connect>" in twiml and "<Stream" in twiml

    await db.refresh(call)
    assert is_paused(call) is False
    assert control_state(call)["last_action"] == "resume"


@pytest.mark.asyncio
async def test_resume_refuses_a_call_that_was_never_held(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="resume-unheld-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.resume_call(call.id, environment_id=environment.id, idempotency_key="resume-key-b")

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "call_not_paused"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_resume_replay_returns_the_stored_answer_and_does_not_redial(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="resume-replay-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    await service.pause_call(call.id, environment_id=environment.id, idempotency_key="replay-pause-key")
    first = await service.resume_call(call.id, environment_id=environment.id, idempotency_key="replay-resume-key")
    redirects_after_first = list(provider.redirects)

    # The key is spent; the call is no longer held. A replay answers from the
    # receipt rather than running the state machine a second time.
    second = await (await _service(db, tenant)).resume_call(
        call.id, environment_id=environment.id, idempotency_key="replay-resume-key"
    )

    assert second.idempotency_key == first.idempotency_key
    assert second.provider_status == first.provider_status
    assert provider.redirects == redirects_after_first


# ---------------------------------------------------------------- adapter ---


def test_the_twilio_adapter_reports_hold_from_live_credentials(
    twilio_credentials: None,
) -> None:
    adapter = TwilioAdapter()
    caps = adapter.capabilities()
    assert caps.allows_control("supports_hold") is True
    assert caps.allows_control("supports_cancel_transfer") is True


def test_an_unconfigured_twilio_adapter_claims_no_control_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    caps = TwilioAdapter().capabilities()
    assert caps.allows_control("supports_hold") is False
    assert caps.allows("voice") is False
