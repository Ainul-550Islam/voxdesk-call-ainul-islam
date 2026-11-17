from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import Call, CallDirection, CallStatus, Environment
from app.db.telephony_models import TelephonyCallSession, TelephonyCountryPolicy
from app.telephony.enums import TelephonyCallState, TelephonyProviderName, TransferMode
from app.telephony.exceptions import TransferError as V2TransferError
from app.telephony.provider import FakeTelephonyProvider
from app.telephony.schemas import CallTransferRequest
from app.telephony.transfer import CallTransferService
from app.telephony.transfer_service import TransferError, TransferOutcome, request_transfer
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_transfer_destination_policy_denial_prevents_transfer_creation(db):
    tenant = await make_tenant(db, "Transfer Policy Ltd")
    environment = await db.scalar(
        select(Environment).where(Environment.tenant_id == tenant.id)
    )
    assert environment is not None
    db.add(
        TelephonyCountryPolicy(
            tenant_id=tenant.id,
            scope="tenant",
            country="US",
            action="deny",
            reason="carrier compliance block",
        )
    )
    call = TelephonyCallSession(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        environment_id=environment.id,
        provider=TelephonyProviderName.TWILIO.value,
        provider_call_id=f"CA-transfer-policy-{uuid.uuid4().hex[:12]}",
        direction="INBOUND",
        from_number="+8801712345678",
        to_number="+88025550100",
        status=TelephonyCallState.IN_PROGRESS.value,
        agent_id=str(uuid.uuid4()),
    )
    db.add(call)
    await db.flush()

    service = CallTransferService(db)
    with pytest.raises(V2TransferError) as denied:
        await service.execute_call_transfer(
            tenant_id=tenant.id,
            call_session=call,
            payload=CallTransferRequest(
                mode=TransferMode.COLD,
                target_destination="+14155550100",
            ),
        )

    assert denied.value.code == "COUNTRY_NOT_ALLOWED"
    assert denied.value.detail["country"] == "US"
    assert call.status == TelephonyCallState.IN_PROGRESS.value
    assert (
        await service.list_call_transfers(
            tenant_id=tenant.id,
            call_session_id=call.id,
        )
    ) == []


@pytest.mark.asyncio
async def test_legacy_transfer_service_policy_denial_never_redirects(db):
    tenant = await make_tenant(db, "Legacy Transfer Policy Ltd")
    environment = await db.scalar(
        select(Environment).where(Environment.tenant_id == tenant.id)
    )
    assert environment is not None
    tenant.escalation_number = "+14155550100"
    db.add(
        TelephonyCountryPolicy(
            tenant_id=tenant.id,
            scope="tenant",
            country="US",
            action="deny",
            reason="carrier compliance block",
        )
    )
    call = Call(
        tenant_id=tenant.id,
        environment_id=environment.id,
        call_sid=f"CA-transfer-policy-{uuid.uuid4().hex[:12]}",
        from_number="+8801712345678",
        to_number=tenant.twilio_number,
        status=CallStatus.IN_PROGRESS,
        direction=CallDirection.INBOUND,
        started_at=datetime.now(timezone.utc),
    )
    db.add(call)
    await db.flush()

    provider = FakeTelephonyProvider()
    result = await request_transfer(db, tenant, call, provider=provider)

    assert result.outcome is TransferOutcome.TRANSFER_FAILED
    assert result.error is TransferError.COUNTRY_NOT_ALLOWED
    assert result.state.value == "failed"
    assert provider.call_count == 0
    await db.refresh(call)
    assert call.status is CallStatus.IN_PROGRESS
    assert call.transfer_attempts == 0
    assert call.transfer_error is not None
