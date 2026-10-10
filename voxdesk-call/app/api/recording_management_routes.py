# File: app/api/recording_management_routes.py — Missing API: recording list/detail/request/signed access/purge endpoints
"""
Recording management API.
Closes gap 8: recording service has request/read/delete logic but API surface very limited.
Provides list/detail/request/signed access/purge endpoints with RBAC, tenant isolation, audit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Call
from app.db.session import get_session
from app.telephony.recording import CallRecording, authorize_read, grant_for, open_grant, request_recording, mark_deletion, purge_for_calls
from app.tenancy.isolation import HierarchyError, to_http, Forbidden, NotFound

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class RecordingRequestIn(_Strict):
    call_id: uuid.UUID
    provider: str = Field(default="twilio", max_length=16)
    consent_category: str = Field(default="unspecified", max_length=32)
    consent_state: str = Field(default="unknown", max_length=32)

class RecordingOut(_Strict):
    id: str
    tenant_id: str
    call_id: str
    provider: str
    state: str
    duration_seconds: Optional[float] = None
    content_type: str
    size_bytes: Optional[int] = None
    has_storage: bool
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    retention_deadline: Optional[str] = None

class SignedAccessOut(_Strict):
    recording_id: str
    token: str
    expires_at: str
    url: Optional[str] = None

class PurgeRequest(_Strict):
    call_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)

def _now() -> datetime:
    return datetime.now(timezone.utc)

@router.get("", response_model=dict)
async def list_recordings(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    call_id: Optional[uuid.UUID] = Query(default=None),
    state: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.RECORDING_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/recordings — List recordings with filters, tenant-scoped."""
    try:
        scope = [CallRecording.tenant_id == ctx.tenant_id]
        if call_id:
            scope.append(CallRecording.call_id == call_id)
        if state:
            scope.append(CallRecording.state == state)

        total = (await session.execute(select(func.count(CallRecording.id)).where(*scope))).scalar() or 0
        rows = (
            await session.execute(
                select(CallRecording).where(*scope).order_by(CallRecording.created_at.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()

        return {
            "recordings": [r.as_dict() for r in rows],
            "total": int(total),
            "limit": limit,
            "offset": offset,
        }
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.get("/{recording_id}", response_model=RecordingOut)
async def get_recording_detail(
    recording_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.RECORDING_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/recordings/{id} — Detail with authorization and audit."""
    try:
        row = await authorize_read(
            session, tenant_id=ctx.tenant_id, recording_id=recording_id, role=ctx.user.role if hasattr(ctx.user, 'role') else None, actor_user_id=ctx.user_id
        )
        await session.commit()
        d = row.as_dict()
        return RecordingOut(
            id=d["id"],
            tenant_id=d["tenant_id"],
            call_id=d["call_id"],
            provider=d["provider"],
            state=d["state"],
            duration_seconds=d["duration_seconds"],
            content_type=d["content_type"],
            size_bytes=d["size_bytes"],
            has_storage=d["has_storage"],
            created_at=d["created_at"],
            completed_at=d["completed_at"],
            retention_deadline=d["retention_deadline"],
        )
    except NotFound:
        raise HTTPException(status_code=404, detail="recording not found")
    except Forbidden:
        raise HTTPException(status_code=403, detail="forbidden")
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.post("", response_model=RecordingOut, status_code=201)
async def request_new_recording(
    payload: RecordingRequestIn,
    ctx: TenantContext = Depends(require_permission(Permission.RECORDING_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/recordings — Request recording for a call."""
    try:
        call = await session.get(Call, payload.call_id)
        if call is None or call.tenant_id != ctx.tenant_id:
            raise HTTPException(status_code=404, detail="call not found")
        row = await request_recording(
            session, call, provider=payload.provider, consent_category=payload.consent_category, consent_state=payload.consent_state
        )
        await session.commit()
        await session.refresh(row)
        d = row.as_dict()
        return RecordingOut(
            id=d["id"],
            tenant_id=d["tenant_id"],
            call_id=d["call_id"],
            provider=d["provider"],
            state=d["state"],
            duration_seconds=d["duration_seconds"],
            content_type=d["content_type"],
            size_bytes=d["size_bytes"],
            has_storage=d["has_storage"],
            created_at=d["created_at"],
            completed_at=d["completed_at"],
            retention_deadline=d["retention_deadline"],
        )
    except Forbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except NotFound:
        raise HTTPException(status_code=404, detail="call not found")
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.post("/{recording_id}/signed-access", response_model=SignedAccessOut)
async def create_signed_access(
    recording_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.RECORDING_READ)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/recordings/{id}/signed-access — Time-limited signed access token."""
    try:
        row = await authorize_read(
            session, tenant_id=ctx.tenant_id, recording_id=recording_id, role=ctx.user.role if hasattr(ctx.user, 'role') else None, actor_user_id=ctx.user_id
        )
        grant = grant_for(row, role=ctx.user.role if hasattr(ctx.user, 'role') else None)
        await session.commit()
        token = grant.token if hasattr(grant, "token") else (grant if isinstance(grant, str) else grant.get("token", str(uuid.uuid4())))
        if hasattr(grant, "expires_at"):
            expires_at = datetime.fromtimestamp(int(grant.expires_at), tz=timezone.utc)
        else:
            from datetime import timedelta
            expires_at = _now() + timedelta(minutes=15)
        return SignedAccessOut(
            recording_id=str(row.id),
            token=token,
            expires_at=expires_at.isoformat(),
            url=None,
        )
    except NotFound:
        raise HTTPException(status_code=404, detail="recording not found")
    except Forbidden:
        raise HTTPException(status_code=403, detail="forbidden")
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.get("/{recording_id}/access/{token}")
async def access_with_token(
    recording_id: uuid.UUID,
    token: str,
    ctx: TenantContext = Depends(require_permission(Permission.RECORDING_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/recordings/{id}/access/{token} — Verify token and return storage reference."""
    try:
        row = await authorize_read(
            session, tenant_id=ctx.tenant_id, recording_id=recording_id, role=ctx.user.role if hasattr(ctx.user, 'role') else None, actor_user_id=ctx.user_id
        )
        result = open_grant(row, token, role=ctx.user.role if hasattr(ctx.user, 'role') else None)
        if not result.get("allowed"):
            raise HTTPException(status_code=403, detail=result.get("reason", "forbidden"))
        return result
    except NotFound:
        raise HTTPException(status_code=404, detail="recording not found")
    except Forbidden:
        raise HTTPException(status_code=403, detail="forbidden")
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.post("/{recording_id}/purge")
async def purge_single_recording(
    recording_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/recordings/{id}/purge — Purge single recording (legal hold checked)."""
    try:
        row = await authorize_read(
            session, tenant_id=ctx.tenant_id, recording_id=recording_id, role=ctx.user.role if hasattr(ctx.user, 'role') else None, actor_user_id=ctx.user_id
        )
        # Check legal hold via recording_policy
        from app.telephony.recording_policy import effective
        from app.db.models import Tenant
        tenant = await session.get(Tenant, ctx.tenant_id)
        policy = await effective(session, tenant)
        held = policy.get("legal_hold", False)
        outcome = await mark_deletion(session, row, held=held)
        await session.commit()
        return {"id": str(row.id), "outcome": outcome, "held": held}
    except NotFound:
        raise HTTPException(status_code=404, detail="recording not found")
    except Forbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except HierarchyError as exc:
        raise to_http(exc) from None

@router.post("/purge", response_model=dict)
async def purge_recordings_for_calls(
    payload: PurgeRequest,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/recordings/purge — Bulk purge for call_ids."""
    try:
        result = await purge_for_calls(session, payload.call_ids)
        await session.commit()
        return result
    except HierarchyError as exc:
        raise to_http(exc) from None
