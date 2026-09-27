"""Outbound dialer environment scope (Batch 06).

The dialer selects and dials only inside one tenant + environment pair:
``next_callable_leads`` filters on ``Lead.environment_id ==
campaign.environment_id``, and ``place_call`` refuses any tenant or
environment mismatch before a claim is even attempted. DNC, the attempt cap
and the call window keep working exactly as before — the environment gate is
additive, never a relaxation.
"""

from __future__ import annotations

import asyncio
from datetime import time

from sqlalchemy import select

from app.db.models import Campaign, Environment, Lead, LeadStatus, Tenant
from app.leads import lifecycle
from app.telephony.outbound import next_callable_leads, place_call
from tests.conftest import make_lead, make_tenant


# ----------------------------------------------------------------- helpers ---

async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def _staging(db, tenant) -> Environment:
    environment = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


async def _ready_tenant(db, name: str = "Dialer Co"):
    """A tenant that may dial right now: outbound enabled, window all day."""
    tenant = await make_tenant(db, name)
    tenant.outbound_enabled = True
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)
    await db.commit()
    return tenant


async def _campaign(db, tenant, environment_id) -> Campaign:
    campaign = Campaign(
        tenant_id=tenant.id, environment_id=environment_id,
        name="Sweep", is_active=True, calls_per_minute=5,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


# ---------------------------------------------------------------- selection ---

async def test_selection_is_restricted_to_the_campaign_environment(db):
    tenant = await _ready_tenant(db)
    production = await _production(db, tenant)
    staging = await _staging(db, tenant)
    campaign = await _campaign(db, tenant, production.id)

    in_scope = await make_lead(
        db, tenant, phone="+15550700001", campaign_id=campaign.id
    )
    await make_lead(  # staging lead pointing at the production campaign
        db, tenant, phone="+15550700002", campaign_id=campaign.id,
        environment_id=staging.id,
    )
    await make_lead(db, tenant, phone="+15550700003")  # no campaign at all

    leads = await next_callable_leads(db, tenant, campaign)
    assert [lead.id for lead in leads] == [in_scope.id]


async def test_staging_campaign_selects_only_staging_leads(db):
    tenant = await _ready_tenant(db)
    staging = await _staging(db, tenant)
    campaign = await _campaign(db, tenant, staging.id)

    await make_lead(db, tenant, phone="+15550700004", campaign_id=campaign.id)
    in_scope = await make_lead(
        db, tenant, phone="+15550700005", campaign_id=campaign.id,
        environment_id=staging.id,
    )

    leads = await next_callable_leads(db, tenant, campaign)
    assert [lead.id for lead in leads] == [in_scope.id]


async def test_dnc_and_attempt_cap_still_exclude_leads(db):
    tenant = await _ready_tenant(db)
    production = await _production(db, tenant)
    campaign = await _campaign(db, tenant, production.id)

    dnc_lead = await make_lead(
        db, tenant, phone="+15550700006", campaign_id=campaign.id
    )
    await lifecycle.transition(
        db, dnc_lead, "do_not_call", reason="opt-out", source="test"
    )
    capped = await make_lead(
        db, tenant, phone="+15550700007", campaign_id=campaign.id
    )
    capped.attempts = tenant.max_call_attempts
    ok_lead = await make_lead(
        db, tenant, phone="+15550700008", campaign_id=campaign.id
    )
    await db.commit()

    leads = await next_callable_leads(db, tenant, campaign)
    assert [lead.id for lead in leads] == [ok_lead.id]


# ------------------------------------------------------------------ dialing ---

async def test_same_environment_pair_dials(db):
    tenant = await _ready_tenant(db)
    production = await _production(db, tenant)
    campaign = await _campaign(db, tenant, production.id)
    lead = await make_lead(
        db, tenant, phone="+15550700009", campaign_id=campaign.id
    )

    result = await place_call(db, tenant, campaign, lead, dry_run=True)
    assert result["ok"] is True and result["dry_run"] is True
    await db.refresh(lead)
    assert lead.status is LeadStatus.QUEUED
    assert lead.attempts == 1


async def test_cross_environment_lead_is_refused_even_when_directly_handed(db):
    """The scheduler only offers in-scope leads, but a caller that hands the
    dialer a cross-environment pair directly still gets refused — before any
    claim, attempt or provider interaction."""
    tenant = await _ready_tenant(db)
    production = await _production(db, tenant)
    staging = await _staging(db, tenant)
    campaign = await _campaign(db, tenant, production.id)
    staging_lead = await make_lead(
        db, tenant, phone="+15550700010", campaign_id=campaign.id,
        environment_id=staging.id,
    )

    result = await place_call(db, tenant, campaign, staging_lead, dry_run=True)
    assert result == {"ok": False, "reason": "environment_mismatch"}
    await db.refresh(staging_lead)
    assert staging_lead.attempts == 0
    assert staging_lead.status is LeadStatus.NEW


async def test_cross_environment_campaign_is_refused(db):
    tenant = await _ready_tenant(db)
    staging = await _staging(db, tenant)
    campaign = await _campaign(db, tenant, staging.id)
    production_lead = await make_lead(
        db, tenant, phone="+15550700011", campaign_id=campaign.id
    )

    result = await place_call(db, tenant, campaign, production_lead, dry_run=True)
    assert result == {"ok": False, "reason": "environment_mismatch"}
    await db.refresh(production_lead)
    assert production_lead.attempts == 0


async def test_foreign_tenant_lead_is_refused(db):
    tenant = await _ready_tenant(db, "Dialer Co")
    other = await _ready_tenant(db, "Other Co")
    production = await _production(db, tenant)
    campaign = await _campaign(db, tenant, production.id)
    foreign_lead = await make_lead(db, other, phone="+15550700012")

    result = await place_call(db, tenant, campaign, foreign_lead, dry_run=True)
    assert result == {"ok": False, "reason": "tenant_mismatch"}


async def test_dnc_lead_is_refused_inside_the_same_environment(db):
    tenant = await _ready_tenant(db)
    production = await _production(db, tenant)
    campaign = await _campaign(db, tenant, production.id)
    lead = await make_lead(
        db, tenant, phone="+15550700013", campaign_id=campaign.id
    )
    await lifecycle.transition(db, lead, "do_not_call", reason="opt-out", source="test")
    await db.commit()

    result = await place_call(db, tenant, campaign, lead, dry_run=True)
    assert result == {"ok": False, "reason": "do_not_call"}
    assert lead.attempts == 0
    assert lead.status is LeadStatus.DNC  # never silently reversed


# ---------------------------------------------------------------- concurrency ---

async def test_parallel_ticks_claim_each_lead_once(concurrent_sessionmaker):
    """Two overlapping campaign ticks over the same campaign: every lead is
    dialed exactly once and the environment filter holds for both tickers.

    Both ticks take their selection snapshot first (the real overlap that
    happens when two scheduler ticks run concurrently), then dial. Every
    claim is the atomic compare-and-set on ``attempts``, so exactly one tick
    wins each lead and the loser gets ``claimed_by_another``.
    """
    async with concurrent_sessionmaker() as session:
        tenant = await _ready_tenant(session, "Race Dialer Co")
        production = await _production(session, tenant)
        staging = await _staging(session, tenant)
        campaign = await _campaign(session, tenant, production.id)
        lead_ids = [
            (await make_lead(
                session, tenant, phone=f"+155507010{i:02d}", campaign_id=campaign.id
            )).id
            for i in range(3)
        ]
        staging_lead_id = (await make_lead(
            session, tenant, phone="+15550701099", campaign_id=campaign.id,
            environment_id=staging.id,
        )).id
        tenant_id, campaign_id = tenant.id, campaign.id

    async def select_phase(session):
        tenant = await session.get(Tenant, tenant_id)
        campaign = await session.get(Campaign, campaign_id)
        return tenant, campaign, await next_callable_leads(session, tenant, campaign)

    async def dial_phase(session, tenant, campaign, leads):
        results = [
            await place_call(session, tenant, campaign, lead, dry_run=True)
            for lead in leads
        ]
        await session.commit()
        return results

    async with concurrent_sessionmaker() as first, concurrent_sessionmaker() as second:
        snapshot_a = await select_phase(first)
        snapshot_b = await select_phase(second)
        # both ticks saw the same three in-scope leads (staging lead excluded)
        assert [lead.id for lead in snapshot_a[2]] == lead_ids
        assert [lead.id for lead in snapshot_b[2]] == lead_ids

        results_a, results_b = await asyncio.gather(
            dial_phase(first, *snapshot_a), dial_phase(second, *snapshot_b)
        )

    results = results_a + results_b
    wins = [r for r in results if r.get("ok")]
    losses = [r for r in results if not r.get("ok")]
    assert len(wins) == 3  # each production lead dialed exactly once
    assert all(r["reason"] == "claimed_by_another" for r in losses)
    assert len(losses) == 3

    async with concurrent_sessionmaker() as session:
        rows = (
            await session.execute(select(Lead).where(Lead.id.in_(lead_ids)))
        ).scalars().all()
        assert all(row.attempts == 1 for row in rows)
        assert all(row.status is LeadStatus.QUEUED for row in rows)
        staging_lead = await session.get(Lead, staging_lead_id)
        assert staging_lead.attempts == 0
        assert staging_lead.status is LeadStatus.NEW
