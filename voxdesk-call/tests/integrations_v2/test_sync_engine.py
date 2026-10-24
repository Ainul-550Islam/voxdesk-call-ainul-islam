"""Tests for incremental sync cursor checkpointing, crash resume, conflict resolution, and dead-lettering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
from sqlalchemy import select

from app.db.integrations_v2_models import SyncCursor
from app.db.models import Lead
from app.integrations_v2.core.connection_store import upsert_connection
from app.integrations_v2.core.pagination import PageBatch
from app.integrations_v2.core.sync_engine import ExternalSyncRecord, run_incremental_sync
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_incremental_sync_resumes_from_checkpoint_and_dead_letters_bad_record(db):
    tenant = await make_tenant(db, "Sync Tenant")
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="pipedrive",
        category="crm",
        auth_kind="oauth2",
        credentials={"access_token": "tok"},
        config={"conflict_policy": "newest_wins"},
    )
    await db.commit()

    now = datetime.now(timezone.utc)

    async def crashing_stream(cursor: str | None) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        assert cursor is None
        yield PageBatch(
            items=[
                ExternalSyncRecord(
                    external_id="p-1",
                    entity_type="Contact",
                    raw_fields={
                        "first_name": "Alan",
                        "last_name": "Turing",
                        "email": "alan@bletchley.uk",
                        "phone": "+14155550111",
                    },
                    external_updated_at=now,
                ),
                ExternalSyncRecord(
                    external_id="p-bad",
                    entity_type="Contact",
                    raw_fields={
                        "first_name": "Bad",
                        "last_name": "Email",
                        "email": "invalid-email-format",
                    },
                    external_updated_at=now,
                ),
            ],
            next_cursor="cur-page-2",
            has_more=True,
            page_number=1,
        )
        raise RuntimeError("simulated network drop before page 2")

    first_run = await run_incremental_sync(
        db,
        connection=conn,
        entity_type="Contact",
        page_stream_factory=crashing_stream,
    )
    await db.commit()

    assert first_run.status == "failed"
    assert first_run.records_upserted == 1
    assert first_run.records_failed == 1
    assert len(first_run.dead_letters) == 1

    saved_cursor = (
        await db.execute(
            select(SyncCursor).where(
                SyncCursor.connection_id == conn.id,
                SyncCursor.entity_type == "Contact",
            )
        )
    ).scalar_one()
    assert saved_cursor.cursor_value == "cur-page-2"

    async def resumed_stream(cursor: str | None) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        assert cursor == "cur-page-2"
        yield PageBatch(
            items=[
                ExternalSyncRecord(
                    external_id="p-2",
                    entity_type="Contact",
                    raw_fields={
                        "first_name": "Joan",
                        "last_name": "Clarke",
                        "email": "joan@bletchley.uk",
                        "phone": "+14155550112",
                    },
                    external_updated_at=now + timedelta(minutes=1),
                )
            ],
            next_cursor="cur-page-3",
            has_more=False,
            page_number=1,
        )

    second_run = await run_incremental_sync(
        db,
        connection=conn,
        entity_type="Contact",
        page_stream_factory=resumed_stream,
    )
    await db.commit()

    assert second_run.status == "succeeded"
    assert second_run.cursor_before == "cur-page-2"
    assert second_run.cursor_after == "cur-page-3"

    leads = (
        await db.execute(select(Lead).where(Lead.tenant_id == tenant.id))
    ).scalars().all()
    assert {lead.email for lead in leads} == {"alan@bletchley.uk", "joan@bletchley.uk"}


@pytest.mark.asyncio
async def test_conflict_policy_voxdesk_wins_preserves_local_lead(db):
    tenant = await make_tenant(db, "Conflict Tenant")
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zoho_crm",
        category="crm",
        auth_kind="oauth2",
        credentials={"access_token": "tok"},
        config={"conflict_policy": "voxdesk_wins"},
    )
    await db.commit()

    async def initial_stream(_cur: str | None) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        yield PageBatch(
            items=[
                ExternalSyncRecord(
                    external_id="zc-100",
                    entity_type="Contact",
                    raw_fields={
                        "first_name": "Initial",
                        "last_name": "Name",
                        "email": "init@example.com",
                        "phone": "+14155550199",
                    },
                )
            ],
            next_cursor="c1",
            has_more=False,
        )

    await run_incremental_sync(
        db, connection=conn, entity_type="Contact", page_stream_factory=initial_stream
    )
    lead = (await db.execute(select(Lead).where(Lead.tenant_id == tenant.id))).scalar_one()
    lead.name = "Locally Edited Name"
    await db.commit()

    async def overwrite_stream(_cur: str | None) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        yield PageBatch(
            items=[
                ExternalSyncRecord(
                    external_id="zc-100",
                    entity_type="Contact",
                    raw_fields={
                        "first_name": "External",
                        "last_name": "Overwrite",
                        "email": "init@example.com",
                        "phone": "+14155550199",
                    },
                )
            ],
            next_cursor="c2",
            has_more=False,
        )

    run2 = await run_incremental_sync(
        db, connection=conn, entity_type="Contact", page_stream_factory=overwrite_stream
    )
    await db.commit()
    assert run2.records_upserted == 0
    await db.refresh(lead)
    assert lead.name == "Locally Edited Name"
