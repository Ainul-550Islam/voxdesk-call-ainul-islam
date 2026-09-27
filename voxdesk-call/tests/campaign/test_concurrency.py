"""Concurrency contracts for campaigns and leads (Batch 06).

Races are tested against the file-backed ``concurrent_sessionmaker`` so two
sessions really hold two connections, like PostgreSQL in production. The
contracts: a dial attempt is claimed exactly once; lead status transitions
are compare-and-set (a stale reader loses instead of overwriting); a
cross-environment pair can never be claimed no matter how the race goes;
planning is deterministic across concurrent readers; and the campaign state
machine cannot be driven into an illegal state by concurrent transitions.
"""

from __future__ import annotations

import asyncio
from datetime import time

import pytest
from sqlalchemy import select

from app.db.models import Campaign, Environment, Lead, LeadStatus, Tenant
from app.leads import lifecycle
from app.leads.activities import history_for
from app.leads.exceptions import ClaimConflict
from app.services import campaign_service
from app.telephony.outbound import place_call
from tests.conftest import make_lead, make_tenant


# ----------------------------------------------------------------- helpers ---

async def _seed(sessionmaker) -> dict:
    """Tenant with a production + staging environment, a campaign and leads."""
    async with sessionmaker() as session:
        tenant = await make_tenant(session, "Race Co")
        tenant.outbound_enabled = True
        tenant.outbound_window_open = time(0, 0)
        tenant.outbound_window_close = time(23, 59, 59)
        await session.commit()

        production = (
            await session.execute(
                select(Environment).where(
                    Environment.tenant_id == tenant.id,
                    Environment.kind == "production",
                )
            )
        ).scalar_one()
        staging = Environment(
            tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
            status="active", is_default=False,
        )
        session.add(staging)
        await session.commit()
        await session.refresh(staging)

        campaign = Campaign(
            tenant_id=tenant.id, environment_id=production.id,
            name="Race campaign", is_active=True, calls_per_minute=5,
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)

        lead = await make_lead(
            session, tenant, phone="+15550300001", campaign_id=campaign.id
        )
        staging_lead = await make_lead(
            session, tenant, phone="+15550300002", campaign_id=campaign.id,
            environment_id=staging.id,
        )
        return {
            "tenant": tenant.id,
            "campaign": campaign.id,
            "lead": lead.id,
            "staging_lead": staging_lead.id,
            "production": production.id,
            "staging": staging.id,
        }


async def _load(session, ids, lead_key: str = "lead"):
    tenant = await session.get(Tenant, ids["tenant"])
    campaign = await session.get(Campaign, ids["campaign"])
    lead = await session.get(Lead, ids[lead_key])
    return tenant, campaign, lead


# ------------------------------------------------------------- dial claims ---

async def test_concurrent_dial_claims_the_lead_exactly_once(concurrent_sessionmaker):
    ids = await _seed(concurrent_sessionmaker)

    async def racer():
        async with concurrent_sessionmaker() as session:
            tenant, campaign, lead = await _load(session, ids)
            return await place_call(session, tenant, campaign, lead, dry_run=True)

    results = await asyncio.gather(racer(), racer())
    winners = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(winners) == 1, results
    assert winners[0]["dry_run"] is True
    assert len(losers) == 1
    assert losers[0]["reason"] == "claimed_by_another"

    async with concurrent_sessionmaker() as session:
        lead = await session.get(Lead, ids["lead"])
        assert lead.attempts == 1  # exactly one attempt burned
        assert lead.status is LeadStatus.QUEUED


async def test_cross_environment_pair_is_never_claimed_under_a_race(
    concurrent_sessionmaker,
):
    """Production campaign + staging lead: both racers must refuse, and the
    lead must be untouched afterwards — the environment gate is not a
    check-then-act race the loser can slip through."""
    ids = await _seed(concurrent_sessionmaker)

    async def racer():
        async with concurrent_sessionmaker() as session:
            tenant, campaign, lead = await _load(session, ids, "staging_lead")
            return await place_call(session, tenant, campaign, lead, dry_run=True)

    results = await asyncio.gather(racer(), racer())
    assert all(r == {"ok": False, "reason": "environment_mismatch"} for r in results)

    async with concurrent_sessionmaker() as session:
        lead = await session.get(Lead, ids["staging_lead"])
        assert lead.attempts == 0
        assert lead.status is LeadStatus.NEW
        history = await history_for(
            session, lead.tenant_id, lead.environment_id, lead.id
        )
        assert history == []


# ------------------------------------------------------- status transitions ---

