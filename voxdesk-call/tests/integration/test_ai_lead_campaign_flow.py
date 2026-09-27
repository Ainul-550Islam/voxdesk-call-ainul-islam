"""P0 master consolidation — AI -> Lead -> Campaign -> outbound, end to end.

Each subsystem is tested individually elsewhere (governed runtime in
``tests/ai``, lifecycle in ``tests/leads``, campaign scope in
``tests/campaign``, dialer scope in ``tests/telephony``). This file exists for
the *seams* — the places where one layer's source of truth has to survive
contact with the next:

    a governed AI turn records its measured usage and the lead decision it
    produced  ->  the decision lands in the canonical lifecycle (with consent
    and history)  ->  campaign planning sees exactly the leads of its own
    tenant + environment  ->  the dialer claims and dials only inside that
    same pair, and a do-not-call recorded by *any* source (AI tool, messaging
    webhook) is terminal for every later layer.

The negatives are the point: a cross-tenant tool call, a cross-environment
campaign audience and any attempt to reverse a DNC must fail closed with the
safe error, leaving the underlying rows untouched.
"""

from __future__ import annotations

import uuid
from datetime import time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agent.functions import FunctionHandlers
from app.ai.guardrails.tool_policy import authorize
from app.core.errors import NotFoundError
from app.db.models import Campaign, Environment, Lead, LeadStatus, UsageEvent, UsageMetric
from app.domain.campaign_models import Audience, CampaignDefinition
from app.leads import lifecycle
from app.leads.activities import history_for
from app.leads.consent import record_consent, voice_denied
from app.leads.exceptions import InvalidTransition
from app.leads.models import LeadConsent
from app.services import campaign_service
from app.telephony.outbound import next_callable_leads, place_call
from tests.acd_support import live_call
from tests.conftest import make_lead, make_tenant

pytestmark = pytest.mark.asyncio

CALLER = "+15551230000"  # the fixed from_number of tests.acd_support.live_call


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


async def _ready_tenant(db, name: str, *, preset: str = "fast"):
    """A tenant that may dial right now and answers on the OpenAI preset."""
    tenant = await make_tenant(db, name)
    tenant.outbound_enabled = True
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)
    tenant.llm_preset = preset
    await db.commit()
    return tenant


