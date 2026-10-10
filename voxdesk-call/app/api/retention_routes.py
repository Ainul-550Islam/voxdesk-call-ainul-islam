"""Retention & Privacy Controls API.

Manages per-tenant, per-agent, per-resource ``RetentionPolicy`` and
``RecordingPolicy`` configurations, legal holds, and real retention purge
execution via ``app.core.retention.purge_expired_calls``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.core.retention import purge_expired_calls
from app.db.enterprise_models import RetentionPolicy
from app.db.models import AuditAction, Tenant
from app.db.session import get_session
from app.telephony.recording_policy import RecordingPolicy, effective as effective_recording_policy, save as save_recording_policy
from app.tenancy.isolation import Forbidden, HierarchyError, to_http

router = APIRouter(prefix="/api/retention", tags=["retention"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetentionPolicyCreate(_Strict):
    agent_id: str = Field(default="", max_length=80, description="Empty for tenant-wide")
    resource_type: str = Field(
        default="call", pattern="^(call|chat|recording|transcript|pcap|all)$"
    )
    retention_days: int = Field(default=90, ge=1, le=3650)
    purge_after_days: int = Field(default=365, ge=1, le=3650)
    legal_hold: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class RetentionPolicyUpdate(_Strict):
    retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    purge_after_days: Optional[int] = Field(default=None, ge=1, le=3650)
    legal_hold: Optional[bool] = None
    meta: Optional[dict[str, Any]] = None


class RecordingPolicyConfigRequest(_Strict):
    agent_id: Optional[str] = Field(default=None, max_length=80)
    environment_id: Optional[uuid.UUID] = None
    enabled: bool = True
    consent_mode: str = Field(
        default="one_party", pattern="^(none|one_party|two_party|explicit)$"
    )
    disclosure_text: Optional[str] = Field(default=None, max_length=500)
    retention_days: int = Field(default=90, ge=1, le=3650)
    legal_hold: bool = False
    raw_access_roles: list[str] = Field(default_factory=lambda: ["owner", "admin"])
    redact_pii: bool = True


class PurgeTriggerRequest(_Strict):
    policy_id: Optional[uuid.UUID] = None
    resource_type: Optional[str] = Field(
        default=None, pattern="^(call|chat|recording|transcript|pcap|all)$"
    )
    dry_run: bool = False


class RetentionPolicyOut(_Strict):
    id: str
    tenant_id: str
    agent_id: str
    resource_type: str
    retention_days: int
    purge_after_days: int
    legal_hold: bool
    last_purge_at: Optional[str] = None
    next_purge_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    meta: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(row: RetentionPolicy) -> RetentionPolicyOut:
    d = row.as_dict()
    return RetentionPolicyOut(
        id=d["id"],
        tenant_id=d["tenant_id"],
        agent_id=d["agent_id"],
        resource_type=d["resource_type"],
        retention_days=d["retention_days"],
        purge_after_days=d["purge_after_days"],
        legal_hold=d["legal_hold"],
        last_purge_at=d["last_purge_at"],
        next_purge_at=d["next_purge_at"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        meta=d["meta"],
    )


async def _get(
    session: AsyncSession, policy_id: uuid.UUID, tenant_id: uuid.UUID
) -> RetentionPolicy:
    row = await session.get(RetentionPolicy, policy_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="retention policy not found")
    return row


@router.post("/policies", response_model=RetentionPolicyOut, status_code=201)
async def create_or_update_retention_policy(
    payload: RetentionPolicyCreate,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/retention/policies — Create or update retention policy per agent/resource."""
    try:
        if payload.meta.get("redact_pii") is False:
            role = getattr(ctx.user, "role", None)
            if role is None or not has_permission(role, Permission.SECURITY_WRITE):
                raise HTTPException(
                    status_code=403,
                    detail="disabling PII redaction requires security:settings permission",
                )

        existing = (
            await session.execute(
                select(RetentionPolicy).where(
                    RetentionPolicy.tenant_id == ctx.tenant_id,
                    RetentionPolicy.agent_id == payload.agent_id,
                    RetentionPolicy.resource_type == payload.resource_type,
                )
            )
        ).scalar_one_or_none()
        now = _now()
        if existing:
            existing.retention_days = payload.retention_days
            existing.purge_after_days = payload.purge_after_days
            existing.legal_hold = payload.legal_hold
            existing.meta = payload.meta
            existing.updated_at = now
            existing.next_purge_at = now + timedelta(days=1)
            row = existing
        else:
            row = RetentionPolicy(
                tenant_id=ctx.tenant_id,
                agent_id=payload.agent_id,
                resource_type=payload.resource_type,
                retention_days=payload.retention_days,
                purge_after_days=payload.purge_after_days,
                legal_hold=payload.legal_hold,
                next_purge_at=now + timedelta(days=1),
                meta=payload.meta,
            )
            session.add(row)
        await session.flush()

        if payload.meta.get("redact_pii") is False:
            await emit(
                session,
                AuditAction.PII_REDACTION_DISABLED,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                detail={
                    "operation": "pii_redaction_disabled",
                    "policy_id": str(row.id),
                    "agent_id": payload.agent_id,
                    "resource_type": payload.resource_type,
                },
                commit=False,
            )
        else:
            await emit(
                session,
                AuditAction.SECURITY_SETTINGS_CHANGED,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                detail={
                    "operation": "retention_policy_upserted",
                    "policy_id": str(row.id),
                    "agent_id": payload.agent_id,
                    "resource_type": payload.resource_type,
                    "retention_days": payload.retention_days,
                    "legal_hold": payload.legal_hold,
                },
                commit=False,
            )

        await session.commit()
        await session.refresh(row)
        return _out(row)
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/policies", response_model=dict)
async def list_retention_policies(
    agent_id: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [RetentionPolicy.tenant_id == ctx.tenant_id]
    if agent_id is not None:
        scope.append(RetentionPolicy.agent_id == agent_id)
    if resource_type:
        scope.append(RetentionPolicy.resource_type == resource_type)
    total = (
        await session.execute(select(func.count(RetentionPolicy.id)).where(*scope))
    ).scalar() or 0
    rows = (
        await session.execute(
            select(RetentionPolicy)
            .where(*scope)
            .order_by(RetentionPolicy.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return {
        "policies": [r.as_dict() for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/policies/{policy_id}", response_model=RetentionPolicyOut)
async def get_retention_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get(session, policy_id, ctx.tenant_id)
    return _out(row)


@router.patch("/policies/{policy_id}", response_model=RetentionPolicyOut)
async def update_retention_policy(
    policy_id: uuid.UUID,
    payload: RetentionPolicyUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get(session, policy_id, ctx.tenant_id)
    if payload.meta is not None and payload.meta.get("redact_pii") is False:
        role = getattr(ctx.user, "role", None)
        if role is None or not has_permission(role, Permission.SECURITY_WRITE):
            raise HTTPException(
                status_code=403,
                detail="disabling PII redaction requires security:settings permission",
            )

    if payload.retention_days is not None:
        row.retention_days = payload.retention_days
    if payload.purge_after_days is not None:
        row.purge_after_days = payload.purge_after_days
    if payload.legal_hold is not None:
        row.legal_hold = payload.legal_hold
    if payload.meta is not None:
        row.meta = payload.meta
    row.updated_at = _now()

    if payload.meta is not None and payload.meta.get("redact_pii") is False:
        await emit(
            session,
            AuditAction.PII_REDACTION_DISABLED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            detail={
                "operation": "pii_redaction_disabled",
                "policy_id": str(row.id),
                "agent_id": row.agent_id,
                "resource_type": row.resource_type,
            },
            commit=False,
        )
    else:
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            detail={
                "operation": "retention_policy_updated",
                "policy_id": str(row.id),
                "retention_days": row.retention_days,
                "legal_hold": row.legal_hold,
            },
            commit=False,
        )

    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete("/policies/{policy_id}")
async def delete_retention_policy(
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get(session, policy_id, ctx.tenant_id)
    if row.legal_hold:
        raise HTTPException(
            status_code=409,
            detail="cannot delete policy under legal hold; release hold first",
        )
    await emit(
        session,
        AuditAction.SECURITY_SETTINGS_CHANGED,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        detail={
            "operation": "retention_policy_deleted",
            "policy_id": str(policy_id),
            "agent_id": row.agent_id,
            "resource_type": row.resource_type,
        },
        commit=False,
    )
    await session.delete(row)
    await session.commit()
    return {"id": str(policy_id), "deleted": True}


@router.post("/recording-policy", response_model=dict, status_code=201)
async def upsert_recording_policy(
    payload: RecordingPolicyConfigRequest,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/retention/recording-policy — Configure recording, consent, and PII redaction policy."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    role = getattr(ctx.user, "role", None)
    try:
        row = await save_recording_policy(
            session,
            tenant,
            environment_id=payload.environment_id,
            agent_id=payload.agent_id,
            enabled=payload.enabled,
            consent_mode=payload.consent_mode,
            disclosure_text=payload.disclosure_text,
            retention_days=payload.retention_days,
            legal_hold=payload.legal_hold,
            raw_access_roles=payload.raw_access_roles,
            redact_pii=payload.redact_pii,
            actor_user_id=ctx.user_id,
            actor_role=role,
        )
    except Forbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    if payload.redact_pii:
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            detail={
                "operation": "recording_policy_saved",
                "policy_id": str(row.id),
                "consent_mode": row.consent_mode,
                "enabled": row.enabled,
            },
            commit=False,
        )
    await session.commit()
    await session.refresh(row)
    return row.as_dict()


@router.get("/recording-policy", response_model=dict)
async def get_effective_recording_policy(
    agent_id: Optional[str] = Query(default=None),
    environment_id: Optional[uuid.UUID] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/retention/recording-policy — Inspect effective recording & PII policy."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    return await effective_recording_policy(
        session, tenant, environment_id=environment_id, agent_id=agent_id
    )


@router.post("/purge", response_model=dict)
async def trigger_purge(
    payload: PurgeTriggerRequest,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/retention/purge — Execute real retention purge (or dry_run count) for tenant."""
    if payload.policy_id is not None:
        row = await _get(session, payload.policy_id, ctx.tenant_id)
        if row.legal_hold:
            raise HTTPException(status_code=409, detail="policy is under legal hold")

    summary = await purge_expired_calls(
        session,
        tenant_id=ctx.tenant_id,
        policy_id=payload.policy_id,
        resource_type=payload.resource_type,
        dry_run=payload.dry_run,
        actor_user_id=ctx.user_id,
    )
    if (
        payload.policy_id is None
        and summary["skipped_legal_hold"] > 0
        and summary["purged_calls"] == 0
        and summary["purged_recordings"] == 0
        and summary["purged_transcripts"] == 0
        and summary["purged_chats"] == 0
        and summary["purged_pcaps"] == 0
    ):
        # Check if tenant-wide legal hold blocked all purging
        rec_hold = await session.scalar(
            select(RecordingPolicy.id).where(
                RecordingPolicy.tenant_id == ctx.tenant_id,
                RecordingPolicy.legal_hold.is_(True),
            )
        )
        ret_hold = await session.scalar(
            select(RetentionPolicy.id).where(
                RetentionPolicy.tenant_id == ctx.tenant_id,
                RetentionPolicy.legal_hold.is_(True),
                RetentionPolicy.agent_id == "",
            )
        )
        if rec_hold is not None or ret_hold is not None:
            raise HTTPException(status_code=409, detail="tenant has policies under legal hold")

    total_purged = (
        summary["purged_calls"]
        + summary["purged_recordings"]
        + summary["purged_transcripts"]
        + summary["purged_chats"]
        + summary["purged_pcaps"]
    )
    return {
        "purged": total_purged,
        "purged_calls": summary["purged_calls"],
        "purged_turns": summary["purged_turns"],
        "purged_recordings": summary["purged_recordings"],
        "purged_transcripts": summary["purged_transcripts"],
        "purged_chats": summary["purged_chats"],
        "purged_pcaps": summary["purged_pcaps"],
        "skipped_legal_hold": summary["skipped_legal_hold"],
        "policies_evaluated": summary["policies_evaluated"],
        "dry_run": bool(payload.dry_run),
        "policy_id": str(payload.policy_id) if payload.policy_id else None,
        "resource_type": payload.resource_type or "all",
        "executed_at": _now().isoformat(),
    }


@router.get("/status", response_model=dict)
async def retention_status(
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/retention/status — Summary of retention policies and legal holds."""
    policies = (
        await session.execute(
            select(RetentionPolicy).where(RetentionPolicy.tenant_id == ctx.tenant_id)
        )
    ).scalars().all()
    rec_holds = int(
        (
            await session.scalar(
                select(func.count(RecordingPolicy.id)).where(
                    RecordingPolicy.tenant_id == ctx.tenant_id,
                    RecordingPolicy.legal_hold.is_(True),
                )
            )
        )
        or 0
    )
    held = sum(1 for p in policies if p.legal_hold) + rec_holds
    return {
        "total_policies": len(policies),
        "legal_holds": held,
        "policies": [p.as_dict() for p in policies],
    }
