from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Call, Environment
from app.db.telephony_models import (
    TelephonyCallSession,
    TelephonyCountryPolicy,
    TelephonyPhoneNumber,
    TelephonyProviderEvent,
)
from app.telephony.enums import (
    CallDirection,
    ProviderEventType,
    TelephonyCallState,
    TelephonyProviderName,
)
from app.telephony.exceptions import TelephonyConfigurationError
from app.telephony.schemas import NormalizedProviderWebhookEvent
from app.telephony.webhooks import (
    TelephonyWebhookProcessor,
    compute_webhook_hmac_signature,
)
from tests.conftest import make_tenant
from tests.webhook_secret import TEST_WEBHOOK_SECRET


async def _phone_for_tenant(db, tenant_id, *, number: str, inbound_enabled: bool, agent_id=None):
    environment_id = await db.scalar(
        select(Environment.id)
        .where(Environment.tenant_id == tenant_id)
        .order_by(Environment.is_default.desc())
        .limit(1)
    )
    row = TelephonyPhoneNumber(
        tenant_id=tenant_id,
        environment_id=environment_id,
        number=number,
        e164_number=number,
        provider=TelephonyProviderName.TWILIO.value,
        inbound_enabled=inbound_enabled,
        inbound_agent_id=agent_id,
    )
    db.add(row)
    await db.flush()
    return row


def _inbound_event(*, call_sid: str, to_number: str, event_id: str):
    return NormalizedProviderWebhookEvent(
        provider=TelephonyProviderName.TWILIO,
        provider_event_id=event_id,
        event_type=ProviderEventType.CALL_RINGING,
        provider_call_id=call_sid,
        from_number="+14155550100",
        to_number=to_number,
        direction=CallDirection.INBOUND,
        call_state=TelephonyCallState.RINGING,
        raw_payload={},
    )


@pytest.mark.asyncio
async def test_disabled_number_rejection_links_event_to_failed_session(db) -> None:
    tenant = await make_tenant(db, "Inbound Reject Ltd")
    number = "+14155550101"
    await _phone_for_tenant(db, tenant.id, number=number, inbound_enabled=False)
    event = _inbound_event(
        call_sid="CA-disabled-number",
        to_number=number,
        event_id="evt-disabled-number",
    )

    with pytest.raises(TelephonyConfigurationError, match="Inbound calls are disabled"):
        await TelephonyWebhookProcessor(db).apply_normalized_event(normalized=event)
    await db.commit()

    legacy = await db.scalar(select(Call).where(Call.call_sid == event.provider_call_id))
    failed_session = await db.scalar(
        select(TelephonyCallSession).where(
            TelephonyCallSession.provider_call_id == event.provider_call_id
        )
    )
    provider_event = await db.scalar(
        select(TelephonyProviderEvent).where(
            TelephonyProviderEvent.provider_event_id == event.provider_event_id
        )
    )
    assert legacy is not None
    assert failed_session is not None
    assert failed_session.legacy_call_id == legacy.id
    assert failed_session.status == TelephonyCallState.FAILED.value
    assert provider_event is not None
    assert provider_event.call_id == failed_session.id
    assert provider_event.call_id != legacy.id
    assert provider_event.failure_reason == "inbound_disabled_on_number"


@pytest.mark.asyncio
async def test_inbound_rejection_route_commits_failed_history_before_returning_error(
    client, db, monkeypatch
) -> None:
    tenant = await make_tenant(db, "Inbound Route Reject Ltd")
    number = "+14155550103"
    await _phone_for_tenant(db, tenant.id, number=number, inbound_enabled=False)
    call_sid = "CA-disabled-route-history"
    event_id = "evt-disabled-route-history"
    payload = {
        "provider_event_id": event_id,
        "provider_call_id": call_sid,
        "event_type": "call.ringing",
        "direction": "inbound",
        "from_number": "+14155550100",
        "to_number": number,
    }
    raw_body = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = compute_webhook_hmac_signature(
        secret=TEST_WEBHOOK_SECRET,
        raw_body=raw_body,
        timestamp=timestamp,
    )
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "telephony_webhook_secret", TEST_WEBHOOK_SECRET)

    response = await client.post(
        "/api/v1/telephony/webhooks/twilio/inbound",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Voxdesk-Timestamp": timestamp,
            "X-Voxdesk-Signature": signature,
            "X-Voxdesk-Organization-Id": str(tenant.id),
        },
    )

    assert response.status_code == 422
    assert "Inbound calls are disabled" in response.text
    failed_session = await db.scalar(
        select(TelephonyCallSession).where(
            TelephonyCallSession.provider_call_id == call_sid
        )
    )
    provider_event = await db.scalar(
        select(TelephonyProviderEvent).where(
            TelephonyProviderEvent.provider_event_id == event_id
        )
    )
    assert failed_session is not None
    assert failed_session.status == TelephonyCallState.FAILED.value
    assert provider_event is not None
    assert provider_event.processing_status == "rejected"
    assert provider_event.call_id == failed_session.id