async def _campaign_row(db, tenant, environment_id, name="Sweep") -> Campaign:
    campaign = Campaign(
        tenant_id=tenant.id, environment_id=environment_id,
        name=name, is_active=True, calls_per_minute=5,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


def _definition(tenant_id: str, *, lead_ids=(), name: str = "Flow outreach") -> CampaignDefinition:
    return CampaignDefinition(
        id=f"campaign-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        name=name,
        audience=Audience(
            tenant_id=tenant_id,
            lead_ids=tuple(str(x) for x in lead_ids),
        ),
    )


async def _lead_by_phone(db, tenant, phone: str, environment_id=None) -> Lead | None:
    stmt = select(Lead).where(Lead.tenant_id == tenant.id, Lead.phone == phone)
    if environment_id is not None:
        stmt = stmt.where(Lead.environment_id == environment_id)
    return (await db.execute(stmt)).scalars().first()


def _fake_openai_client(monkeypatch, reply: str, *, total_tokens: int):
    async def create(**_kwargs):
        message = SimpleNamespace(
            content=reply,
            tool_calls=None,
            model_dump=lambda exclude_none=False: {"role": "assistant", "content": reply},
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(total_tokens=total_tokens),
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(
        "app.agent.text_agent._openai_style_client", lambda provider, api_key: client
    )


# ------------------------------------------------------- AI tool -> every layer ---

async def test_ai_dnc_decision_holds_through_lifecycle_campaign_and_dialer(db):
    """A do-not-call spoken to the AI agent on a live call is one decision with
    one owner: the tool writes consent, consent drives the lifecycle, and every
    downstream layer — campaign selection, the dialer, any later status change
    — honours it. No layer may reverse it."""
    tenant = await _ready_tenant(db, "Flow Co")
    env = await _production(db, tenant)
    call = await live_call(db, tenant, env)

    handlers = FunctionHandlers(db, tenant, call)
    result = await handlers.dispatch("mark_do_not_call", {"reason": "please stop calling"})
    assert result["ok"] is True

    lead = await _lead_by_phone(db, tenant, CALLER)
    assert lead is not None
    assert lead.environment_id == env.id          # created inside the call's environment
    assert lead.status is LeadStatus.DNC          # written by the lifecycle, not the tool
    assert await voice_denied(db, tenant.id, lead.id) is True

    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [
        (None, "new"), ("new", "do_not_call"),
    ]
    assert history[0].source == "agent_tool"
    assert history[1].source == "consent"

    # The campaign layer: the DNC lead is never selectable, and a direct hand-off
    # to the dialer is refused before any claim, attempt or provider interaction.
    campaign = await _campaign_row(db, tenant, env.id)
    lead.campaign_id = campaign.id
    await db.commit()
    assert await next_callable_leads(db, tenant, campaign) == []
    assert await place_call(db, tenant, campaign, lead, dry_run=True) == {
        "ok": False, "reason": "do_not_call",
    }

    # Reversal is denied from every source, and the row survives the attempts.
    with pytest.raises(InvalidTransition):
        await lifecycle.transition(db, lead, "new", reason="reopen", source="operator")
    with pytest.raises(InvalidTransition):
        await record_consent(
            db, lead, channel="voice", decision="granted", source="operator"
        )
    await db.commit()
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    await db.refresh(call)
    assert call.intent == "do_not_call"


async def test_cross_tenant_tool_call_never_touches_the_other_tenants_lead(db):
    """Two tenants, one phone number. The AI tool on tenant A's live call is
    scoped to tenant A's environment by the call itself — tenant B's lead with
    the *same* number is never read, never mutated, and the governance layer
    refuses the model as a principal before any tool exists."""
    tenant_a = await _ready_tenant(db, "Owner Co")
    tenant_b = await _ready_tenant(db, "Rival Co")
    env_a = await _production(db, tenant_a)
    lead_b = await make_lead(db, tenant_b, phone=CALLER)

    # The model is never the principal: server-side tool policy refuses first.
    assert authorize("mark_do_not_call", principal="model").allowed is False

    call_a = await live_call(db, tenant_a, env_a)
    handlers = FunctionHandlers(db, tenant_a, call_a)
    result = await handlers.dispatch("mark_do_not_call", {"reason": "stop"})
    assert result["ok"] is True

    lead_a = await _lead_by_phone(db, tenant_a, CALLER)
    assert lead_a is not None
    assert lead_a.status is LeadStatus.DNC
    assert lead_a.environment_id == env_a.id

    await db.refresh(lead_b)
    assert lead_b.status is LeadStatus.NEW        # untouched
    assert lead_b.tenant_id == tenant_b.id
    consents = (
        await db.execute(
            select(func.count()).select_from(LeadConsent).where(LeadConsent.lead_id == lead_b.id)
        )
    ).scalar_one()
    assert consents == 0


async def test_ai_created_staging_lead_is_invisible_to_a_production_campaign(db):
    """A call in the staging environment produces a staging lead. The production
    campaign — even when handed that lead's id explicitly — refuses the whole
    audience, and the dialer refuses the cross-environment pair directly."""
    tenant = await _ready_tenant(db, "Two Env Co")
    staging = await _staging(db, tenant)
    call = await live_call(db, tenant, staging)

    handlers = FunctionHandlers(db, tenant, call)
    assert (await handlers.dispatch("mark_do_not_call", {"reason": "stop"}))["ok"] is True
    staging_lead = await _lead_by_phone(db, tenant, CALLER, environment_id=staging.id)
    assert staging_lead is not None and staging_lead.status is LeadStatus.DNC

    production_lead = await make_lead(db, tenant, phone="+15552000004")

    # Cross-environment audience member -> the whole create fails closed.
    with pytest.raises(NotFoundError):
        await campaign_service.create_campaign(
            db, tenant,
            _definition(str(tenant.id), lead_ids=[staging_lead.id, production_lead.id]),
        )

    row = await campaign_service.create_campaign(
        db, tenant, _definition(str(tenant.id), lead_ids=[production_lead.id])
    )
    production = await _production(db, tenant)
    assert row.environment_id == production.id
    fetched = await campaign_service.get_campaign(db, tenant, str(row.id))
    validated = await campaign_service.validate_audience(db, tenant, fetched)
    assert validated["lead_count"] == 1
    resolved = await campaign_service.resolve_audience(db, tenant, fetched.audience)
    assert resolved == [str(production_lead.id)]

    # Dialer seam: even a stale campaign_id on the staging lead cannot drag it
    # into the production sweep, and a direct hand-off is refused.
    production_lead.campaign_id = row.id
    staging_lead.campaign_id = row.id
    await db.commit()
    callable_leads = await next_callable_leads(db, tenant, row)
    assert [lead.id for lead in callable_leads] == [production_lead.id]
    assert await place_call(db, tenant, row, staging_lead, dry_run=True) == {
        "ok": False, "reason": "environment_mismatch",
    }


# ------------------------------------------- governed text turn -> lead -> dial ---

async def test_governed_text_reply_records_usage_then_campaign_dials_the_lead(
    client, db, monkeypatch
):
    """The happy chain through the real webhook: a governed AI text turn
    (policy, admission, guardrails, measured usage on the existing billing
    meter) answers a lead's SMS; the same lead then travels through campaign
    planning into the dialer's atomic claim inside one tenant + environment."""
    from app.core.config import settings

    tenant = await _ready_tenant(db, "Text Flow Co")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-used")
    _fake_openai_client(monkeypatch, "we are open today", total_tokens=23)

    lead = await make_lead(db, tenant, phone="+15552000001")
    env = await _production(db, tenant)
    assert lead.environment_id == env.id

    response = await client.post(
        "/channels/message",
        data={
            "From": "+15552000001", "To": tenant.twilio_number,
            "Body": "do you have openings this week?", "MessageSid": "SM-flow-happy-1",
        },
    )
    assert response.status_code == 200
    assert "we are open today" in response.text

    # The governed runtime recorded the provider-measured tokens on the
    # existing meter, attributed to the tenant's production environment.
    events = (
        await db.execute(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant.id,
                UsageEvent.metric == UsageMetric.LLM_TOKEN,
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].quantity == 23
    assert events[0].environment_id == env.id

    # Campaign planning over the same lead, through the service layer.
    row = await campaign_service.create_campaign(
        db, tenant, _definition(str(tenant.id), lead_ids=[lead.id])
    )
    assert row.environment_id == env.id
    fetched = await campaign_service.get_campaign(db, tenant, str(row.id))
    assert (await campaign_service.validate_audience(db, tenant, fetched))["lead_count"] == 1

    # Outbound: the same-environment pair dials, exactly once, via the atomic claim.
    lead.campaign_id = row.id
    await db.commit()
    assert [x.id for x in await next_callable_leads(db, tenant, row)] == [lead.id]
    dial = await place_call(db, tenant, row, lead, dry_run=True)
    assert dial["ok"] is True and dial["dry_run"] is True
    await db.refresh(lead)
    assert lead.status is LeadStatus.QUEUED
    assert lead.attempts == 1
    second = await place_call(db, tenant, row, lead, dry_run=True)
    assert second["ok"] is True           # the claim already moved it to QUEUED
    await db.refresh(lead)
    assert lead.attempts == 2             # each dial is one atomic claim, never a reset


# --------------------------------------------------- messaging source -> dialer ---

async def test_messaging_stop_and_start_never_reverse_the_voice_dnc_for_the_dialer(
    client, db
):
    """A STOP over SMS creates the lead and the DNC through consent + lifecycle.
    A later START restores SMS consent only — the voice do-not-call survives it,
    the dialer still refuses, and no campaign source may transition the lead out
    of the terminal state."""
    tenant = await _ready_tenant(db, "Consent Flow Co")
    env = await _production(db, tenant)

    stopped = await client.post(
        "/channels/message",
        data={"From": "+15552000005", "To": tenant.twilio_number, "Body": "STOP",
              "MessageSid": "SM-flow-stop-1"},
    )
    assert stopped.status_code == 200
    lead = await _lead_by_phone(db, tenant, "+15552000005")
    assert lead is not None
    assert lead.status is LeadStatus.DNC
    assert lead.environment_id == env.id

    started = await client.post(
        "/channels/message",
        data={"From": "+15552000005", "To": tenant.twilio_number, "Body": "START",
              "MessageSid": "SM-flow-start-1"},
    )
    assert started.status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC           # START never reverses a voice DNC
    assert await voice_denied(db, tenant.id, lead.id) is True
    consents = (
        await db.execute(
            select(LeadConsent)
            .where(LeadConsent.lead_id == lead.id)
            .order_by(LeadConsent.recorded_at, LeadConsent.version)
        )
    ).scalars().all()
    assert [(c.channel, c.decision, c.source) for c in consents] == [
        ("voice", "denied", "messaging:stop"),
        ("sms", "granted", "messaging:start"),
    ]

    # The dialer and the lifecycle agree with the consent ledger.
    campaign = await _campaign_row(db, tenant, env.id)
    lead.campaign_id = campaign.id
    await db.commit()
    assert await next_callable_leads(db, tenant, campaign) == []
    assert await place_call(db, tenant, campaign, lead, dry_run=True) == {
        "ok": False, "reason": "do_not_call",
    }
    with pytest.raises(InvalidTransition):
        await lifecycle.transition(db, lead, "called", reason="sweep", source="campaign")
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
