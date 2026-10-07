"""Lead lifecycle facade. Callers own the transaction; this module flushes only."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus
from app.integrations.crm import hooks as crm_hooks
from app.leads import (
    activities,
    consent,
    dedup,
    enrichment,
    exporters,
    importers,
    lifecycle,
    scoring,
    segmentation,
    tasks,
)
from app.leads.exceptions import DuplicateLead, LeadError
from app.leads.models import LeadIdentity, LeadSegment
from app.leads.repository import (
    campaign_in_scope,
    get_identity,
    get_lead,
    get_segment,
    require_environment,
    touch,
    user_in_tenant,
)
from app.tenancy.isolation import BoundaryDenied, NotFound, ValidationFailed

_PAGE_MAX = 100


def page(limit: int | None, offset: int | None, *, cap: int = _PAGE_MAX) -> tuple[int, int]:
    size = 50 if limit is None else int(limit)
    if size < 1 or size > cap:
        raise ValidationFailed(f"limit must be between 1 and {cap}")
    start = 0 if offset is None else int(offset)
    if start < 0:
        raise ValidationFailed("offset must be zero or greater")
    return size, start


def _reject_override(tenant_id: uuid.UUID, claimed_tenant_id: uuid.UUID | None) -> None:
    if claimed_tenant_id is not None and claimed_tenant_id != tenant_id:
        raise BoundaryDenied()


def _clean_custom(raw: dict | None) -> dict:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValidationFailed("custom_fields must be an object")
    if "transcript" in raw:
        raise ValidationFailed("Transcripts are not stored on leads")
    return exporters.safe_custom_fields(raw)


async def _campaign(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
    environment_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Validate a campaign binding against tenant *and* environment.

    A campaign can only ever own leads from its own environment: when the
    caller supplies the lead's environment, a campaign bound elsewhere is
    refused with the same not-found a foreign-tenant campaign gets, so no
    boundary is revealed. Leads are never silently moved between
    environments to make a binding fit.
    """
    if campaign_id is None:
        return None
    row = await campaign_in_scope(session, tenant_id, environment_id, campaign_id)
    if row is None:
        raise NotFound()
    return row.id


async def ensure_identity(session: AsyncSession, lead: Lead, *, source: str = "backfill") -> LeadIdentity:
    existing = await get_identity(session, lead.tenant_id, lead.id)
    if existing is not None:
        return existing
    if lead.environment_id is None:
        raise ValidationFailed("Lead has no environment")
    row = LeadIdentity(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        phone_normalized=dedup.normalize_phone(lead.phone),
        email_normalized=dedup.normalize_email(lead.email),
        source=source[:64],
    )
    session.add(row)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        await session.refresh(lead)
        found = await get_identity(session, lead.tenant_id, lead.id)
        if found is not None:
            return found
        raise
    return row


async def create_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone: str,
    name: str = "",
    email: str | None = None,
    company: str | None = None,
    notes: str | None = None,
    campaign_id: uuid.UUID | None = None,
    custom_fields: dict | None = None,
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    source: str = "api",
    on_duplicate: str = "error",
) -> tuple[Lead, bool]:
    _reject_override(tenant_id, claimed_tenant_id)
    environment = await require_environment(session, tenant_id, environment_id)
    phone_key = dedup.normalize_phone(phone)
    if phone_key is None:
        raise ValidationFailed("Phone must be in international format starting with +")
    email_key = None
    if email:
        email_key = dedup.normalize_email(email)
        if email_key is None:
            raise ValidationFailed("Email is not valid")
    owned_campaign = await _campaign(session, tenant_id, campaign_id, environment)
    matches = await dedup.exact_matches(
        session, tenant_id, environment, phone=phone_key, email=email_key
    )
    if matches:
        if on_duplicate == "skip":
            existing = await get_lead(session, tenant_id, matches[0].lead_id, environment)
            return existing, False
        raise DuplicateLead()
    lead = Lead(
        tenant_id=tenant_id,
        environment_id=environment,
        name=(name or "")[:255],
        phone=phone_key,
        email=email_key,
        company=(company or None),
        notes=(notes or "")[:2000] or None,
        campaign_id=owned_campaign,
        custom_fields=_clean_custom(custom_fields),
        status=LeadStatus.NEW,
    )
    if lead.company:
        lead.company = lead.company[:255]
    try:
        async with session.begin_nested():
            session.add(lead)
            await session.flush()
            session.add(
                LeadIdentity(
                    lead_id=lead.id,
                    tenant_id=tenant_id,
                    environment_id=lead.environment_id,
                    phone_normalized=phone_key,
                    email_normalized=email_key,
                    source=source[:64],
                )
            )
            await session.flush()
    except IntegrityError as exc:
        if on_duplicate == "skip":
            again = await dedup.exact_matches(
                session, tenant_id, environment, phone=phone_key, email=email_key
            )
            if again:
                existing = await get_lead(session, tenant_id, again[0].lead_id, environment)
                return existing, False
        raise DuplicateLead() from exc
    await lifecycle.record_created(session, lead, source=source, actor_id=actor_id)
    await crm_hooks.on_lead_created(session, lead)
    return lead, True


