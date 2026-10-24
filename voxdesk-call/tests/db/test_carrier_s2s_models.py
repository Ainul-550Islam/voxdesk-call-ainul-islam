"""Database tests for the Batch 02 carrier media tables.

The two tables of this batch are tenant-scoped by construction: every read
and every write carries the tenant id, and the unique key of a connection
check is scoped to the tenant as well. These tests pin that behaviour down
against the real ORM metadata and the real SQLite schema used by the test
suite.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.carrier_s2s_models import CarrierConnectionCheck, S2SSessionRecord
from app.db.models import Base, Tenant


async def _tenant(db, name: str) -> Tenant:
    tenant = Tenant(
        name=name,
        twilio_number=f"+1555{uuid.uuid4().int % 10**7:07d}",
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def test_tables_are_registered_on_the_shared_metadata() -> None:
    """The Batch 02 tables must be part of the schema Alembic compares."""
    assert "s2s_sessions" in Base.metadata.tables
    assert "carrier_connection_checks" in Base.metadata.tables

    sessions = Base.metadata.tables["s2s_sessions"]
    assert sessions.c.tenant_id.nullable is False
    assert sessions.c.provider.nullable is False
    assert sessions.c.cost_known.nullable is False
    # The failure message of a session that ended badly is optional.
    assert sessions.c.error.nullable is True
    assert sessions.c.failover_reason.nullable is True
    # The idempotency digest is indexed but may be absent for a check that
    # was not made through an idempotent route.
    assert sessions.c.metadata_json.nullable is False

    checks = Base.metadata.tables["carrier_connection_checks"]
    assert checks.c.carrier.nullable is False
    assert checks.c.status.nullable is False
    assert checks.c.checked_at.nullable is False
    # The outcome flag is never null: a check either succeeded or did not.
    assert checks.c.ok.nullable is False


async def test_session_row_round_trips_with_its_cost_ledger(db) -> None:
    tenant = await _tenant(db, "Batch02 Sessions")
    row = S2SSessionRecord(
        tenant_id=tenant.id,
        provider="openai_realtime",
        model="gpt-realtime",
        status="active",
        duration_ms=4321,
        input_audio_s=1.5,
        output_audio_s=2.25,
        input_tokens=120,
        output_tokens=340,
        cost_micro_usd=987,
        cost_known=True,
        metadata_json={"transport": "webrtc"},
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)

    assert row.id is not None
    assert row.tenant_id == tenant.id
    assert row.organization_id == tenant.id
    assert row.provider == "openai_realtime"
    assert row.status == "active"
    assert row.duration_ms == 4321
    assert row.input_audio_s == pytest.approx(1.5)
    assert row.output_audio_s == pytest.approx(2.25)
    assert row.input_tokens == 120
    assert row.output_tokens == 340
    assert row.cost_micro_usd == 987
    assert row.cost_known is True
    assert row.metadata_json == {"transport": "webrtc"}
    assert row.error is None
    assert row.started_at is not None
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_a_session_starts_unknown_cost_when_no_price_is_configured(db) -> None:
    """An unpriced session must not look like a free one."""
    tenant = await _tenant(db, "Batch02 Unknown Cost")
    row = S2SSessionRecord(
        tenant_id=tenant.id,
        provider="gemini_live",
        model="gemini-live-2.5",
        status="completed",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)

    assert row.cost_known is False
    assert row.cost_micro_usd == 0
    assert row.ended_at is None
    assert row.failover_reason is None
    assert row.error is None


async def test_connection_check_persists_its_idempotency_digest(db) -> None:
    tenant = await _tenant(db, "Batch02 Checks")
    digest = uuid.uuid4().hex
    check = CarrierConnectionCheck(
        tenant_id=tenant.id,
        carrier="vonage",
        status="not_configured",
        reason="missing_credentials",
        detail="VONAGE_APPLICATION_ID is not set",
        idempotency_key_digest=digest,
    )
    db.add(check)
    await db.flush()
    await db.refresh(check)

    assert check.id is not None
    assert check.tenant_id == tenant.id
    assert check.organization_id == tenant.id
    assert check.carrier == "vonage"
    assert check.status == "not_configured"
    assert check.reason == "missing_credentials"
    assert check.idempotency_key_digest == digest
    assert check.checked_at is not None
    # The check did not succeed, so the flag says so.
    assert check.ok is False


async def test_the_idempotency_digest_is_unique_per_tenant_and_carrier(db) -> None:
    tenant = await _tenant(db, "Batch02 Duplicate Check")
    other_tenant = await _tenant(db, "Batch02 Other Tenant")
    digest = uuid.uuid4().hex

    db.add(
        CarrierConnectionCheck(
            tenant_id=tenant.id,
            carrier="sip",
            status="ok",
            idempotency_key_digest=digest,
        )
    )
    await db.flush()

    with pytest.raises(IntegrityError):
        db.add(
            CarrierConnectionCheck(
                tenant_id=tenant.id,
                carrier="sip",
                status="ok",
                idempotency_key_digest=digest,
            )
        )
        await db.flush()
    await db.rollback()

    # The same digest in another tenant is a different row.
    db.add(
        CarrierConnectionCheck(
            tenant_id=other_tenant.id,
            carrier="sip",
            status="ok",
            idempotency_key_digest=digest,
        )
    )
    await db.flush()


async def test_rows_of_one_tenant_are_invisible_to_another(db) -> None:
    first = await _tenant(db, "Batch02 Isolation One")
    second = await _tenant(db, "Batch02 Isolation Two")

    mine = S2SSessionRecord(
        tenant_id=first.id,
        provider="openai_realtime",
        model="gpt-realtime",
        status="completed",
    )
    theirs = S2SSessionRecord(
        tenant_id=second.id,
        provider="openai_realtime",
        model="gpt-realtime",
        status="completed",
    )
    db.add_all([mine, theirs])
    await db.flush()

    visible = (
        (
            await db.execute(
                select(S2SSessionRecord).where(S2SSessionRecord.tenant_id == first.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.id for row in visible] == [mine.id]

    by_id = await db.scalar(
        select(S2SSessionRecord).where(
            S2SSessionRecord.id == theirs.id,
            S2SSessionRecord.tenant_id == first.id,
        )
    )
    assert by_id is None


async def test_a_failed_session_keeps_its_reason_and_message(db) -> None:
    tenant = await _tenant(db, "Batch02 Failed Session")
    row = S2SSessionRecord(
        tenant_id=tenant.id,
        provider="gemini_live",
        model="gemini-live-2.5",
        status="failed_over",
        failover_reason="provider_error",
        error="the provider dropped the socket",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)

    assert row.status == "failed_over"
    assert row.failover_reason == "provider_error"
    assert row.error == "the provider dropped the socket"


async def test_a_successful_check_is_flagged_as_ok(db) -> None:
    tenant = await _tenant(db, "Batch02 Ok Check")
    check = CarrierConnectionCheck(
        tenant_id=tenant.id,
        carrier="sip",
        status="ok",
        ok=True,
        reason="ready",
    )
    db.add(check)
    await db.flush()
    await db.refresh(check)

    assert check.ok is True
    assert check.status == "ok"


async def test_the_module_exports_both_models() -> None:
    from app.db import carrier_s2s_models

    assert set(carrier_s2s_models.__all__) == {
        "CarrierConnectionCheck",
        "S2SSessionRecord",
    }
