"""Campaign service (Batch 01 enterprise expansion; Batch 06 environment scope).

Manages campaigns on top of the existing ``Campaign``/``Lead``/``Tenant``
tables and the outbound safety helpers. The cardinal rule:

**This service never places a call.** Scheduling, audience resolution,
eligibility and planning all produce *execution intents* — deterministic,
idempotent units of work that the existing outbound mechanism may pick up.
DNC checks, call-window checks, daily limits and attempt limits are enforced
here with the same helpers ``app/telephony/outbound.py`` uses, and this module
cannot weaken them: the compliance gate only ever makes requirements stricter.

Batch 06 (campaign ↔ environment integrity): every campaign row carries
``environment_id`` (migration ``0026_campaign_environment_scope``), so a
campaign belongs to exactly one tenant + environment pair. Every lookup here
verifies ``campaign.tenant_id == authenticated tenant`` **and**
``campaign.environment_id == effective environment``; audiences (explicit
lead ids and registered segments) resolve only inside the campaign's own
environment, and a cross-environment target fails closed with the same
not-found a cross-tenant campaign gets. Environment resolution reuses the
existing stack (``app.environments.context_resolution`` at the API edge,
``app.leads.repository.production_environment_id`` for the headless default)
— this module invents no resolver and never fabricates an environment.

Persistence honesty: the ``Campaign`` row stores name/goal/script/opening
line/throttle **and environment_id** (real columns). Schedule, audience,
compliance gate and richer state live in the per-tenant overlay that predates
this batch — no columns exist for them yet (schema gap, reported at the end
of the batch); the new environment relationship is *not* overlay state, it is
a durable column with a composite foreign key. Lead eligibility and progress
are computed from real rows.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, time as _time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.models import Campaign, Environment, Lead, LeadStatus, Tenant
from app.domain.campaign_models import (
    Audience,
    CampaignChannel,
    CampaignDefinition,
    CampaignExecutionIntent,
    CampaignGoal,
    CampaignMetrics,
    CampaignSchedule,
    CampaignState,
    ComplianceGate,
    Throttle,
    intent_idempotency_key,
)
from app.environments.resource_binding import assert_environment_accepts_write
from app.leads.repository import production_environment_id
from app.telephony import outbound
from app.tenancy.isolation import NotFound

#: tenant_id -> campaign_id -> non-column campaign state (schedule, audience, ...)
_OVERLAY: dict[str, dict[str, dict]] = {}
#: tenant_id -> segment_id -> segment definition (no segment table yet)
_SEGMENTS: dict[str, dict[str, object]] = {}

_DEFAULT_OPENING_LINE = "Hi, this is {agent} calling from {business}. Do you have a quick minute?"

_WINDOW_MIN_DAILY_START = _time(8, 0)   # TCPA-style floor; tenants cannot call earlier
_WINDOW_MAX_DAILY_END = _time(21, 0)    # and not after 9pm


def _slot(store: dict, *keys: str) -> dict:
    node: dict = store
    for key in keys:
        node = node.setdefault(key, {})
    return node


def _overlay(tenant_id, campaign_id) -> dict:
    return _slot(_OVERLAY, str(tenant_id), str(campaign_id))


def _ensure_owned(
    campaign: Campaign | None, tenant_id, environment_id=None
) -> Campaign:
    """Tenant **and** environment ownership, with one safe not-found for both.

    A campaign from another tenant and a campaign from another environment of
    the same tenant are the same public answer: not found. No boundary detail
    is ever disclosed, and there is no fallback to a different environment.
    """
    if campaign is None or str(campaign.tenant_id) != str(tenant_id):
        raise NotFoundError("campaign not found")
    if environment_id is not None and str(campaign.environment_id) != str(environment_id):
        raise NotFoundError("campaign not found")
    return campaign


async def _resolve_environment(session: AsyncSession, tenant: Tenant, raw) -> Environment:
    """The effective environment for a campaign operation.

    Existing resolution order, fail closed at every step:

    1. an explicit environment id — it must exist *and* belong to this tenant;
       a cross-tenant id is a not-found, never a fallback;
    2. otherwise the tenant's active default production environment (the same
       rule ``app.leads.repository.production_environment_id`` already
       implements for leads);
    3. a tenant with no active production environment is refused — an
       environment is never invented here.

    Suspended/archived environments are still *resolvable* (reads stay
    possible); write paths additionally call
    ``assert_environment_accepts_write``.
    """
    if raw not in (None, ""):
        try:
            parsed = uuid.UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            raise NotFoundError("campaign not found") from None
        environment = await session.get(Environment, parsed)
        if environment is None or str(environment.tenant_id) != str(tenant.id):
            raise NotFoundError("campaign not found")
        return environment
    try:
        production_id = await production_environment_id(session, tenant.id)
    except NotFound:
        raise NotFoundError("campaign not found") from None
    environment = await session.get(Environment, production_id)
    if environment is None:
        raise NotFoundError("campaign not found")
    return environment


def _validate(definition: CampaignDefinition) -> list[str]:
    problems = list(definition.validate())
    # Hard compliance floor: the tenant's window must sit inside the legal one.
    if definition.schedule.daily_start < _WINDOW_MIN_DAILY_START:
        problems.append("daily_start must not be earlier than 08:00")
    if definition.schedule.daily_end > _WINDOW_MAX_DAILY_END:
        problems.append("daily_end must not be later than 21:00")
    return problems


def _validate_binding(definition: CampaignDefinition) -> list[str]:
    """``_validate`` plus the Batch 06 persistence contract.

    A persisted campaign always carries tenant + environment + campaign
    identity, and its audience environment must be the campaign's own.
    """
    problems = _validate(definition)
    if not definition.environment_id:
        problems.append("campaign requires environment_id")
    if (
        definition.audience.environment_id
        and definition.audience.environment_id != definition.environment_id
    ):
        problems.append("audience environment must match the campaign environment")
    return problems


async def _assert_audience_in_environment(
    session: AsyncSession,
    tenant: Tenant,
    definition: CampaignDefinition,
    environment: Environment,
) -> None:
    """Refuse audiences that reach outside the campaign's environment.

    Every explicit lead id must be a lead of this tenant *and* this
    environment; a registered segment must not be bound to a different
    environment. Anything else fails closed with the safe not-found — the
    same answer a cross-tenant campaign gets — so no boundary is revealed
    and nothing is silently retargeted.
    """
    for raw in definition.audience.lead_ids:
        try:
            lead_uuid = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            raise NotFoundError("campaign not found") from None
        lead = await session.get(Lead, lead_uuid)
        if (
            lead is None
            or str(lead.tenant_id) != str(tenant.id)
            or str(lead.environment_id) != str(environment.id)
        ):
            raise NotFoundError("campaign not found")
    for segment_id in definition.audience.segment_ids:
        segment = _resolve_segment_lookup(tenant, segment_id)
        if segment is None:
            # Unknown segment ids resolve to no leads (existing behaviour).
            continue
        segment_env = str(getattr(segment, "environment_id", "") or "")
        if segment_env and segment_env != str(environment.id):
            raise NotFoundError("campaign not found")


# ------------------------------------------------------------------- CRUD ---

async def create_campaign(
    session: AsyncSession,
    tenant: Tenant,
    definition: CampaignDefinition,
    *,
    environment_id=None,
) -> Campaign:
    if definition.tenant_id != str(tenant.id):
        raise BadRequestError("campaign does not belong to this tenant")
    environment = await _resolve_environment(
        session, tenant, definition.environment_id or environment_id
    )
    # A suspended/archived environment never receives a new campaign.
    assert_environment_accepts_write(environment)
    definition = replace(
        definition,
        environment_id=str(environment.id),
        audience=replace(definition.audience, environment_id=str(environment.id)),
    )
    problems = _validate_binding(definition)
    if problems:
        raise BadRequestError("; ".join(problems))
    await _assert_audience_in_environment(session, tenant, definition, environment)
    row = Campaign(
        tenant_id=tenant.id,
        environment_id=environment.id,
        name=definition.name[:200],
        goal=definition.goal.value,
        script_prompt=definition.script_prompt,
        opening_line=definition.opening_line or _DEFAULT_OPENING_LINE,
        calls_per_minute=definition.throttle.calls_per_minute,
        is_active=False,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    _overlay(tenant.id, row.id).update({
        "state": CampaignState.DRAFT.value,
        "schedule": _schedule_to_dict(definition.schedule),
        "audience": {
            "segment_ids": list(definition.audience.segment_ids),
            "lead_ids": list(definition.audience.lead_ids),
        },
        "throttle": {
            "daily_limit": definition.throttle.daily_limit,
            "max_attempts_per_lead": definition.throttle.max_attempts_per_lead,
        },
        "compliance": {
            "require_a2p_registration": definition.compliance.require_a2p_registration,
        },
        "channel": definition.channel.value,
    })
    return row


def _schedule_to_dict(schedule: CampaignSchedule) -> dict:
    return {
        "start_at": schedule.start_at,
        "end_at": schedule.end_at,
        "daily_start": schedule.daily_start.strftime("%H:%M"),
        "daily_end": schedule.daily_end.strftime("%H:%M"),
        "days_of_week": list(schedule.days_of_week),
        "timezone": schedule.timezone,
    }


async def get_campaign(
    session: AsyncSession,
    tenant: Tenant,
    campaign_id: str,
    *,
    environment_id=None,
) -> CampaignDefinition:
    try:
        parsed = uuid.UUID(str(campaign_id))
    except ValueError:
        raise NotFoundError("campaign not found") from None
    row = await session.get(Campaign, parsed)
    environment = await _resolve_environment(session, tenant, environment_id)
    _ensure_owned(row, tenant.id, environment.id)
    overlay = _overlay(tenant.id, row.id)
    schedule = overlay.get("schedule", {})
    audience = overlay.get("audience", {"segment_ids": [], "lead_ids": []})
    throttle = overlay.get("throttle", {"daily_limit": 200, "max_attempts_per_lead": 3})
    return CampaignDefinition(
        id=str(row.id),
        tenant_id=str(tenant.id),
        name=row.name,
        environment_id=str(row.environment_id),
        goal=CampaignGoal(row.goal or "qualify"),
        channel=CampaignChannel(overlay.get("channel", "voice")),
        script_prompt=row.script_prompt or "",
        opening_line=row.opening_line or "",
        audience=Audience(
            tenant_id=str(tenant.id),
            segment_ids=tuple(audience.get("segment_ids", [])),
            lead_ids=tuple(audience.get("lead_ids", [])),
            environment_id=str(row.environment_id),
        ),
        schedule=CampaignSchedule(
            start_at=schedule.get("start_at", ""),
            end_at=schedule.get("end_at", ""),
            daily_start=_parse_hhmm(schedule.get("daily_start", "09:00")),
            daily_end=_parse_hhmm(schedule.get("daily_end", "20:00")),
            days_of_week=tuple(schedule.get("days_of_week", list(range(7)))),
            timezone=schedule.get("timezone", tenant.timezone),
        ),
        throttle=Throttle(
            calls_per_minute=row.calls_per_minute,
            daily_limit=throttle.get("daily_limit", 200),
            max_attempts_per_lead=throttle.get("max_attempts_per_lead", 3),
        ),
        compliance=ComplianceGate(),
        state=CampaignState(overlay.get("state", "draft")),
    )


def _parse_hhmm(value: str) -> _time:
    hour, minute = (value or "09:00").split(":")[:2]
    return _time(int(hour), int(minute))


async def list_campaigns(
    session: AsyncSession, tenant: Tenant, *, environment_id=None
) -> list[CampaignDefinition]:
    environment = await _resolve_environment(session, tenant, environment_id)
    stmt = (
        select(Campaign)
        .where(
            Campaign.tenant_id == tenant.id,
            Campaign.environment_id == environment.id,
        )
        .order_by(Campaign.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        await get_campaign(session, tenant, str(row.id), environment_id=environment.id)
        for row in rows
    ]


async def update_campaign(
    session: AsyncSession,
    tenant: Tenant,
    campaign_id: str,
    definition: CampaignDefinition,
    *,
    environment_id=None,
) -> CampaignDefinition:
    try:
        parsed = uuid.UUID(str(campaign_id))
    except ValueError:
        raise NotFoundError("campaign not found") from None
    row = await session.get(Campaign, parsed)
    environment = await _resolve_environment(session, tenant, environment_id)
    _ensure_owned(row, tenant.id, environment.id)
    assert_environment_accepts_write(environment)
    # The campaign's environment is immutable: the stored row wins over
    # anything the caller supplied, and a mismatch already 404'd above.
    definition = replace(
        definition,
        id=str(row.id),
        environment_id=str(row.environment_id),
        audience=replace(definition.audience, environment_id=str(row.environment_id)),
    )
    problems = _validate_binding(definition)
    if problems:
        raise BadRequestError("; ".join(problems))
    await _assert_audience_in_environment(session, tenant, definition, environment)
    row.name = definition.name[:200]
    row.goal = definition.goal.value
    row.script_prompt = definition.script_prompt
    row.opening_line = definition.opening_line or row.opening_line
    row.calls_per_minute = definition.throttle.calls_per_minute
    session.add(row)
    _overlay(tenant.id, row.id).update({
        "schedule": _schedule_to_dict(definition.schedule),
        "audience": {
            "segment_ids": list(definition.audience.segment_ids),
            "lead_ids": list(definition.audience.lead_ids),
        },
        "throttle": {
            "daily_limit": definition.throttle.daily_limit,
            "max_attempts_per_lead": definition.throttle.max_attempts_per_lead,
        },
        "channel": definition.channel.value,
    })
    await session.commit()
    return await get_campaign(session, tenant, campaign_id, environment_id=row.environment_id)


async def _owned_for_write(
    session: AsyncSession, tenant: Tenant, campaign_id: str, environment_id
) -> tuple[Campaign, Environment]:
    """Load + tenant/environment ownership + writable-environment gate."""
    try:
        parsed = uuid.UUID(str(campaign_id))
    except ValueError:
        raise NotFoundError("campaign not found") from None
    row = await session.get(Campaign, parsed)
    environment = await _resolve_environment(session, tenant, environment_id)
    _ensure_owned(row, tenant.id, environment.id)
    assert_environment_accepts_write(environment)
    return row, environment


async def _set_state(session: AsyncSession, tenant: Tenant, row: Campaign, state: CampaignState) -> CampaignDefinition:
    overlay = _overlay(tenant.id, row.id)
    current = CampaignState(overlay.get("state", "draft"))
    if current is state:
        return await get_campaign(session, tenant, str(row.id), environment_id=row.environment_id)
    if not _can_transition(current, state):
        raise BadRequestError(f"cannot move campaign {current.value} -> {state.value}")
    overlay["state"] = state.value
    row.is_active = state is CampaignState.RUNNING
    session.add(row)
    await session.commit()
    return await get_campaign(session, tenant, str(row.id), environment_id=row.environment_id)


def _can_transition(current: CampaignState, target: CampaignState) -> bool:
    allowed = {
        CampaignState.DRAFT: {CampaignState.SCHEDULED, CampaignState.CANCELLED},
        CampaignState.SCHEDULED: {CampaignState.RUNNING, CampaignState.CANCELLED},
        CampaignState.RUNNING: {CampaignState.PAUSED, CampaignState.COMPLETED, CampaignState.CANCELLED},
        CampaignState.PAUSED: {CampaignState.RUNNING, CampaignState.CANCELLED},
        CampaignState.COMPLETED: set(),
        CampaignState.CANCELLED: set(),
    }
    return target in allowed.get(current, set())


async def schedule_campaign(
    session: AsyncSession, tenant: Tenant, campaign_id: str, *, environment_id=None
) -> CampaignDefinition:
    row, _environment = await _owned_for_write(session, tenant, campaign_id, environment_id)
    return await _set_state(session, tenant, row, CampaignState.SCHEDULED)


async def pause_campaign(
    session: AsyncSession, tenant: Tenant, campaign_id: str, *, environment_id=None
) -> CampaignDefinition:
    row, _environment = await _owned_for_write(session, tenant, campaign_id, environment_id)
    return await _set_state(session, tenant, row, CampaignState.PAUSED)


async def resume_campaign(
    session: AsyncSession, tenant: Tenant, campaign_id: str, *, environment_id=None
) -> CampaignDefinition:
    row, _environment = await _owned_for_write(session, tenant, campaign_id, environment_id)
    if not outbound.is_call_window_open(tenant):
        raise BadRequestError("cannot resume outside the outbound call window")
    return await _set_state(session, tenant, row, CampaignState.RUNNING)


async def cancel_campaign(
    session: AsyncSession, tenant: Tenant, campaign_id: str, *, environment_id=None
) -> CampaignDefinition:
    row, _environment = await _owned_for_write(session, tenant, campaign_id, environment_id)
    return await _set_state(session, tenant, row, CampaignState.CANCELLED)


async def complete_campaign(
    session: AsyncSession, tenant: Tenant, campaign_id: str, *, environment_id=None
) -> CampaignDefinition:
    row, _environment = await _owned_for_write(session, tenant, campaign_id, environment_id)
    return await _set_state(session, tenant, row, CampaignState.COMPLETED)


# -------------------------------------------------------------- eligibility ---

async def validate_audience(
    session: AsyncSession,
    tenant: Tenant,
    definition: CampaignDefinition,
    *,
    environment_id=None,
) -> dict:
    """Count the leads the audience actually resolves to (tenant+environment scoped)."""
    environment = await _resolve_environment(
        session, tenant, definition.environment_id or environment_id
    )
    audience = replace(definition.audience, environment_id=str(environment.id))
    ids = await resolve_audience(session, tenant, audience)
    return {"segment_ids": definition.audience.segment_ids, "lead_count": len(ids)}


def register_segment(tenant: Tenant, segment) -> None:
    """Store a segment definition (overlay; no segment table yet)."""
    _SEGMENTS.setdefault(str(tenant.id), {})[segment.id] = segment


def _resolve_segment_lookup(tenant: Tenant, segment_id: str):
    return _SEGMENTS.get(str(tenant.id), {}).get(segment_id)


def _lead_payload(lead: Lead) -> dict:
    return {
        "name": lead.name or "",
        "company": lead.company or "",
        "status": lead.status.value if lead.status else "",
        "score": lead.score,
        "attempts": lead.attempts,
    }


async def resolve_segment(
    session: AsyncSession, tenant: Tenant, segment, environment: Environment | None = None
) -> list[str]:
    """Resolve a segment definition to lead ids by evaluating its rules.

    Environment-aware: a segment registered for a specific environment
    resolves to nothing when evaluated in a different one, and the lead scan
    itself is filtered to the evaluating environment — a segment can never
    pull another environment's leads into a campaign.
    """
    if environment is None:
        environment = await _resolve_environment(
            session, tenant, getattr(segment, "environment_id", "") or None
        )
    segment_env = str(getattr(segment, "environment_id", "") or "")
    if segment_env and segment_env != str(environment.id):
        return []
    stmt = (
        select(Lead)
        .where(
            Lead.tenant_id == tenant.id,
            Lead.environment_id == environment.id,
        )
        .limit(50_000)
    )
    leads = (await session.execute(stmt)).scalars().all()
    if not segment.rules:
        return [str(lead.id) for lead in leads]
    return [
        str(lead.id) for lead in leads
        if all(rule.matches(_lead_payload(lead)) for rule in segment.rules)
    ]


async def resolve_audience(session: AsyncSession, tenant: Tenant, audience: Audience) -> list[str]:
    """Union of explicit lead ids and segment-resolved ids, deduplicated.

    Environment-aware since Batch 06: an explicit lead id only counts when
    the lead actually exists inside the audience's tenant + environment, and
    segments resolve inside that same environment. An audience without an
    environment id (legacy in-memory use) resolves against the tenant's
    active default production environment — never against "wherever the
    leads happen to be".
    """
    environment = await _resolve_environment(session, tenant, audience.environment_id or None)
    ids: set[str] = set()
    for raw in audience.lead_ids:
        try:
            lead_uuid = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            continue
        lead = await session.get(Lead, lead_uuid)
        if (
            lead is not None
            and str(lead.tenant_id) == str(tenant.id)
            and str(lead.environment_id) == str(environment.id)
        ):
            ids.add(str(lead.id))
    for segment_id in audience.segment_ids:
        segment = _resolve_segment_lookup(tenant, segment_id)
        if segment is not None:
            ids.update(await resolve_segment(session, tenant, segment, environment))
    return sorted(ids)


async def eligibility_check(
    session: AsyncSession,
    tenant: Tenant,
    lead: Lead,
    definition: CampaignDefinition,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """The four safety gates, in order. Any skip returns ``(False, reason)``."""
    moment = now or datetime.now(timezone.utc)

    if definition.compliance.require_dnc_check and lead.status is LeadStatus.DNC:
        return False, "lead is on do-not-call"
    if definition.compliance.require_call_window and not outbound.is_call_window_open(tenant, moment):
        return False, "outside the outbound call window"
    attempt_cap = min(tenant.max_call_attempts, definition.throttle.max_attempts_per_lead)
    if definition.compliance.require_attempt_limit and lead.attempts >= attempt_cap:
        return False, "attempt limit reached"
    if definition.compliance.require_daily_limit:
        used_today = await _attempts_today(session, tenant, definition)
        if used_today >= definition.throttle.daily_limit:
            return False, "daily limit reached"
    if lead.next_attempt_at is not None and lead.next_attempt_at > moment:
        return False, "lead is in backoff"
    return True, ""


async def _attempts_today(session: AsyncSession, tenant: Tenant, definition: CampaignDefinition) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        campaign_uuid = uuid.UUID(definition.id)
    except (ValueError, TypeError):
        return 0
    filters = [
        Lead.tenant_id == tenant.id,
        Lead.campaign_id == campaign_uuid,
        Lead.last_attempt_at >= start,
    ]
    if definition.environment_id:
        try:
            filters.append(Lead.environment_id == uuid.UUID(definition.environment_id))
        except (ValueError, TypeError):
            return 0
    stmt = select(func.count()).select_from(Lead).where(*filters)
    return (await session.execute(stmt)).scalar() or 0


# ------------------------------------------------------------------- plan ---

async def execution_plan(
    session: AsyncSession,
    tenant: Tenant,
    definition: CampaignDefinition,
    *,
    limit: int = 100,
) -> list[CampaignExecutionIntent]:
    """Produce safe execution intents. Never dials anything.

    Deterministic: the audience resolves to a sorted id list, every lead is
    re-checked against tenant *and* the campaign's environment, and each
    intent carries the campaign's environment id so the outbound layer can
    enforce the same boundary without another lookup.
    """
    limit = min(max(limit, 1), 1_000)
    lead_ids = await resolve_audience(session, tenant, definition.audience)
    intents: list[CampaignExecutionIntent] = []
    for lead_id in lead_ids[:limit]:
        try:
            lead = await session.get(Lead, uuid.UUID(lead_id))
        except ValueError:
            continue
        if lead is None or str(lead.tenant_id) != str(tenant.id):
            continue
        if definition.environment_id and str(lead.environment_id) != definition.environment_id:
            continue
        ok, reason = await eligibility_check(session, tenant, lead, definition)
        key = intent_idempotency_key(definition.id, lead_id)
        intents.append(CampaignExecutionIntent(
            id=key,
            campaign_id=definition.id,
            tenant_id=str(tenant.id),
            lead_id=lead_id,
            channel=definition.channel,
            idempotency_key=key,
            reason_skipped="" if ok else reason,
            environment_id=definition.environment_id or str(lead.environment_id),
        ))
    return intents


async def progress(session: AsyncSession, tenant: Tenant, definition: CampaignDefinition) -> CampaignMetrics:
    """Aggregate-safe counters from real lead rows (tenant + environment scoped)."""
    try:
        campaign_uuid = uuid.UUID(definition.id)
    except ValueError:
        return CampaignMetrics()
    base = [Lead.tenant_id == tenant.id, Lead.campaign_id == campaign_uuid]
    if definition.environment_id:
        try:
            base.append(Lead.environment_id == uuid.UUID(definition.environment_id))
        except (ValueError, TypeError):
            return CampaignMetrics()
    total = (await session.execute(
        select(func.count()).select_from(Lead).where(*base)
    )).scalar() or 0
    attempted = (await session.execute(
        select(func.count()).select_from(Lead).where(*base, Lead.attempts > 0)
    )).scalar() or 0
    dnc = (await session.execute(
        select(func.count()).select_from(Lead).where(*base, Lead.status == LeadStatus.DNC)
    )).scalar() or 0
    qualified = (await session.execute(
        select(func.count()).select_from(Lead).where(*base, Lead.status == LeadStatus.QUALIFIED)
    )).scalar() or 0
    failed = (await session.execute(
        select(func.count()).select_from(Lead).where(*base, Lead.status == LeadStatus.FAILED)
    )).scalar() or 0
    return CampaignMetrics(
        total_leads=total,
        attempted=attempted,
        dnc_skipped=dnc,
        conversions=qualified,
        failed=failed,
    )


async def aggregate_results(session: AsyncSession, tenant: Tenant, definition: CampaignDefinition) -> dict:
    """Progress plus the eligibility split, for the results view."""
    metrics = await progress(session, tenant, definition)
    plan = await execution_plan(session, tenant, definition, limit=1_000)
    split = {
        "eligible": sum(1 for i in plan if not i.skipped),
        "skipped_dnc": sum(1 for i in plan if i.reason_skipped == "lead is on do-not-call"),
        "skipped_window": sum(1 for i in plan if i.reason_skipped == "outside the outbound call window"),
        "skipped_attempt_limit": sum(1 for i in plan if i.reason_skipped == "attempt limit reached"),
        "skipped_daily_limit": sum(1 for i in plan if i.reason_skipped == "daily limit reached"),
    }
    return {"metrics": metrics.__dict__, "eligibility": split}