async def update_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    fields: dict,
) -> Lead:
    _reject_override(tenant_id, claimed_tenant_id)
    if "tenant_id" in fields and fields["tenant_id"] not in (None, tenant_id, str(tenant_id)):
        raise BoundaryDenied()
    if "status" in fields:
        raise ValidationFailed("Status changes go through an explicit transition")
    if "environment_id" in fields:
        # A lead's environment is immutable after creation. The ORM-level
        # freeze hook is the backstop; refusing here returns the honest
        # error instead of a flush-time one. No transfer operation exists.
        raise ValidationFailed("A lead's environment_id is immutable")
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    identity = await ensure_identity(session, lead)
    changed: list[str] = []
    if "name" in fields and fields["name"] is not None:
        lead.name = str(fields["name"])[:255]
        changed.append("name")
    if "company" in fields:
        company = fields["company"]
        lead.company = None if not company else str(company)[:255]
        changed.append("company")
    if "notes" in fields:
        notes = fields["notes"] or ""
        if "transcript" in str(notes).lower() and len(str(notes)) > 2000:
            raise ValidationFailed("A transcript cannot be stored on a lead")
        lead.notes = str(notes)[:2000] or None
        changed.append("notes")
    if "email" in fields:
        email = fields["email"]
        email_key = dedup.normalize_email(email) if email else None
        if email and email_key is None:
            raise ValidationFailed("Email is not valid")
        if email_key != identity.email_normalized:
            clash = await dedup.exact_matches(
                session, tenant_id, lead.environment_id, phone=None, email=email_key,
                exclude_lead_id=lead.id,
            ) if email_key else []
            if clash:
                raise DuplicateLead()
            lead.email = email_key
            identity.email_normalized = email_key
            changed.append("email")
    if "phone" in fields and fields["phone"]:
        phone_key = dedup.normalize_phone(str(fields["phone"]))
        if phone_key is None:
            raise ValidationFailed("Phone must be in international format starting with +")
        if phone_key != identity.phone_normalized:
            clash = await dedup.exact_matches(
                session, tenant_id, lead.environment_id, phone=phone_key, email=None,
                exclude_lead_id=lead.id,
            )
            if clash:
                raise DuplicateLead()
            lead.phone = phone_key
            identity.phone_normalized = phone_key
            changed.append("phone")
    if "campaign_id" in fields:
        raw = fields["campaign_id"]
        lead.campaign_id = await _campaign(
            session, tenant_id, uuid.UUID(str(raw)) if raw else None, lead.environment_id
        )
        changed.append("campaign_id")
    if "custom_fields" in fields:
        lead.custom_fields = _clean_custom(fields["custom_fields"])
        changed.append("custom_fields")
    if changed:
        touch(identity)
        await activities.record(
            session, lead, kind="update", summary="Updated " + ",".join(changed), actor_id=actor_id
        )
        await crm_hooks.on_lead_updated(session, lead, reason="fields:" + ",".join(changed))
    await session.flush()
    return lead


async def change_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    target: str,
    reason: str,
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    expected: str | None = None,
) -> Lead:
    _reject_override(tenant_id, claimed_tenant_id)
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    await ensure_identity(session, lead)
    believed = None
    if expected is not None:
        believed, _alias = lifecycle.resolve_status(expected)
    elif lead.status is not None:
        believed = lead.status
    return await lifecycle.transition(
        session,
        lead,
        target,
        reason=reason or "operator",
        source="api",
        actor_id=actor_id,
        expected=believed,
    )


async def assign_owner(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    user_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> Lead:
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    await user_in_tenant(session, tenant_id, user_id)
    identity = await ensure_identity(session, lead)
    identity.owner_user_id = user_id
    touch(identity)
    await activities.record(
        session, lead, kind="assignment", summary=f"Owner set to {user_id}", actor_id=actor_id
    )
    await session.flush()
    return lead


async def search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    status: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    query: str | None = None,
    campaign_id: uuid.UUID | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
    include_merged: bool = False,
) -> list[Lead]:
    environment = await require_environment(session, tenant_id, environment_id)
    size, start = page(limit, offset)
    stmt = select(Lead).where(
        Lead.tenant_id == tenant_id,
        Lead.environment_id == environment,
    )
    if not include_merged:
        merged = select(LeadIdentity.lead_id).where(
            LeadIdentity.tenant_id == tenant_id,
            LeadIdentity.environment_id == environment,
            LeadIdentity.merged_into_id.is_not(None),
        )
        stmt = stmt.where(Lead.id.not_in(merged))
    if status:
        stored, _alias = lifecycle.resolve_status(status)
        stmt = stmt.where(Lead.status == stored)
    if phone:
        phone_key = dedup.normalize_phone(phone) or phone
        stmt = stmt.where(Lead.phone == phone_key)
    if email:
        stmt = stmt.where(Lead.email == (dedup.normalize_email(email) or email))
    if query:
        stmt = stmt.where(Lead.name.contains(query[:100]))
    if campaign_id is not None:
        stmt = stmt.where(Lead.campaign_id == campaign_id)
    rows = (
        await session.execute(stmt.order_by(Lead.created_at.desc()).limit(size).offset(start))
    ).scalars().all()
    return list(rows)


