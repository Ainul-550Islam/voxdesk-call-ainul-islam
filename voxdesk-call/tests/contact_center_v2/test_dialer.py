import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer import (
    execute_contact,
    preview_dial,
    preview_next,
    run_tick,
    start_campaign,
)
from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.policy import prepare_contacts
from app.db.contact_center_v2_models import AgentReservation, DialerAttempt, DialerContact
from app.db.models import Call, User


async def test_predictive_start_refuses_without_registered_available_agent(
    db: AsyncSession, policy_factory, lead_factory, agent_a: User,
):
    await lead_factory()
    policy = await policy_factory()
    with pytest.raises(ContactCenterV2Error) as raised:
        await start_campaign(db, tenant_id=policy.tenant_id, policy_id=policy.id, actor_id=agent_a.id)
    assert raised.value.code == "no_available_agent"
    assert policy.status == "draft"
    assert not (await db.execute(select(DialerAttempt))).scalars().all()


async def test_provider_ambiguity_blocks_contact_and_never_auto_retries(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a: User,
    available_presence,
    softphone_registration,
    monkeypatch: pytest.MonkeyPatch,
):
    await lead_factory()
    policy = await policy_factory(mode="progressive")
    policy.status = "running"
    await prepare_contacts(db, policy)
    contact = (await db.execute(select(DialerContact))).scalar_one()
    calls = 0

    async def ambiguous(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"ok": False, "reason": "dial_failed"}

    monkeypatch.setattr("app.telephony.outbound.place_call", ambiguous)
    attempt = await execute_contact(
        db, policy=policy, contact=contact, actor_agent_id=agent_a.id,
    )
    assert attempt is not None
    assert attempt.outcome == "failed"
    assert attempt.failure_category == "dial_failed"
    await db.refresh(contact)
    assert contact.state == "blocked"
    assert contact.block_reason == "provider_ambiguous"
    assert not (await db.execute(select(AgentReservation).where(AgentReservation.status == "active"))).scalars().all()
    tick = await run_tick(db, tenant_id=policy.tenant_id, policy_id=policy.id)
    assert tick["attempted"] == 0
    assert calls == 1


async def test_preview_requires_agent_click_and_records_only_acknowledged_ringing(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a: User,
    available_presence,
    softphone_registration,
    monkeypatch: pytest.MonkeyPatch,
):
    lead = await lead_factory()
    policy = await policy_factory(mode="preview")
    policy.status = "running"
    await prepare_contacts(db, policy)
    contact, preview_lead = await preview_next(
        db, tenant_id=policy.tenant_id, policy_id=policy.id, agent_id=agent_a.id,
    )
    assert preview_lead.id == lead.id
    provider_sid = f"CA{uuid.uuid4().hex[:30]}"

    async def acknowledged(session, tenant, campaign, dialed_lead, **kwargs):
        call = Call(
            tenant_id=tenant.id,
            environment_id=campaign.environment_id,
            call_sid=provider_sid,
            from_number=kwargs["caller_id_number"],
            to_number=dialed_lead.phone,
        )
        session.add(call)
        await session.commit()
        return {"ok": True, "call_sid": provider_sid, "call_id": str(call.id)}

    monkeypatch.setattr("app.telephony.outbound.place_call", acknowledged)
    assert (await run_tick(db, tenant_id=policy.tenant_id, policy_id=policy.id))["reason"] == "agent_click_required"
    first = await preview_dial(
        db, tenant_id=policy.tenant_id, contact_id=contact.id, agent_id=agent_a.id,
        idempotency_key="preview-click-1",
    )
    replay = await preview_dial(
        db, tenant_id=policy.tenant_id, contact_id=contact.id, agent_id=agent_a.id,
        idempotency_key="preview-click-1",
    )
    assert first is not None
    assert replay.id == first.id
    assert first.outcome == "ringing"
    assert first.provider_call_id == provider_sid
    assert first.answered_at is None
    assert len((await db.execute(select(DialerAttempt))).scalars().all()) == 1


async def test_ai_blast_starts_and_dials_without_human_agent_reservation(
    db: AsyncSession, policy_factory, lead_factory, agent_a: User, monkeypatch: pytest.MonkeyPatch,
):
    lead = await lead_factory()
    policy = await policy_factory(mode="ai_agent_blast")

    async def resolved(*args, **kwargs):
        return object()

    monkeypatch.setattr("app.runtime.agent_config_resolver.resolve_runtime_config", resolved)
    started, _ = await start_campaign(db, tenant_id=policy.tenant_id, policy_id=policy.id, actor_id=agent_a.id)
    assert started.status == "running"
    provider_sid = f"CA{uuid.uuid4().hex[:30]}"
    seen = {}

    async def acknowledged(session, tenant, campaign, dialed_lead, **kwargs):
        seen.update(kwargs)
        call = Call(
            tenant_id=tenant.id, environment_id=campaign.environment_id,
            call_sid=provider_sid, from_number=kwargs["caller_id_number"], to_number=dialed_lead.phone,
            lead_id=dialed_lead.id,
        )
        session.add(call)
        await session.commit()
        return {"ok": True, "call_sid": provider_sid, "call_id": str(call.id)}

    monkeypatch.setattr("app.telephony.outbound.place_call", acknowledged)
    result = await run_tick(db, tenant_id=policy.tenant_id, policy_id=policy.id)
    assert result["attempted"] == 1
    assert result["pacing_plan"]["mode"] == "ai_agent_blast"
    assert f"campaign_id={policy.campaign_id}" in seen["answer_url"]
    assert "dialer:" not in seen["answer_url"]
    attempt = (await db.execute(select(DialerAttempt))).scalar_one()
    assert attempt.mode == "ai_agent_blast" and attempt.agent_user_id is None
    assert not (await db.execute(select(AgentReservation).where(AgentReservation.status == "active"))).scalars().all()
    assert lead.id == attempt.lead_id


async def test_ai_blast_rejects_fractional_pacing_before_start(
    db: AsyncSession, policy_factory, lead_factory, agent_a: User,
):
    await lead_factory()
    policy = await policy_factory(mode="ai_agent_blast", pacing_ratio=Decimal("0.5"))
    with pytest.raises(ContactCenterV2Error) as raised:
        await start_campaign(db, tenant_id=policy.tenant_id, policy_id=policy.id, actor_id=agent_a.id)
    assert raised.value.code == "ai_pacing_invalid"
    assert policy.status == "draft"


async def test_cps_token_bucket_refills_without_ever_exceeding_burst(
    monkeypatch: pytest.MonkeyPatch,
):
    from datetime import datetime, timedelta, timezone

    from app.contact_center_v2.dialer.dial_worker import CpsTokenBucket

    bucket = CpsTokenBucket()
    monkeypatch.setattr(bucket, "_client", lambda: None)
    moment = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    assert await bucket.acquire("test", rate=Decimal("2"), burst=2, now=moment)
    assert await bucket.acquire("test", rate=Decimal("2"), burst=2, now=moment)
    assert not await bucket.acquire("test", rate=Decimal("2"), burst=2, now=moment)
    assert await bucket.acquire(
        "test", rate=Decimal("2"), burst=2, now=moment + timedelta(milliseconds=500)
    )