@pytest.mark.asyncio
async def test_later_inbound_answer_rechecks_country_policy(db) -> None:
    tenant = await make_tenant(db, "Answer Policy Ltd")
    environment_id = await db.scalar(
        select(Environment.id)
        .where(Environment.tenant_id == tenant.id)
        .order_by(Environment.is_default.desc())
        .limit(1)
    )
    call_sid = "CA-answer-policy-recheck"
    call_session = TelephonyCallSession(
        tenant_id=tenant.id,
        environment_id=environment_id,
        provider=TelephonyProviderName.TWILIO.value,
        provider_call_id=call_sid,
        direction=CallDirection.INBOUND.value,
        from_number="+14155550100",
        to_number="+14155550101",
        status=TelephonyCallState.RINGING.value,
    )
    db.add_all(
        [
            call_session,
            TelephonyCountryPolicy(
                tenant_id=tenant.id,
                scope="tenant",
                country="US",
                action="deny",
                reason="policy changed while ringing",
            ),
        ]
    )
    await db.flush()

    event = NormalizedProviderWebhookEvent(
        provider=TelephonyProviderName.TWILIO,
        provider_event_id="evt-answer-policy-recheck",
        event_type=ProviderEventType.CALL_ANSWERED,
        provider_call_id=call_sid,
        from_number=call_session.from_number,
        to_number=call_session.to_number,
        direction=CallDirection.INBOUND,
        call_state=TelephonyCallState.IN_PROGRESS,
        raw_payload={},
    )
    processor = TelephonyWebhookProcessor(db)
    result = await processor.apply_normalized_event(normalized=event)

    assert result.detail == "inbound_country_policy_denied"
    assert result.call_state == TelephonyCallState.FAILED.value
    assert call_session.status == TelephonyCallState.FAILED.value
    assert call_session.hangup_reason == "inbound_country_policy_denied"
    assert call_session.metadata_json["country_policy"]["stage"] == "answer"
    assert call_session.transcript_turns == []
    assert processor._pending_slot_releases == {
        (str(tenant.id), str(call_session.id))
    }

    legacy = await db.scalar(select(Call).where(Call.call_sid == call_sid))
    provider_event = await db.scalar(
        select(TelephonyProviderEvent).where(
            TelephonyProviderEvent.provider_event_id == event.provider_event_id
        )
    )
    assert legacy is not None
    assert legacy.failure_reason == "inbound_country_policy_denied"
    assert provider_event is not None
    assert provider_event.processing_status == "failed_country_policy"
    assert provider_event.failure_reason == "inbound_country_policy_denied"


@pytest.mark.asyncio
async def test_stale_agent_binding_records_failure_without_invalid_legacy_agent_fk(db) -> None:
    tenant = await make_tenant(db, "Stale Binding Ltd")
    number = "+14155550102"
    await _phone_for_tenant(
        db,
        tenant.id,
        number=number,
        inbound_enabled=True,
        agent_id="stale-external-agent-key",
    )
    event = _inbound_event(
        call_sid="CA-stale-agent",
        to_number=number,
        event_id="evt-stale-agent",
    )

    with pytest.raises(TelephonyConfigurationError) as rejected:
        await TelephonyWebhookProcessor(db).apply_normalized_event(normalized=event)
    await db.commit()

    assert rejected.value.code == "AGENT_RUNTIME_AGENT_NOT_FOUND"
    legacy = await db.scalar(select(Call).where(Call.call_sid == event.provider_call_id))
    failed_session = await db.scalar(
        select(TelephonyCallSession).where(
            TelephonyCallSession.provider_call_id == event.provider_call_id
        )
    )
    assert legacy is not None
    assert legacy.agent_id is None
    assert failed_session is not None
    assert failed_session.agent_id == "stale-external-agent-key"
    assert failed_session.legacy_call_id == legacy.id