async def test_concurrent_transitions_are_compare_and_set(concurrent_sessionmaker):
    """Two sessions believe NEW; one wins, the loser gets ClaimConflict and
    the winner's target is the only recorded transition."""
    ids = await _seed(concurrent_sessionmaker)

    async def racer(target: str):
        async with concurrent_sessionmaker() as session:
            lead = await session.get(Lead, ids["lead"])
            try:
                await lifecycle.transition(
                    session, lead, target,
                    reason="race", source="test", expected=LeadStatus.NEW,
                )
                await session.commit()
                return ("ok", target)
            except ClaimConflict:
                await session.rollback()
                return ("conflict", target)

    results = await asyncio.gather(racer("queued"), racer("called"))
    outcomes = sorted(outcome for outcome, _ in results)
    assert outcomes == ["conflict", "ok"], results
    winner = next(target for outcome, target in results if outcome == "ok")

    async with concurrent_sessionmaker() as session:
        lead = await session.get(Lead, ids["lead"])
        assert lifecycle.status_value(lead.status) == winner
        history = await history_for(
            session, lead.tenant_id, lead.environment_id, lead.id
        )
        assert len(history) == 1
        assert history[0].from_status == "new"
        assert history[0].to_status == winner
        assert history[0].sequence == 1


async def test_repeat_transition_after_claim_loss_is_still_valid(
    concurrent_sessionmaker,
):
    """The loser of a claim race may re-read and transition from the NEW
    actual state — compare-and-set loses a race, it does not corrupt."""
    ids = await _seed(concurrent_sessionmaker)

    async with concurrent_sessionmaker() as first:
        lead = await first.get(Lead, ids["lead"])
        await lifecycle.transition(
            first, lead, "called", reason="first", source="test",
            expected=LeadStatus.NEW,
        )
        await first.commit()

    async with concurrent_sessionmaker() as second:
        lead = await second.get(Lead, ids["lead"])
        # stale belief: expected NEW, actual CALLED
        with pytest.raises(ClaimConflict):
            await lifecycle.transition(
                second, lead, "qualified", reason="stale", source="test",
                expected=LeadStatus.NEW,
            )
        await second.rollback()
        await second.refresh(lead)  # the rollback expired the row
        # fresh belief: expected CALLED, actual CALLED
        await lifecycle.transition(
            second, lead, "qualified", reason="fresh", source="test",
            expected=LeadStatus.CALLED,
        )
        await second.commit()
        assert lead.status is LeadStatus.QUALIFIED


# ------------------------------------------------------------- planning ---

async def test_concurrent_plans_are_deterministic_and_identical(
    concurrent_sessionmaker,
):
    ids = await _seed(concurrent_sessionmaker)
    campaign_service._overlay(ids["tenant"], ids["campaign"])["audience"] = {
        "segment_ids": [], "lead_ids": [str(ids["lead"])],
    }

    async def planner():
        async with concurrent_sessionmaker() as session:
            tenant = await session.get(Tenant, ids["tenant"])
            definition = await campaign_service.get_campaign(
                session, tenant, str(ids["campaign"])
            )
            intents = await campaign_service.execution_plan(session, tenant, definition)
            return [(i.id, i.lead_id, i.skipped, i.reason_skipped) for i in intents]

    first, second = await asyncio.gather(planner(), planner())
    assert first == second
    assert len(first) == 1
    assert first[0][1] == str(ids["lead"])
    assert first[0][2] is False


# ------------------------------------------------------- campaign states ---

async def test_concurrent_state_transitions_never_produce_an_illegal_state(
    concurrent_sessionmaker,
):
    """schedule (draft→scheduled) racing cancel (draft→cancelled): whatever
    interleaving happens, the stored state is one of the two legal results,
    a cancelled campaign is never re-scheduled afterwards, and is_active
    stays consistent with the final state."""
    ids = await _seed(concurrent_sessionmaker)

    async def mutator(action):
        async with concurrent_sessionmaker() as session:
            tenant = await session.get(Tenant, ids["tenant"])
            try:
                definition = await action(session, tenant, str(ids["campaign"]))
                return ("ok", definition.state.value)
            except Exception as exc:  # BadRequestError on an illegal move
                return ("rejected", type(exc).__name__)

    results = await asyncio.gather(
        mutator(campaign_service.schedule_campaign),
        mutator(campaign_service.cancel_campaign),
    )
    assert any(outcome == "ok" for outcome, _ in results), results

    async with concurrent_sessionmaker() as session:
        tenant = await session.get(Tenant, ids["tenant"])
        final = await campaign_service.get_campaign(session, tenant, str(ids["campaign"]))
        assert final.state.value in {"scheduled", "cancelled"}
        row = await session.get(Campaign, ids["campaign"])
        assert row.is_active is (final.state.value == "running")

        # and the machine stays closed: a cancelled campaign cannot be revived
        if final.state.value == "cancelled":
            with pytest.raises(Exception) as info:
                await campaign_service.schedule_campaign(
                    session, tenant, str(ids["campaign"])
                )
            assert "cannot move campaign" in str(info.value)


async def test_concurrent_schedule_is_idempotent(concurrent_sessionmaker):
    ids = await _seed(concurrent_sessionmaker)

    async def scheduler():
        async with concurrent_sessionmaker() as session:
            tenant = await session.get(Tenant, ids["tenant"])
            definition = await campaign_service.schedule_campaign(
                session, tenant, str(ids["campaign"])
            )
            return definition.state.value

    results = await asyncio.gather(scheduler(), scheduler())
    assert results == ["scheduled", "scheduled"]
