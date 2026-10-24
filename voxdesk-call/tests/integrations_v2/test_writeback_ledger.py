"""Tests for idempotent post-call writeback ledger, multi-provider partial failure, DLQ redrive, and signed recording URLs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Call, CallStatus
from app.integrations_v2.core.connection_store import upsert_connection
from app.integrations_v2.core.errors import UpstreamTransientError
from app.integrations_v2.core.writeback import (
    OUTBOX_EVENT_TYPE_INT2_WRITEBACK,
    CallWritebackInput,
    enqueue_call_writeback,
    execute_writeback_now,
    redrive_dead_letter_writeback,
    writeback_call_outcome_multi,
)
from app.outbox.models import OutboxEvent
from app.telephony import recording as telephony_recording
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_duplicate_post_call_writeback_produces_single_external_write_and_signs_recording(
    db,
):
    tenant = await make_tenant(db, "Writeback Tenant")

    call_row = Call(
        tenant_id=tenant.id,
        call_sid=f"CA{uuid.uuid4().hex[:24]}",
        from_number="+14155550100",
        to_number="+14155550199",
        direction="inbound",
        status=CallStatus.COMPLETED,
    )
    db.add(call_row)
    await db.flush()

    raw_storage_key = f"tenants/{tenant.id}/recordings/secret-s3-object-key.wav"
    rec_row = telephony_recording.CallRecording(
        tenant_id=tenant.id,
        call_id=call_row.id,
        provider="twilio",
        state="ready",
        storage_key=raw_storage_key,
    )
    db.add(rec_row)

    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zendesk",
        category="helpdesk",
        auth_kind="oauth2",
        credentials={"access_token": "zd-tok"},
        config={"subdomain": "acme"},
    )
    await db.commit()

    dispatched_payloads: list[dict] = []

    async def record_dispatcher(payload: dict) -> str:
        dispatched_payloads.append(payload)
        return "zd-ticket-9001"

    wb_input = CallWritebackInput(
        call_id=str(call_row.id),
        subject="Support Call",
        summary="Resolved billing inquiry",
        duration_seconds=185,
        caller_phone="+14155550100",
        recording_id=rec_row.id,
    )

    ledger1, executed1 = await execute_writeback_now(
        db,
        connection=conn,
        writeback_input=wb_input,
        dispatcher=record_dispatcher,
    )
    await db.commit()
    assert executed1 is True
    assert ledger1.status == "succeeded"
    assert ledger1.external_id == "zd-ticket-9001"

    ledger2, executed2 = await execute_writeback_now(
        db,
        connection=conn,
        writeback_input=wb_input,
        dispatcher=record_dispatcher,
    )
    await db.commit()
    assert executed2 is False
    assert ledger2.id == ledger1.id
    assert len(dispatched_payloads) == 1

    sent_url = dispatched_payloads[0]["recording_url"]
    assert "grant=" in sent_url and "exp=" in sent_url
    assert raw_storage_key not in sent_url

    wb2 = CallWritebackInput(
        call_id=str(uuid.uuid4()),
        subject="Queued Call",
        summary="Queued summary",
    )
    _, created_a = await enqueue_call_writeback(db, connection=conn, writeback_input=wb2)
    _, created_b = await enqueue_call_writeback(db, connection=conn, writeback_input=wb2)
    await db.commit()
    assert created_a is True
    assert created_b is False

    outbox_rows = (
        await db.execute(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == tenant.id,
                OutboxEvent.event_type == OUTBOX_EVENT_TYPE_INT2_WRITEBACK,
            )
        )
    ).scalars().all()
    assert len(outbox_rows) == 2


@pytest.mark.asyncio
async def test_multi_provider_partial_failure_isolates_siblings_and_redrives_dlq(db):
    tenant = await make_tenant(db, "Multi Writeback Tenant")
    zd_conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zendesk",
        category="helpdesk",
        auth_kind="oauth2",
        credentials={"access_token": "zd-tok"},
        config={"subdomain": "acme"},
    )
    crm_conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zoho_crm",
        category="crm",
        auth_kind="oauth2",
        credentials={"access_token": "zcrm-tok"},
    )
    await db.commit()

    async def ok_dispatch(_p: dict) -> str:
        return "zd-ok-1"

    async def failing_dispatch(_p: dict) -> str:
        raise UpstreamTransientError("503 temporary outage", provider="zoho_crm")

    wb = CallWritebackInput(call_id="call-partial-1", subject="S", summary="Sum")
    outcomes = await writeback_call_outcome_multi(
        db,
        targets=[
            (zd_conn, "ticket", ok_dispatch),
            (crm_conn, "call_activity", failing_dispatch),
        ],
        writeback_input=wb,
    )
    await db.commit()
    assert outcomes["zendesk:ticket"].status == "succeeded"
    assert outcomes["zoho_crm:call_activity"].status == "failed"

    crm_ledger = outcomes["zoho_crm:call_activity"]
    crm_ledger.status = "dead_letter"
    await db.commit()

    redriven = await redrive_dead_letter_writeback(
        db, ledger_id=crm_ledger.id, tenant_id=tenant.id
    )
    assert redriven.status == "pending"

    async def recovered_dispatch(_p: dict) -> str:
        return "zcrm-recovered-99"

    done_ledger, executed = await execute_writeback_now(
        db,
        connection=crm_conn,
        writeback_input=wb,
        dispatcher=recovered_dispatch,
        entity="call_activity",
    )
    await db.commit()
    assert executed is True
    assert done_ledger.status == "succeeded"
    assert done_ledger.external_id == "zcrm-recovered-99"
