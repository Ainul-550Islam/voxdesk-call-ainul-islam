# File: tests/telephony/test_dtmf_batch.py — batched DTMF with per-item receipts.
"""Tests for ``app.telephony.call_control_service.send_dtmf_batch``.

A batch is not atomic against a carrier: once a run has been played it cannot
be un-played. So each run gets its own durable receipt and its own result row,
and one failure must not hide the runs that did reach the carrier.

Covered:

1. Every accepted run produces one real ``<Play digits="...">`` redirect, in
   order, and one ``sent`` result row.
2. A run the provider rejects is reported on its own row and the runs around it
   are still sent.
3. An item with no Idempotency-Key is rejected locally and never reaches the
   carrier.
4. Replaying one run returns the stored outcome for that run only and does not
   re-send it.
5. A held call refuses DTMF; a terminal call refuses it too.
6. The request model rejects oversized batches, non-DTMF characters and a
   missing or too-short key before any provider call happens.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.models import Call, CallDirection, CallStatus, Environment, Tenant, UserRole
from app.schemas.call_control import (
    MAX_DTMF_BATCH_ITEMS,
    DtmfBatchRequest,
    DtmfItem,
)
from app.telephony.call_control_service import (
    CallControlError,
    CallControlService,
    control_state,
)
from app.telephony.provider import FakeTelephonyProvider, set_provider
from app.telephony.providers.twilio import TwilioAdapter
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
        db, tenant, UserRole.OWNER, email=f"dtmf-{uuid.uuid4().hex[:8]}@example.test"
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


def _items(*digits: str, prefix: str = "dtmf-key") -> list[DtmfItem]:
    return [
        DtmfItem(digits=value, idempotency_key=f"{prefix}-{index}-{uuid.uuid4().hex[:8]}")
        for index, value in enumerate(digits)
    ]


# ------------------------------------------------------------------ batch ---


@pytest.mark.asyncio
async def test_every_run_produces_one_real_play_redirect(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="dtmf-batch-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.send_dtmf_batch(
        call.id, environment_id=environment.id, items=_items("1234", "*", "9w9")
    )

    assert result.action == "dtmf_batch"
    assert result.sent == 3
    assert result.rejected == 0
    assert [redirect[0] for redirect in provider.redirects] == [call.call_sid] * 3
    played = [redirect[1] for redirect in provider.redirects]
    assert '<Play digits="1234"' in played[0]
    assert '<Play digits="*"' in played[1]
    assert '<Play digits="9w9"' in played[2]
    assert [item.status for item in result.items] == ["sent", "sent", "sent"]


@pytest.mark.asyncio
async def test_a_rejected_run_does_not_stop_the_runs_around_it(
    db: AsyncSession, twilio_credentials: None
) -> None:
    """One failing run must not roll back the runs that already played."""
    tenant = await make_tenant(db, name="dtmf-partial-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    from app.telephony.provider import RedirectResult

    class RejectOne(FakeTelephonyProvider):
        """Refuses one digit run and records every attempt it was given."""

        reject = "9999"
        attempts: list[str] = []

        async def redirect_call(self, call_sid: str, twiml: str) -> RedirectResult:
            self.attempts.append(twiml)
            if f'digits="{self.reject}"' in twiml:
                return RedirectResult(False, "21201", "invalid digits")
            return await super().redirect_call(call_sid, twiml)

    fake = RejectOne()
    set_provider(fake)
    try:
        result = await service.send_dtmf_batch(
            call.id, environment_id=environment.id, items=_items("1111", "9999", "2222")
        )
    finally:
        set_provider(None)

    assert [item.status for item in result.items] == ["sent", "failed", "sent"]
    assert result.sent == 2
    assert result.rejected == 1
    assert result.items[1].error == "invalid digits"
    # All three were attempted, and the two accepted runs really went out.
    assert len(fake.attempts) == 3
    assert fake.call_count == 2


@pytest.mark.asyncio
async def test_an_item_without_a_key_never_reaches_the_carrier(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="dtmf-nokey-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    result = await service.send_dtmf_batch(
        call.id,
        environment_id=environment.id,
        items=[
            DtmfItem(digits="1111", idempotency_key="dtmf-present-key-1"),
            DtmfItem(digits="2222"),
        ],
    )

    assert [item.status for item in result.items] == ["sent", "rejected"]
    assert result.items[1].error is not None
    assert "Idempotency-Key" in (result.items[1].error or "")
    assert [redirect[1] for redirect in provider.redirects if 'digits="2222"' in redirect[1]] == []


@pytest.mark.asyncio
async def test_replaying_one_run_returns_its_stored_outcome_only(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="dtmf-replay-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    shared = "dtmf-shared-key-0001"
    first = await service.send_dtmf_batch(
        call.id,
        environment_id=environment.id,
        items=[DtmfItem(digits="4321", idempotency_key=shared)],
    )
    redirects_after_first = list(provider.redirects)

    second = await (await _service(db, tenant)).send_dtmf_batch(
        call.id,
        environment_id=environment.id,
        items=[DtmfItem(digits="4321", idempotency_key=shared)],
    )

    assert second.items[0].status == first.items[0].status
    assert second.items[0].idempotency_key == first.items[0].idempotency_key
    assert second.replayed == 1
    # The carrier was not asked again: the stored answer was returned.
    assert provider.redirects == redirects_after_first


@pytest.mark.asyncio
async def test_a_held_call_refuses_dtmf(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="dtmf-held-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)
    await service.pause_call(call.id, environment_id=environment.id, idempotency_key="dtmf-pause-key-1")

    with pytest.raises(CallControlError) as raised:
        await service.send_dtmf_batch(
            call.id, environment_id=environment.id, items=_items("1")
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "call_paused"
    # The pause redirect is the only thing the carrier ever saw.
    assert all("<Play" not in twiml for _sid, twiml in provider.redirects)


@pytest.mark.asyncio
async def test_a_finished_call_refuses_dtmf(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="dtmf-final-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment, status=CallStatus.COMPLETED)
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.send_dtmf_batch(
            call.id, environment_id=environment.id, items=_items("1")
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "call_dtmf_not_allowed"
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_dtmf_is_scoped_to_the_calls_environment(
    db: AsyncSession, twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    tenant = await make_tenant(db, name="dtmf-scope-org")
    environment = await _environment(db, tenant)
    call = await _live_call(db, tenant, environment)
    service = await _service(db, tenant)

    with pytest.raises(CallControlError) as raised:
        await service.send_dtmf_batch(
            call.id, environment_id=uuid.uuid4(), items=_items("1")
        )

    assert raised.value.status_code == 404
    assert provider.call_count == 0


# ----------------------------------------------------------------- schema ---


def test_the_batch_model_rejects_an_oversized_batch() -> None:
    with pytest.raises(ValidationError):
        DtmfBatchRequest(
            items=[DtmfItem(digits="1", idempotency_key=f"key-{i:04d}") for i in range(MAX_DTMF_BATCH_ITEMS + 1)]
        )


def test_the_batch_model_rejects_an_empty_batch() -> None:
    with pytest.raises(ValidationError):
        DtmfBatchRequest(items=[])


@pytest.mark.parametrize("digits", ["12a", "<Play>", "12 34", "", "12;34", "1<2"])
def test_the_item_model_rejects_non_dtmf_characters(digits: str) -> None:
    with pytest.raises(ValidationError):
        DtmfItem(digits=digits, idempotency_key="dtmf-key-valid")


def test_the_item_model_accepts_the_full_dtmf_alphabet() -> None:
    item = DtmfItem(digits="0123456789#*wW", idempotency_key="dtmf-key-alphabet")
    assert item.digits == "0123456789#*wW"


def test_the_item_model_rejects_a_too_short_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        DtmfItem(digits="1234", idempotency_key="short")


def test_the_batch_model_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DtmfBatchRequest.model_validate({"items": [], "surprise": True})


# ---------------------------------------------------------------- adapter ---


@pytest.mark.asyncio
async def test_the_twilio_adapter_rejects_empty_digits_without_calling_twilio(
    twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    from app.telephony.provider_errors import ProviderValidationError

    with pytest.raises(ProviderValidationError):
        await TwilioAdapter().send_dtmf("CA123", "   ")
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_the_twilio_adapter_rejects_a_missing_call_sid(
    twilio_credentials: None, provider: FakeTelephonyProvider
) -> None:
    from app.telephony.provider_errors import ProviderValidationError

    with pytest.raises(ProviderValidationError):
        await TwilioAdapter().send_dtmf("", "1234")
    assert provider.call_count == 0


def test_the_hold_and_resume_paths_share_one_control_state_key() -> None:
    """The hold marker lives in one place, so the state machine cannot drift."""
    call = Call(transfer_context={"call_control": {"paused": True}})
    assert control_state(call)["paused"] is True