async def duplicates(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> dict:
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    exact = await dedup.exact_matches(
        session,
        tenant_id,
        lead.environment_id,
        phone=lead.phone,
        email=lead.email,
        exclude_lead_id=lead.id,
    )
    return {
        "exact": [
            {"lead_id": str(row.lead_id), "mergeable": True, "match": "phone_or_email"}
            for row in exact
        ],
        "informational": await dedup.informational_name_matches(session, lead),
    }


async def merge_leads(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    survivor_id: uuid.UUID,
    duplicate_id: uuid.UUID,
    reason: str,
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> dict:
    _reject_override(tenant_id, claimed_tenant_id)
    environment = await require_environment(session, tenant_id, environment_id)
    await ensure_identity(session, await get_lead(session, tenant_id, survivor_id, environment))
    row = await dedup.merge(
        session,
        tenant_id=tenant_id,
        environment_id=environment,
        survivor_id=survivor_id,
        duplicate_id=duplicate_id,
        reason=reason,
        actor_id=actor_id,
    )
    return {
        "merge_id": str(row.id),
        "survivor_lead_id": str(row.survivor_lead_id),
        "duplicate_lead_id": str(row.duplicate_lead_id),
    }


async def score_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> dict:
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    status_before = lead.status
    row = await scoring.snapshot(session, lead)
    if lead.status is not status_before:
        raise LeadError("Scoring must not change lead status", code="score_mutated_status")
    return {"version": row.version, "score": row.score, "factors": row.factors, "status": lead.status.value}


async def import_body(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    body: bytes,
    content_type: str,
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> dict:
    _reject_override(tenant_id, claimed_tenant_id)
    payload = importers.bounded_body(body)
    environment = await require_environment(session, tenant_id, environment_id)
    key = importers.idempotency_key(tenant_id, idempotency_key, payload)
    job, created = await importers.begin_import_job(
        session,
        tenant_id=tenant_id,
        environment_id=environment,
        organization_id=organization_id,
        key=key,
    )
    if not created:
        stored = (job.payload or {}).get("result")
        if isinstance(stored, dict):
            replayed = dict(stored)
            replayed["replayed"] = True
            return replayed
        return {"created": 0, "skipped": 0, "errors": [], "replayed": True}
    rows = importers.parse_rows(payload, content_type)
    created_count = 0
    skipped = 0
    errors: list[dict] = []
    for index, row in enumerate(rows):
        try:
            _lead, was_new = await create_lead(
                session,
                tenant_id=tenant_id,
                environment_id=environment,
                phone=str(row.get("phone") or ""),
                name=str(row.get("name") or ""),
                email=row.get("email") or None,
                company=row.get("company") or None,
                notes=row.get("notes") or None,
                source="import",
                actor_id=actor_id,
                on_duplicate="skip",
            )
        except LeadError as exc:
            errors.append({"row": index, "code": exc.code})
            continue
        except ValidationFailed as exc:
            errors.append({"row": index, "code": exc.code})
            continue
        if was_new:
            created_count += 1
        else:
            skipped += 1
    result = {
        "created": created_count,
        "skipped": skipped,
        "errors": errors[:50],
        "replayed": False,
        "job_id": str(job.id),
    }
    importers.finish_job(job, result)
    await session.flush()
    return result


async def export_leads(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> str:
    cap = exporters.MAX_EXPORT_ROWS if limit is None else min(int(limit), exporters.MAX_EXPORT_ROWS)
    if cap < 1:
        raise ValidationFailed("limit must be at least 1")
    rows = await search(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        limit=min(cap, _PAGE_MAX),
        offset=0,
    )
    # search caps at 100. Page through so a bounded export can exceed one page
    # without loading an unbounded table.
    if cap > _PAGE_MAX:
        collected = list(rows)
        start = _PAGE_MAX
        while len(collected) < cap:
            nxt = await search(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,
                limit=_PAGE_MAX,
                offset=start,
            )
            if not nxt:
                break
            collected.extend(nxt)
            start += _PAGE_MAX
        rows = collected[:cap]
    return exporters.render_csv(rows)


async def add_note(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    summary: str,
    environment_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    call_id: uuid.UUID | None = None,
    appointment_id: uuid.UUID | None = None,
) -> dict:
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    if call_id is not None:
        from app.db.models import Call

        call = await session.get(Call, call_id)
        if call is None or call.tenant_id != tenant_id or call.environment_id != lead.environment_id:
            raise NotFound()
    if appointment_id is not None:
        from app.db.models import Appointment

        appointment = await session.get(Appointment, appointment_id)
        if (
            appointment is None
            or appointment.tenant_id != tenant_id
            or appointment.environment_id != lead.environment_id
        ):
            raise NotFound()
    row = await activities.record(
        session,
        lead,
        kind="note" if call_id is None and appointment_id is None else "reference",
        summary=summary,
        actor_id=actor_id,
        call_id=call_id,
        appointment_id=appointment_id,
    )
    return {"id": str(row.id), "summary": row.summary, "call_id": str(call_id) if call_id else None}


async def lead_timeline(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
) -> list[dict]:
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    size, start = page(limit, offset)
    return await activities.timeline(session, lead, limit=size, offset=start)


async def open_task(session: AsyncSession, **kwargs):
    tenant_id = kwargs["tenant_id"]
    environment = await require_environment(session, tenant_id, kwargs.get("environment_id"))
    lead = await get_lead(session, tenant_id, kwargs["lead_id"], environment)
    return await tasks.create_task(
        session,
        lead,
        title=kwargs["title"],
        assignee_id=kwargs.get("assignee_id"),
        due_at=kwargs.get("due_at"),
    )


async def update_task(session: AsyncSession, **kwargs):
    environment = await require_environment(
        session, kwargs["tenant_id"], kwargs.get("environment_id")
    )
    return await tasks.change_task(
        session,
        tenant_id=kwargs["tenant_id"],
        environment_id=environment,
        task_id=kwargs["task_id"],
        status=kwargs.get("status"),
        assignee_id=kwargs.get("assignee_id"),
    )


async def record_consent(session: AsyncSession, **kwargs):
    _reject_override(kwargs["tenant_id"], kwargs.get("claimed_tenant_id"))
    environment = await require_environment(
        session, kwargs["tenant_id"], kwargs.get("environment_id")
    )
    lead = await get_lead(session, kwargs["tenant_id"], kwargs["lead_id"], environment)
    return await consent.record_consent(
        session,
        lead,
        channel=kwargs["channel"],
        decision=kwargs["decision"],
        source=kwargs.get("source") or "api",
        actor_id=kwargs.get("actor_id"),
    )


async def enrich(session: AsyncSession, *, tenant_id: uuid.UUID, lead_id: uuid.UUID, environment_id=None):
    environment = await require_environment(session, tenant_id, environment_id)
    lead = await get_lead(session, tenant_id, lead_id, environment)
    return await enrichment.request_enrichment(session, lead)


async def create_segment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    definition: dict,
    environment_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
) -> LeadSegment:
    _reject_override(tenant_id, claimed_tenant_id)
    environment = await require_environment(session, tenant_id, environment_id)
    cleaned = segmentation.validate_definition(definition)
    label = name.strip()
    if not label or len(label) > 80:
        raise ValidationFailed("Segment name is required")
    row = LeadSegment(
        tenant_id=tenant_id,
        environment_id=environment,
        name=label,
        definition=cleaned,
        created_by=actor_id,
    )
    session.add(row)
    await session.flush()
    return row


async def segment_members(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    segment_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
) -> list[Lead]:
    environment = await require_environment(session, tenant_id, environment_id)
    row = await get_segment(session, tenant_id, environment, segment_id)
    size, start = page(limit, offset)
    return await segmentation.members(session, row, limit=size, offset=start)


def lead_dict(lead: Lead, identity: LeadIdentity | None = None) -> dict:
    status = lead.status.value if hasattr(lead.status, "value") else str(lead.status)
    body = {
        "id": str(lead.id),
        "tenant_id": str(lead.tenant_id),
        "environment_id": str(lead.environment_id) if lead.environment_id else None,
        "name": lead.name,
        "phone": lead.phone,
        "email": lead.email,
        "company": lead.company,
        "status": status,
        "score": lead.score,
        "attempts": lead.attempts,
        "campaign_id": str(lead.campaign_id) if lead.campaign_id else None,
        "custom_fields": exporters.safe_custom_fields(lead.custom_fields),
    }
    if identity is not None:
        body["owner_user_id"] = str(identity.owner_user_id) if identity.owner_user_id else None
        body["merged_into_id"] = str(identity.merged_into_id) if identity.merged_into_id else None
    return body
