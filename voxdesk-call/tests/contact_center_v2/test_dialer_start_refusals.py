from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer.dialer_service import start_campaign
from app.contact_center_v2.errors import ContactCenterV2Error
from app.db.models import DurableJob, User


async def test_start_refusal_leaves_campaign_draft_and_enqueues_no_job(
    db: AsyncSession, policy_factory, lead_factory, agent_a: User,
):
    await lead_factory()
    policy = await policy_factory(mode="progressive")
    before = await db.scalar(select(func.count()).select_from(DurableJob))
    with pytest.raises(ContactCenterV2Error) as raised:
        await start_campaign(
            db, tenant_id=policy.tenant_id, policy_id=policy.id, actor_id=agent_a.id
        )
    assert raised.value.code == "no_available_agent"
    assert policy.status == "draft"
    assert await db.scalar(select(func.count()).select_from(DurableJob)) == before


async def test_ai_start_refuses_unresolvable_runtime_and_fractional_pacing(
    db: AsyncSession,
    policy_factory,
    lead_factory,
    agent_a: User,
    monkeypatch: pytest.MonkeyPatch,
):
    await lead_factory()
    unavailable = await policy_factory(mode="ai_agent_blast")

    async def missing_runtime(*args, **kwargs):
        raise LookupError("no production AI agent")

    monkeypatch.setattr(
        "app.runtime.agent_config_resolver.resolve_runtime_config", missing_runtime
    )
    with pytest.raises(ContactCenterV2Error) as raised:
        await start_campaign(
            db, tenant_id=unavailable.tenant_id, policy_id=unavailable.id, actor_id=agent_a.id
        )
    assert raised.value.code == "ai_agent_unavailable"
    assert unavailable.status == "draft"

    unavailable.pacing_ratio = Decimal("1.5")
    await db.commit()
    with pytest.raises(ContactCenterV2Error) as raised:
        await start_campaign(
            db, tenant_id=unavailable.tenant_id, policy_id=unavailable.id, actor_id=agent_a.id
        )
    assert raised.value.code == "ai_pacing_invalid"
    assert unavailable.status == "draft"


async def test_cap_above_three_percent_requires_audited_jurisdiction_override(
    db: AsyncSession, policy_factory, lead_factory, agent_a: User,
):
    await lead_factory()
    policy = await policy_factory(abandonment_cap=Decimal("0.031"))
    with pytest.raises(ContactCenterV2Error) as raised:
        await start_campaign(
            db, tenant_id=policy.tenant_id, policy_id=policy.id, actor_id=agent_a.id
        )
    assert raised.value.code == "jurisdiction_override_required"
    assert policy.status == "draft"
