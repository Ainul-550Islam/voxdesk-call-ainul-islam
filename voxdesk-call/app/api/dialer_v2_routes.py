"""Predictive, progressive, and preview dialing APIs."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center_v2.dialer import (
    add_jurisdiction_override,
    campaign_precheck,
    upsert_policy,
)
from app.contact_center_v2.dialer.campaign_state import transition_campaign_state
from app.contact_center_v2.dialer.preview_dialer import (
    dial_claimed_contact as preview_dial,
    next_contact as preview_next,
    skip_claimed_contact as preview_skip,
)
from app.contact_center_v2.http import audit, endpoint_errors
from app.contact_center_v2.policy import (
    abandonment_measurement,
    policy_for,
    prepare_contacts,
    preparation_summary,
)
from app.contact_center_v2.schemas import (
    CampaignAction,
    DialerPolicyBody,
    DialerPolicyPatch,
    JurisdictionOverrideBody,
    PreviewSkipBody,
)
from app.db.contact_center_v2_models import DialerAttempt, DialerContact, DialerJurisdictionOverride, PacingSnapshot
from app.db.session import get_session
from app.resilience.idempotency import claim_request, complete_request

router = APIRouter(prefix="/api/cc2/dialer", tags=["contact-center-v2-dialer"])


@router.post("/policies", status_code=201)
@endpoint_errors
async def create_policy(
    payload: DialerPolicyBody,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await upsert_policy(session, tenant_id=ctx.tenant_id, payload=payload.model_dump())
    await audit(
        session, ctx, event_type="cc2.dialer.policy_created", resource_type="dialer_policy",
        resource_id=row.id, environment_id=row.environment_id, detail={"mode": row.mode},
    )
    await session.commit()
    return {"policy": row.as_dict()}


@router.get("/policies/{policy_id}")
@endpoint_errors
async def get_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await policy_for(session, ctx.tenant_id, policy_id)
    await audit(session, ctx, event_type="cc2.dialer.policy_read", resource_type="dialer_policy", resource_id=row.id)
    await session.commit()
    return {"policy": row.as_dict()}


@router.patch("/policies/{policy_id}")
@endpoint_errors
async def patch_policy(
    policy_id: uuid.UUID,
    payload: DialerPolicyPatch,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await upsert_policy(
        session, tenant_id=ctx.tenant_id, policy_id=policy_id,
        payload=payload.model_dump(exclude_none=True),
    )
    await audit(session, ctx, event_type="cc2.dialer.policy_updated", resource_type="dialer_policy", resource_id=row.id)
    await session.commit()
    return {"policy": row.as_dict()}


@router.post("/policies/{policy_id}/prepare")
@endpoint_errors
async def prepare_policy(
    policy_id: uuid.UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=256),
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await policy_for(session, ctx.tenant_id, policy_id, for_update=True)
    if row.status == "running":
        from app.contact_center_v2.errors import Conflict
        raise Conflict("Pause the dialer before refreshing its compliance evidence")
    claim = None
    if idempotency_key:
        claim = await claim_request(
            session, tenant_id=ctx.tenant_id, environment_id=row.environment_id,
            operation="cc2.dialer.prepare", key=idempotency_key,
            request_data={"policy_id": str(row.id)},
        )
    if claim is not None and claim.replayed:
        result = await preparation_summary(session, row)
    else:
        result = await prepare_contacts(session, row)
        if claim is not None:
            await complete_request(
                session, claim.receipt, resource_type="dialer_policy", resource_id=row.id
            )
    replayed = bool(claim and claim.replayed)
    await audit(session, ctx, event_type="cc2.dialer.evidence_prepared", resource_type="dialer_policy", resource_id=row.id, detail={**result, "replayed": replayed})
    await session.commit()
    return {"policy": row.as_dict(), "preparation": result, "replayed": replayed}


@router.post("/policies/{policy_id}/start")
@endpoint_errors
async def start_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    result = await transition_campaign_state(session, tenant_id=ctx.tenant_id, environment_id=ctx.environment_id, policy_id=policy_id, actor_user_id=ctx.user_id, action="start")
    row = result.policy
    await audit(session, ctx, event_type="cc2.dialer.started", resource_type="dialer_policy", resource_id=row.id, detail={"job_id": result.job_id, "mode": row.mode, "replayed": result.replayed})
    await session.commit()
    return {"policy": row.as_dict(), "job_id": result.job_id, "replayed": result.replayed}


@router.post("/policies/{policy_id}/pause")
@endpoint_errors
async def pause_policy(
    policy_id: uuid.UUID,
    payload: CampaignAction,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    result = await transition_campaign_state(session, tenant_id=ctx.tenant_id, environment_id=ctx.environment_id, policy_id=policy_id, actor_user_id=ctx.user_id, action="pause")
    row = result.policy
    await audit(session, ctx, event_type="cc2.dialer.paused", resource_type="dialer_policy", resource_id=row.id, detail={"reason": payload.reason, "replayed": result.replayed})
    await session.commit()
    return {"policy": row.as_dict(), "replayed": result.replayed}


@router.post("/policies/{policy_id}/stop")
@endpoint_errors
async def stop_policy(
    policy_id: uuid.UUID,
    payload: CampaignAction,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    result = await transition_campaign_state(session, tenant_id=ctx.tenant_id, environment_id=ctx.environment_id, policy_id=policy_id, actor_user_id=ctx.user_id, action="stop")
    row = result.policy
    await audit(session, ctx, event_type="cc2.dialer.stopped", resource_type="dialer_policy", resource_id=row.id, detail={"reason": payload.reason, "replayed": result.replayed})
    await session.commit()
    return {"policy": row.as_dict(), "replayed": result.replayed}


@router.get("/policies/{policy_id}/precheck")
@endpoint_errors
async def precheck_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await policy_for(session, ctx.tenant_id, policy_id)
    result = await campaign_precheck(session, row, refresh=False)
    await audit(session, ctx, event_type="cc2.dialer.precheck", resource_type="dialer_policy", resource_id=row.id, detail={"allowed": result.allowed, "reasons": [item.code for item in result.reasons], "refresh": False})
    await session.commit()
    return result.as_dict()


@router.post("/policies/{policy_id}/precheck")
@endpoint_errors
async def refresh_precheck_policy(
    policy_id: uuid.UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=256),
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await policy_for(session, ctx.tenant_id, policy_id, for_update=True)
    if row.status == "running":
        from app.contact_center_v2.errors import Conflict
        raise Conflict("Pause the dialer before refreshing its compliance evidence")
    claim = None
    if idempotency_key:
        claim = await claim_request(
            session, tenant_id=ctx.tenant_id, environment_id=row.environment_id,
            operation="cc2.dialer.precheck_refresh", key=idempotency_key,
            request_data={"policy_id": str(row.id)},
        )
    refresh = not bool(claim and claim.replayed)
    result = await campaign_precheck(session, row, refresh=refresh)
    if claim is not None and not claim.replayed:
        await complete_request(
            session, claim.receipt, resource_type="dialer_policy", resource_id=row.id
        )
    replayed = bool(claim and claim.replayed)
    await audit(session, ctx, event_type="cc2.dialer.precheck", resource_type="dialer_policy", resource_id=row.id, detail={"allowed": result.allowed, "reasons": [item.code for item in result.reasons], "refresh": refresh, "replayed": replayed})
    await session.commit()
    return {**result.as_dict(), "replayed": replayed}


@router.get("/policies/{policy_id}/pacing")
@endpoint_errors
async def get_pacing(
    policy_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_READ)),
    session: AsyncSession = Depends(get_session),
):
    await policy_for(session, ctx.tenant_id, policy_id)
    rows = (
        await session.execute(
            select(PacingSnapshot).where(
                PacingSnapshot.tenant_id == ctx.tenant_id,
                PacingSnapshot.policy_id == policy_id,
            ).order_by(PacingSnapshot.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    await audit(session, ctx, event_type="cc2.dialer.pacing_read", resource_type="dialer_policy", resource_id=policy_id, detail={"count": len(rows)})
    await session.commit()
    return {"snapshots": [{
        "id": str(row.id), "mode": row.mode, "answer_rate": str(row.answer_rate),
        "occupancy": str(row.occupancy), "dial_ratio": str(row.dial_ratio),
        "projected_abandon_rate": str(row.projected_abandon_rate),
        "available_agents": row.available_agents,
        "soon_available_agents": str(row.soon_available_agents),
        "sample_size": row.sample_size, "throttled_to_progressive": row.throttled_to_progressive,
        "reason": row.reason, "created_at": row.created_at.isoformat(),
    } for row in rows]}


@router.get("/policies/{policy_id}/abandonment")
@endpoint_errors
async def get_abandonment(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await policy_for(session, ctx.tenant_id, policy_id)
    measurement = await abandonment_measurement(session, row)
    await audit(session, ctx, event_type="cc2.dialer.abandonment_read", resource_type="dialer_policy", resource_id=row.id)
    await session.commit()
    return {"measurement": measurement.as_dict()}


@router.post("/policies/{policy_id}/overrides", status_code=201)
@endpoint_errors
async def create_override(
    policy_id: uuid.UUID,
    payload: JurisdictionOverrideBody,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=256),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    policy = await policy_for(session, ctx.tenant_id, policy_id)
    claim = None
    if idempotency_key:
        claim = await claim_request(
            session, tenant_id=ctx.tenant_id, environment_id=policy.environment_id,
            operation="cc2.dialer.override", key=idempotency_key,
            request_data={"policy_id": str(policy_id), "payload": payload.model_dump(mode="json")},
        )
    if claim is not None and claim.replayed:
        row = await session.get(DialerJurisdictionOverride, uuid.UUID(claim.receipt.resource_id))
        if row is None or row.tenant_id != ctx.tenant_id or row.policy_id != policy_id:
            from app.contact_center_v2.errors import NotFound
            raise NotFound("dialer override")
    else:
        row = await add_jurisdiction_override(session, tenant_id=ctx.tenant_id, policy_id=policy_id, actor_id=ctx.user_id, **payload.model_dump())
        if claim is not None:
            await complete_request(session, claim.receipt, resource_type="dialer_override", resource_id=row.id)
    replayed = bool(claim and claim.replayed)
    await audit(session, ctx, event_type="cc2.dialer.jurisdiction_override", resource_type="dialer_override", resource_id=row.id, detail={"policy_id": str(policy_id), "jurisdiction": row.jurisdiction, "abandonment_cap": str(row.abandonment_cap), "legal_reference": row.legal_reference, "reason": row.reason, "expires_at": row.expires_at.isoformat(), "replayed": replayed})
    await session.commit()
    return {"override": {"id": str(row.id), "policy_id": str(row.policy_id), "jurisdiction": row.jurisdiction, "abandonment_cap": str(row.abandonment_cap), "legal_reference": row.legal_reference, "reason": row.reason, "approved_by": str(row.approved_by), "expires_at": row.expires_at.isoformat(), "created_at": row.created_at.isoformat()}, "replayed": replayed}


@router.get("/policies/{policy_id}/contacts")
@endpoint_errors
async def get_contacts(
    policy_id: uuid.UUID,
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_READ)),
    session: AsyncSession = Depends(get_session),
):
    await policy_for(session, ctx.tenant_id, policy_id)
    stmt = select(DialerContact).where(DialerContact.tenant_id == ctx.tenant_id, DialerContact.policy_id == policy_id)
    if state:
        stmt = stmt.where(DialerContact.state == state)
    rows = (await session.execute(stmt.order_by(DialerContact.created_at, DialerContact.id).limit(limit))).scalars().all()
    await audit(session, ctx, event_type="cc2.dialer.contacts_read", resource_type="dialer_policy", resource_id=policy_id, detail={"count": len(rows)})
    await session.commit()
    return {"contacts": [row.as_dict() for row in rows]}


@router.get("/policies/{policy_id}/attempts")
@endpoint_errors
async def get_attempts(
    policy_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_READ)),
    session: AsyncSession = Depends(get_session),
):
    await policy_for(session, ctx.tenant_id, policy_id)
    rows = (await session.execute(select(DialerAttempt).where(DialerAttempt.tenant_id == ctx.tenant_id, DialerAttempt.policy_id == policy_id).order_by(DialerAttempt.created_at.desc()).limit(limit))).scalars().all()
    await audit(session, ctx, event_type="cc2.dialer.attempts_read", resource_type="dialer_policy", resource_id=policy_id, detail={"count": len(rows)})
    await session.commit()
    return {"attempts": [row.as_dict() for row in rows]}


@router.post("/preview/{policy_id}/next")
@endpoint_errors
async def next_preview(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_OPERATE)),
    session: AsyncSession = Depends(get_session),
):
    contact, lead = await preview_next(session, tenant_id=ctx.tenant_id, policy_id=policy_id, agent_id=ctx.user_id)
    await audit(session, ctx, event_type="cc2.dialer.preview_leased", resource_type="dialer_contact", resource_id=contact.id)
    await session.commit()
    return {"contact": contact.as_dict(), "lead": {"id": str(lead.id), "name": lead.name, "phone": lead.phone, "custom_fields": dict(lead.custom_fields or {})}}


@router.post("/preview/contacts/{contact_id}/dial")
@endpoint_errors
async def dial_preview(
    contact_id: uuid.UUID,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=256),
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_OPERATE)),
    session: AsyncSession = Depends(get_session),
):
    attempt = await preview_dial(session, tenant_id=ctx.tenant_id, contact_id=contact_id, agent_id=ctx.user_id, idempotency_key=idempotency_key)
    if attempt is None:
        from app.contact_center_v2.errors import Conflict
        raise Conflict("The preview contact was claimed by another agent")
    await audit(session, ctx, event_type="cc2.dialer.preview_dialed", resource_type="dialer_attempt", resource_id=attempt.id, detail={"outcome": attempt.outcome})
    await session.commit()
    return {"attempt": attempt.as_dict()}


@router.post("/preview/contacts/{contact_id}/skip")
@endpoint_errors
async def skip_preview(
    contact_id: uuid.UUID,
    payload: PreviewSkipBody,
    ctx: TenantContext = Depends(require_permission(Permission.DIALER_OPERATE)),
    session: AsyncSession = Depends(get_session),
):
    contact = await preview_skip(session, tenant_id=ctx.tenant_id, contact_id=contact_id, agent_id=ctx.user_id)
    contact.block_reason = payload.reason[:64]
    await audit(session, ctx, event_type="cc2.dialer.preview_skipped", resource_type="dialer_contact", resource_id=contact.id, detail={"reason": payload.reason})
    await session.commit()
    return {"contact": contact.as_dict()}


__all__ = ["router"]
