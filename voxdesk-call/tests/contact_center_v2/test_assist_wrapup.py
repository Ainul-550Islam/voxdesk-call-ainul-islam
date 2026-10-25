import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.assist.wrapup_service import (
    reconcile_crm_writebacks,
    submit_call_wrapup,
)
from app.db.contact_center_v2_models import WrapupDraft
from app.db.models import (
    Call,
    CrmEvent,
    CrmIntegration,
    CrmProviderType,
    CrmSync,
    CrmSyncStatus,
    User,
)
from tests.contact_center_v2.conftest import Cc2Scope


async def wrapup_draft(db: AsyncSession, scope: Cc2Scope, agent: User) -> WrapupDraft:
    call = Call(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        call_sid=f"CA{uuid.uuid4().hex[:30]}",
        from_number="+15550004000",
        to_number="+15550001000",
    )
    db.add(call)
    await db.flush()
    row = WrapupDraft(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        call_id=call.id,
        agent_user_id=agent.id,
        status="draft",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(row)
    await db.commit()
    return row


async def test_approved_wrapup_queues_redacted_crm_delivery_and_requires_ack(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    row = await wrapup_draft(db, cc2_scope, agent_a)
    db.add(CrmIntegration(
        tenant_id=cc2_scope.tenant.id,
        provider=CrmProviderType.HUBSPOT,
        is_enabled=True,
        subscribed_events=["call.completed"],
        share_transcripts=False,
    ))
    await db.commit()

    submitted = await submit_call_wrapup(
        db,
        tenant_id=cc2_scope.tenant.id,
        agent_user_id=agent_a.id,
        call_id=row.call_id,
        disposition="follow_up",
        summary_redacted="Customer asked for a callback; email person@example.com",
        tags=["Callback"],
    )
    assert submitted.status == "submitted"
    assert submitted.crm_status == "pending"
    event = await db.scalar(select(CrmEvent).where(CrmEvent.id == uuid.UUID(submitted.crm_reference)))
    assert event is not None
    assert event.payload["agent_approved"] is True
    assert event.payload["status"] == "follow_up"
    assert "person@example.com" not in event.payload["summary"]
    assert "transcript_reference" not in event.payload
    sync = await db.scalar(select(CrmSync).where(CrmSync.event_id == event.id))
    assert sync is not None and sync.status is CrmSyncStatus.PENDING

    replay = await submit_call_wrapup(
        db,
        tenant_id=cc2_scope.tenant.id,
        agent_user_id=agent_a.id,
        call_id=row.call_id,
        disposition="follow_up",
        summary_redacted="Customer asked for a callback; email person@example.com",
        tags=["Callback"],
    )
    assert replay.crm_reference == submitted.crm_reference
    assert len((await db.execute(select(CrmEvent))).scalars().all()) == 1

    # A status label without a provider object ID is not acknowledged proof.
    sync.status = CrmSyncStatus.SYNCED
    sync.external_id = None
    assert await reconcile_crm_writebacks(db, tenant_id=cc2_scope.tenant.id) == 1
    assert submitted.crm_status == "failed"
    assert await reconcile_crm_writebacks(db, tenant_id=cc2_scope.tenant.id) == 0

    # A canonical operator retry can recover a failed wrap-up directly.
    sync.status = CrmSyncStatus.SYNCED
    sync.external_id = "hubspot-note-ack-1"
    assert await reconcile_crm_writebacks(db, tenant_id=cc2_scope.tenant.id) == 1
    assert submitted.crm_status == "written"
    assert submitted.failure_category == ""


async def test_wrapup_without_configured_crm_is_explicitly_not_requested(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    row = await wrapup_draft(db, cc2_scope, agent_a)
    submitted = await submit_call_wrapup(
        db,
        tenant_id=cc2_scope.tenant.id,
        agent_user_id=agent_a.id,
        call_id=row.call_id,
        disposition="completed",
        summary_redacted="Redacted summary",
    )
    assert submitted.crm_status == "not_requested"
    assert submitted.crm_reference
    assert not (await db.execute(select(CrmSync))).scalars().all()
