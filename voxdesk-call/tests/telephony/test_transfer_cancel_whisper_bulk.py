# File: tests/telephony/test_transfer_cancel_whisper_bulk.py — transfer cancel,
# warm-transfer whisper, and bulk transfer with per-item receipts.
"""Tests for the transfer half of ``app.telephony.call_control_service``.

Each of the three operations is a carrier side effect that cannot be undone, so
each is gated on a state machine, carries a durable receipt, and only reports
success after the provider confirmed it.

Covered:

* **cancel** — pulls the caller back, records the transfer as failed because it
  did not complete, and refuses when no transfer is in flight or the provider
  has no verified cancel.
* **whisper** — speaks a sanitized briefing to the human leg only, never the
  caller, and refuses when there is no separate human leg to speak to.
* **bulk** — transfers several calls with independent receipts; one failure
  does not undo the calls that already reached the carrier, and a retry replays
  the stored outcome instead of dialling again.
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
    TransferState,
    UserRole,
)
from app.schemas.call_control import (
    MAX_BULK_TRANSFER_ITEMS,
    BulkTransferItem,
    BulkTransferRequest,
    TransferWhisperRequest,
)
from app.telephony.call_control_service import (
    CallControlError,
    CallControlService,
    control_state,
)
from app.telephony.provider import FakeTelephonyProvider, set_provider
from tests.conftest import make_tenant, make_user
from pydantic import ValidationError


# --------------------------------------------------------------- fixtures ---


@pytest.fixture
def twilio_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "f" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", "test-auth-token")


@pytest.fixture
def provider() -> FakeTelephonyProvider:
    fake = FakeTelephonyProvider()
    set_provider(fake)
    yield fake
    set_provider(None)


async def _environment(db: AsyncSession, tenant: Tenant) -> Environment:
    row = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production"
        )
    )
    assert row is not None
    return row


async def _service(db: AsyncSession, tenant: Tenant) -> CallControlService:
    from app.auth.dependencies import TenantContext

    user = await make_user(
        db, tenant, UserRole.OWNER, email=f"xfer-{uuid.uuid4().hex[:8]}@example.test"
    )
    return CallControlService(db, TenantContext(user=user, tenant=tenant))


async def _live_call(
    db: AsyncSession, tenant: Tenant, environment: Environment, **overrides: Any
) -> Call:
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


async def _transferring_call(
    db: AsyncSession, tenant: Tenant, environment: Environment, **overrides: Any
) -> Call:
    """A call with a transfer in flight, as the route layer would see it."""
    context: dict[str, Any] = {
        "warm_transfer": {
            "mode": "warm",
            "target_call_sid": f"WT{uuid.uuid4().hex[:16]}",
            "caller_hold": True,
            "human_briefed": False,
        }
    }
    fields: dict[str, Any] = {
        "status": CallStatus.TRANSFERRED,
        "transfer_state": TransferState.DIALING,
        "transfer_destination": "+15557654321",
        "transfer_context": context,
    }
    fields.update(overrides)
    return await _live_call(db, tenant, environment, **fields)


def _bulk(call_id: uuid.UUID, destination: str = "+15557654321", **extra: Any) -> BulkTransferItem:
    return BulkTransferItem(
        call_id=str(call_id),
        destination=destination,
        idempotency_key=f"bulk-{uuid.uuid4().hex[:12]}",
        **extra,
    )


# ---------------------------------------------------------------- cancel ---


@pytest.mark.asyncio
async def test_cancel_brings_the_caller_back_and_records_the_transfer_as_failed(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-cancel-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.cancel_transfer(
        call.id,
        environment_id=environment.id,
        reason="caller changed their mind",
        idempotency_key="cancel-key-0001",
    )

    assert result.action == "transfer_cancel"
    assert result.provider == "twilio"
    assert result.replayed is False
    twiml = provider.last_twiml() or ""
    assert "<Connect>" in twiml and "<Stream" in twiml

    await db.refresh(call)
    # The transfer did not complete, so it is recorded as a failure rather than
    # left dangling in a state that suggests a human is still being dialled.
    assert call.transfer_state is TransferState.FAILED
    assert call.status is CallStatus.IN_PROGRESS
    assert control_state(call)["last_action"] == "transfer_cancel"


@pytest.mark.asyncio
async def test_cancel_refuses_when_no_transfer_is_in_flight(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-cancel-idle-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.cancel_transfer(
            call.id, environment_id=environment.id, idempotency_key="cancel-key-0002"
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "transfer_not_in_flight"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_cancel_replay_returns_the_stored_answer_and_does_not_redirect_again(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-cancel-replay-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    service = await _service(db, tenant)

    first = await service.cancel_transfer(
        call.id, environment_id=environment.id, idempotency_key="cancel-replay-key"
    )
    redirects_after_first = list(provider.redirects)

    second = await (await _service(db, tenant)).cancel_transfer(
        call.id, environment_id=environment.id, idempotency_key="cancel-replay-key"
    )

    assert second.idempotency_key == first.idempotency_key
    assert second.provider_status == first.provider_status
    assert provider.redirects == redirects_after_first


@pytest.mark.asyncio
async def test_cancel_reports_a_provider_refusal_rather_than_a_success(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-cancel-refused-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    service = await _service(db, tenant)
    provider.fail_with = ("20404", "call is not in progress")

    with pytest.raises(CallControlError) as raised:
        await service.cancel_transfer(
            call.id, environment_id=environment.id, idempotency_key="cancel-key-0003"
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "transfer_cancel_rejected"

    await db.refresh(call)
    # A refused cancel leaves the transfer in flight: the human may still be
    # ringing, and saying otherwise would strand the caller.
    assert call.transfer_state is TransferState.DIALING


# --------------------------------------------------------------- whisper ---


@pytest.mark.asyncio
async def test_whisper_speaks_the_sanitized_text_to_the_human_leg_only(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-whisper-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    human_leg = str(call.transfer_context["warm_transfer"]["target_call_sid"])
    service = await _service(db, tenant)

    result = await service.whisper_to_agent(
        call.id,
        environment_id=environment.id,
        message="Caller is a returning customer. Angry about the invoice.",
        idempotency_key="whisper-key-0001",
    )

    assert result.action == "transfer_whisper"
    assert result.provider_status == "whisper_delivered"
    assert "returning customer" in result.message
    # Addressed to the human leg, never to the caller on the parent leg.
    assert provider.redirects[-1][0] == human_leg
    assert "<Say" in (provider.last_twiml() or "")

    await db.refresh(call)
    assert call.transfer_context["warm_transfer"]["last_whisper"] == result.message
    assert call.transfer_context["warm_transfer"]["human_briefed"] is True


@pytest.mark.asyncio
async def test_whisper_strips_digit_runs_so_a_caller_cannot_speak_a_number_to_staff(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-whisper-sanitize-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.whisper_to_agent(
        call.id,
        environment_id=environment.id,
        message="Call them back on 555 0199 please",
        idempotency_key="whisper-key-0002",
    )

    assert "555" not in result.message
    assert "number removed" in result.message
    # What was stored and returned is the sanitized text, not the raw input.
    assert "<Say" in (provider.last_twiml() or "")
    assert "555" not in (provider.last_twiml() or "")


@pytest.mark.asyncio
async def test_whisper_refuses_when_there_is_no_human_leg_to_speak_to(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-whisper-noleg-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(
        db, tenant, environment, transfer_context={"warm_transfer": {"mode": "warm"}}
    )
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.whisper_to_agent(
            call.id,
            environment_id=environment.id,
            message="A briefing nobody can hear.",
            idempotency_key="whisper-key-0003",
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "whisper_target_unavailable"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_whisper_refuses_text_that_is_empty_once_sanitized(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-whisper-empty-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.whisper_to_agent(
            call.id,
            environment_id=environment.id,
            # Markup is stripped and digits are replaced, so nothing speakable
            # is left: a whisper must not be reported delivered when the text
            # that would have been spoken is empty.
            message="<>&\"'",
            idempotency_key="whisper-key-0004",
        )

    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "whisper_empty_after_sanitization"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_whisper_replay_returns_the_stored_answer_and_speaks_once(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-whisper-replay-org")
    environment = await _environment(db, tenant)
    call = await _transferring_call(db, tenant, environment)
    service = await _service(db, tenant)

    first = await service.whisper_to_agent(
        call.id,
        environment_id=environment.id,
        message="Briefing for the human.",
        idempotency_key="whisper-replay-key",
    )
    redirects_after_first = list(provider.redirects)

    second = await (await _service(db, tenant)).whisper_to_agent(
        call.id,
        environment_id=environment.id,
        message="Briefing for the human.",
        idempotency_key="whisper-replay-key",
    )

    assert second.message == first.message
    assert second.idempotency_key == first.idempotency_key
    assert provider.redirects == redirects_after_first


# ------------------------------------------------------------------ bulk ---


@pytest.mark.asyncio
async def test_bulk_transfer_reaches_the_carrier_for_every_live_call(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-bulk-org")
    environment = await _environment(db, tenant)
    first = await _live_call(db, tenant, environment)
    second = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.bulk_transfer(
        environment_id=environment.id,
        items=[_bulk(first.id), _bulk(second.id)],
        batch_key="batch-key-0001",
    )

    assert result.total == 2
    assert result.succeeded == 2
    assert result.failed == 0
    assert [item.status for item in result.items] == ["succeeded", "succeeded"]
    assert provider.call_count == 2
    assert all("<Dial" in twiml for _sid, twiml in provider.redirects)

    await db.refresh(first)
    await db.refresh(second)
    assert first.transfer_state is TransferState.DIALING
    assert second.transfer_state is TransferState.DIALING


@pytest.mark.asyncio
async def test_one_failing_call_does_not_undo_the_calls_that_already_transferred(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-bulk-partial-org")
    environment = await _environment(db, tenant)
    live = await _live_call(db, tenant, environment)
    finished = await _live_call(db, tenant, environment, status=CallStatus.COMPLETED)
    service = await _service(db, tenant)

    result = await service.bulk_transfer(
        environment_id=environment.id,
        items=[_bulk(live.id), _bulk(finished.id)],
        batch_key="batch-key-0002",
    )

    assert result.succeeded == 1
    assert result.failed == 1
    assert result.items[0].status == "succeeded"
    assert result.items[1].status == "failed"
    assert result.items[1].error_code == "call_already_terminal"

    await db.refresh(live)
    # The call that reached the carrier stays transferred: dialling it cannot
    # be undone, so the record has to say what actually happened.
    assert live.transfer_state is TransferState.DIALING
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_a_malformed_call_id_is_reported_and_does_not_stop_the_batch(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-bulk-badid-org")
    environment = await _environment(db, tenant)
    live = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.bulk_transfer(
        environment_id=environment.id,
        items=[
            BulkTransferItem(
                call_id="z" * 36,
                destination="+15557654321",
                idempotency_key="bulk-bad-id-0001",
            ),
            _bulk(live.id),
        ],
        batch_key="batch-key-0003",
    )

    assert result.items[0].status == "rejected"
    assert result.items[0].error_code == "invalid_call_id"
    assert result.items[1].status == "succeeded"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_retrying_a_batch_replays_the_calls_that_already_transferred(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-bulk-retry-org")
    environment = await _environment(db, tenant)
    live = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)
    items = [_bulk(live.id)]

    first = await service.bulk_transfer(
        environment_id=environment.id, items=items, batch_key="batch-replay-key"
    )
    redirects_after_first = list(provider.redirects)

    second = await (await _service(db, tenant)).bulk_transfer(
        environment_id=environment.id, items=items, batch_key="batch-replay-key"
    )

    assert second.items[0].replayed is True
    assert second.items[0].status == first.items[0].status
    assert second.batch_id == first.batch_id
    assert second.replayed == 1
    # The human was not dialled a second time.
    assert provider.redirects == redirects_after_first


@pytest.mark.asyncio
async def test_bulk_transfer_is_scoped_to_the_calls_environment(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="xfer-bulk-scope-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.bulk_transfer(
        environment_id=uuid.uuid4(), items=[_bulk(call.id)], batch_key="batch-scope-key"
    )

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "call_not_found"
    assert provider.call_count == 0


# ----------------------------------------------------------------- schema ---


def test_the_bulk_model_rejects_an_oversized_batch() -> None:
    with pytest.raises(ValidationError):
        BulkTransferRequest(
            items=[
                BulkTransferItem(
                    call_id=str(uuid.uuid4()),
                    destination="+15557654321",
                    idempotency_key=f"bulk-key-{i:04d}",
                )
                for i in range(MAX_BULK_TRANSFER_ITEMS + 1)
            ]
        )


def test_the_bulk_model_rejects_a_non_e164_destination() -> None:
    with pytest.raises(ValidationError):
        BulkTransferItem(
            call_id=str(uuid.uuid4()),
            destination="555-0100",
            idempotency_key="bulk-key-format",
        )


def test_the_whisper_model_rejects_a_blank_or_oversized_message() -> None:
    with pytest.raises(ValidationError):
        TransferWhisperRequest(message="   ", idempotency_key="whisper-key-format")
    with pytest.raises(ValidationError):
        TransferWhisperRequest(message="x" * 501, idempotency_key="whisper-key-format")


def test_the_bulk_model_rejects_a_non_e164_destination_before_any_provider_call() -> None:
    """An invalid destination is refused at the boundary, not dialled."""
    with pytest.raises(ValidationError):
        BulkTransferItem(
            call_id=str(uuid.uuid4()),
            destination="not-a-number",
            idempotency_key="bulk-key-format",
        )


def test_the_whisper_model_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TransferWhisperRequest.model_validate({"message": "hi", "to_caller": True})
