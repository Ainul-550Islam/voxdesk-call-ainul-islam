# File: app/api/post_call_analysis_routes.py — Missing APIs: custom post-call-analysis definition CRUD, per-call results, custom fields, backfill/reprocess with idempotency
"""
Post-call analysis API.
Closes gaps:
11. Custom post-call-analysis definition API missing — configurable analysis schema CRUD + per-call results
12. Historical analysis backfill API missing — bulk backfill/reprocess + status + idempotency
34. Post-call custom fields API — Boolean/Text/Number/Enum/custom schema matching Retell model
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.session import get_session
from app.db.enterprise_models import AnalysisSchema, AnalysisResult, BackfillJob
from app.tenancy.isolation import HierarchyError, to_http
from app.environments.context_resolution import resolve_environment
from app.environments.membership import resolve as resolve_membership
from app.services import post_call_analysis_service as analysis
from app.audit.service import record_event
from app.core.rate_limit import rate_limit
from app.resilience.idempotency import claim_request, complete_request, IdempotencyError

router = APIRouter(prefix="/api/analysis", tags=["post-call-analysis"])

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class CustomFieldDef(_Strict):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    type: str = Field(pattern="^(boolean|text|number|enum|custom|list)$")
    description: str = Field(default="", max_length=500)
    required: bool = False
    enum_values: Optional[list[str]] = Field(default=None, max_length=50)
    default: Optional[Any] = None
    examples: list[Any] = Field(default_factory=list, max_length=5)
    items_type: str = Field(default="text", pattern="^(text|number|boolean)$")

class AnalysisSchemaCreate(_Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    fields: list[CustomFieldDef] = Field(min_length=1, max_length=100)

class AnalysisSchemaUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    fields: Optional[list[CustomFieldDef]] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None

class AnalysisSchemaOut(_Strict):
    environment_id: str
    version: int
    id: str
    tenant_id: str
    name: str
    description: str
    fields: list[dict]
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class AnalysisResultOut(_Strict):
    id: str
    tenant_id: str
    call_id: str
    schema_id: str
    result: dict
    status: str
    created_at: Optional[str] = None

class BackfillRequest(_Strict):
    schema_id: uuid.UUID
    call_ids: Optional[list[uuid.UUID]] = Field(default=None, min_length=1, max_length=200, description="If omitted, select up to 200 eligible calls; larger selections are rejected")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)

class BackfillOut(_Strict):
    environment_id: Optional[str] = None
    schema_version: Optional[int] = None
    job_ids: list[str] = Field(default_factory=list)
    cancelled_calls: int = 0
    id: str
    tenant_id: str
    schema_id: str
    status: str
    total_calls: int
    processed_calls: int
    failed_calls: int
    idempotency_key: str
    created_at: Optional[str] = None
    completed_at: Optional[str] = None

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _schema_out(row: AnalysisSchema) -> AnalysisSchemaOut:
    d = row.as_dict()
    return AnalysisSchemaOut(
        id=d["id"],
        environment_id=d["environment_id"],
        version=d["version"],
        tenant_id=d["tenant_id"],
        name=d["name"],
        description=d["description"],
        fields=d["fields"],
        is_active=d["is_active"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


async def _environment(session, ctx, *, write=False, permission=None):
    try:
        environment = await resolve_environment(session, user=ctx.user, tenant=ctx.tenant, environment_id=ctx.environment_id)
        if environment is None:
            raise HTTPException(409, detail={"code": "NOT_CONFIGURED", "capability": "environment"})
        decision = await resolve_membership(session, ctx.user, environment, ctx.tenant)
        if not decision.allowed or environment.status != "active":
            raise HTTPException(404, detail="environment not found")
        if not has_permission(decision.role, permission or (Permission.QA_WRITE if write else Permission.QA_READ)):
            raise HTTPException(403, detail="environment permission denied")
        return environment.id
    except HierarchyError as exc:
        raise to_http(exc) from None


async def _owned_schema(session, ctx, schema_id, *, write=False):
    environment_id = await _environment(session, ctx, write=write)
    row = await session.scalar(select(AnalysisSchema).where(
        AnalysisSchema.id == schema_id, AnalysisSchema.tenant_id == ctx.tenant_id,
        AnalysisSchema.environment_id == environment_id).with_for_update())
    if row is None:
        raise HTTPException(404, detail="schema not found")
    return row


async def _audit_schema(session, ctx, row, action):
    await record_event(session, tenant_id=ctx.tenant_id, environment_id=row.environment_id,
                       actor_user_id=ctx.user_id, event_type=action,
                       resource_type="analysis_schema", resource_id=row.id,
                       detail={"version": row.version})

@router.post("/schemas", response_model=AnalysisSchemaOut, status_code=201)
async def create_schema(
    payload: AnalysisSchemaCreate,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/analysis/schemas — Create configurable analysis schema with custom fields."""
    environment_id = await _environment(session, ctx, write=True)
    try:
        row = await analysis.create_schema(session, tenant_id=ctx.tenant_id,
            environment_id=environment_id, name=payload.name, description=payload.description,
            fields=[field.model_dump() for field in payload.fields])
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    await _audit_schema(session, ctx, row, "analysis.schema_created")
    await session.commit()
    await session.refresh(row)
    return _schema_out(row)

@router.get("/schemas", response_model=dict)
async def list_schemas(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    is_active: Optional[bool] = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    environment_id = await _environment(session, ctx)
    scope = [AnalysisSchema.tenant_id == ctx.tenant_id, AnalysisSchema.environment_id == environment_id]
    if is_active is not None:
        scope.append(AnalysisSchema.is_active == is_active)
    total = (await session.execute(select(func.count(AnalysisSchema.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(AnalysisSchema).where(*scope).order_by(AnalysisSchema.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {"schemas": [_schema_out(r).model_dump() for r in rows], "total": int(total), "limit": limit, "offset": offset}

@router.get("/schemas/{schema_id}", response_model=AnalysisSchemaOut)
async def get_schema(
    schema_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    return _schema_out(await _owned_schema(session, ctx, schema_id))

@router.patch("/schemas/{schema_id}", response_model=AnalysisSchemaOut)
async def update_schema(
    schema_id: uuid.UUID,
    payload: AnalysisSchemaUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _owned_schema(session, ctx, schema_id, write=True)
    try:
        await analysis.update_schema(session, row, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    row.updated_at = _now()
    await _audit_schema(session, ctx, row, "analysis.schema_updated")
    await session.commit()
    await session.refresh(row)
    return _schema_out(row)

@router.delete("/schemas/{schema_id}")
async def delete_schema(
    schema_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _owned_schema(session, ctx, schema_id, write=True)
    await analysis.update_schema(session, row, {"is_active": False})
    await _audit_schema(session, ctx, row, "analysis.schema_deactivated")
    await session.commit()
    return {"id": str(schema_id), "deleted": False, "deactivated": True, "reason": "preserve_version_history"}

@router.get("/results", response_model=dict)
async def list_results(
    call_id: Optional[uuid.UUID] = Query(default=None),
    schema_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/analysis/results — Per-call results with filters."""
    environment_id = await _environment(session, ctx)
    scope = [AnalysisResult.tenant_id == ctx.tenant_id, AnalysisResult.environment_id == environment_id]
    if call_id:
        scope.append(AnalysisResult.call_id == call_id)
    if schema_id:
        scope.append(AnalysisResult.schema_id == schema_id)
    total = (await session.execute(select(func.count(AnalysisResult.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(AnalysisResult).where(*scope).order_by(AnalysisResult.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {
        "results": [r.as_dict() for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }

@router.get("/calls/{call_id}/results", response_model=dict)
async def get_call_results(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/analysis/calls/{id}/results — All analysis results for a call."""
    from app.db.models import Call
    environment_id = await _environment(session, ctx)
    call = await session.scalar(select(Call).where(Call.id == call_id, Call.tenant_id == ctx.tenant_id,
                                                   Call.environment_id == environment_id))
    if call is None:
        raise HTTPException(404, detail="call not found")
    rows = (
        await session.execute(
            select(AnalysisResult).where(AnalysisResult.tenant_id == ctx.tenant_id, AnalysisResult.environment_id == environment_id, AnalysisResult.call_id == call_id).order_by(AnalysisResult.created_at.desc())
        )
    ).scalars().all()
    return {"call_id": str(call_id), "results": [r.as_dict() for r in rows], "total": len(rows)}

@router.post("/backfill", response_model=BackfillOut, status_code=201)
async def create_backfill(
    payload: BackfillRequest,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Admit pinned, bounded custom analysis into the ordinary durable worker."""
    schema = await _owned_schema(session, ctx, payload.schema_id, write=True)
    if payload.idempotency_key and x_idempotency_key and payload.idempotency_key != x_idempotency_key:
        raise HTTPException(422, detail="Header and body idempotency keys disagree")
    key = x_idempotency_key or payload.idempotency_key
    if not key:
        raise HTTPException(422, detail="Idempotency-Key is required")
    try:
        if not await rate_limit(f"analysis:admission:{ctx.tenant_id}:{schema.environment_id}", 10, 60):
            raise HTTPException(429, detail="Admission rate exceeded or backend unavailable", headers={"Retry-After": "60"})
    except RuntimeError:
        raise HTTPException(501, detail={"code": "NOT_CONFIGURED", "capability": "redis_rate_limit"}) from None
    try:
        claim = await claim_request(session, tenant_id=ctx.tenant_id, environment_id=schema.environment_id,
            operation="analysis.backfill", key=key, request_data=payload.model_dump(mode="json", exclude={"idempotency_key"}))
        if claim.replayed:
            batch = await session.scalar(select(BackfillJob).where(BackfillJob.id == uuid.UUID(claim.receipt.resource_id),
                BackfillJob.tenant_id == ctx.tenant_id, BackfillJob.environment_id == schema.environment_id))
            if batch is None:
                raise HTTPException(409, detail="Original backfill is unavailable")
            return await analysis.backfill_status(session, batch)
        ids = await analysis.select_backfill_calls(session, tenant_id=ctx.tenant_id,
            environment_id=schema.environment_id, call_ids=payload.call_ids,
            start_date=payload.start_date, end_date=payload.end_date)
        batch = await analysis.admit_backfill(session, tenant=ctx.tenant, schema=schema, call_ids=ids,
                                             actor_user_id=ctx.user_id, key_digest=claim.receipt.key_digest)
        await record_event(session, tenant_id=ctx.tenant_id, environment_id=schema.environment_id,
            actor_user_id=ctx.user_id, event_type="analysis.backfill_admitted", resource_type="analysis_backfill",
            resource_id=batch.id, detail={"schema_version": schema.version, "call_count": len(ids)})
        await complete_request(session, claim.receipt, resource_type="analysis_backfill", resource_id=batch.id)
        await session.commit()
        return await analysis.backfill_status(session, batch)
    except IdempotencyError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from None
    except LookupError:
        raise HTTPException(404, detail="Call not found in eligible scope") from None
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None

@router.get("/backfill", response_model=dict)
async def list_backfills(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    environment_id = await _environment(session, ctx)
    scope = (BackfillJob.tenant_id == ctx.tenant_id, BackfillJob.environment_id == environment_id)
    total = await session.scalar(select(func.count(BackfillJob.id)).where(*scope))
    rows = (await session.scalars(select(BackfillJob).where(*scope).order_by(BackfillJob.created_at.desc(), BackfillJob.id)
                                   .offset(offset).limit(limit))).all()
    return {"jobs": [await analysis.backfill_status(session, row) for row in rows],
            "total": total, "limit": limit, "offset": offset}

@router.get("/backfill/{job_id}", response_model=BackfillOut)
async def get_backfill(
    job_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    environment_id = await _environment(session, ctx)
    row = await session.scalar(select(BackfillJob).where(BackfillJob.id == job_id,
        BackfillJob.tenant_id == ctx.tenant_id, BackfillJob.environment_id == environment_id))
    if row is None:
        raise HTTPException(404, detail="backfill job not found")
    return await analysis.backfill_status(session, row)


# Compatibility paths retained, backed by the same scoped analysis service.
compat_router = APIRouter(prefix="/extended")


@compat_router.get("/health", response_model=dict)
async def extended_health_check(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Check scoped database access, not unprobed provider connectivity."""
    environment_id = await _environment(session, ctx)
    count = await session.scalar(select(func.count(AnalysisSchema.id)).where(
        AnalysisSchema.tenant_id == ctx.tenant_id, AnalysisSchema.environment_id == environment_id))
    return {"tenant_id": str(ctx.tenant_id), "environment_id": str(environment_id),
            "database": "reachable", "schemas": count, "provider_connectivity": "not_probed", "at": _now().isoformat()}


@compat_router.get("/stats", response_model=dict)
async def extended_stats(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Count stored definitions, results and manifests without inventing execution."""
    environment_id = await _environment(session, ctx)
    return await analysis.observed_stats(session, tenant_id=ctx.tenant_id, environment_id=environment_id)


@compat_router.get("/config", response_model=dict)
async def extended_config(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Expose the implemented input contract and limits, never provider secrets."""
    environment_id = await _environment(session, ctx)
    return {"tenant_id": str(ctx.tenant_id), "environment_id": str(environment_id),
            "definition_contract": AnalysisSchemaCreate.model_json_schema(),
            "backfill_max_calls": analysis.MAX_BACKFILL_CALLS,
            "backfill_execution_limit_per_minute": analysis.BACKFILL_PER_MINUTE,
            "cache_clear": "unsupported"}


@compat_router.get("/metrics", response_model=dict)
async def extended_metrics(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Read durable job and step states, not legacy backfill progress counters."""
    environment_id = await _environment(session, ctx)
    return await analysis.observed_metrics(session, tenant_id=ctx.tenant_id, environment_id=environment_id)


@compat_router.post("/validate", response_model=dict)
async def extended_validate(
    payload: dict,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Read-only validation using the same definition contract/compiler as create."""
    await _environment(session, ctx)
    try:
        definition = AnalysisSchemaCreate.model_validate(payload)
        compiled = analysis.compile_fields([field.model_dump() for field in definition.fields])
    except ValidationError as exc:
        return {"valid": False, "errors": [{"location": list(error["loc"]), "type": error["type"], "message": error["msg"]}
            for error in exc.errors(include_input=False, include_url=False, include_context=False)]}
    except ValueError as exc:
        return {"valid": False, "errors": [{"type": "definition_invalid", "message": str(exc)}]}
    return {"valid": True, "errors": [], "compiled_schema": compiled}


@compat_router.get("/audit", response_model=dict)
async def extended_audit_log(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Read scoped analysis audit metadata; no bodies, emails or IP addresses."""
    if not ctx.can(Permission.QA_READ):
        raise HTTPException(403, detail="analysis permission denied")
    environment_id = await _environment(session, ctx, permission=Permission.AUDIT_READ)
    return await analysis.audit_page(session, tenant_id=ctx.tenant_id, environment_id=environment_id,
                                     limit=limit, offset=offset)


@compat_router.get("/list", response_model=dict)
async def extended_list(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Compatibility list of real scoped analysis definitions."""
    page = await list_schemas(limit=limit, offset=offset, is_active=None, ctx=ctx, session=session)
    return {"items": page["schemas"], "total": page["total"], "limit": limit, "offset": offset}


@compat_router.post("/bulk", response_model=dict)
async def extended_bulk(
    payload: dict,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Compatibility backfill admission; use the body idempotency_key."""
    try:
        request = BackfillRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, detail=[{"location": list(error["loc"]), "type": error["type"], "message": error["msg"]}
            for error in exc.errors(include_input=False, include_url=False, include_context=False)]) from None
    return await create_backfill(request, ctx=ctx, session=session, x_idempotency_key=None)


@compat_router.delete("/cache", response_model=dict)
async def extended_clear_cache(
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """No analysis cache exists; persisted evidence must not be called a cache."""
    await _environment(session, ctx, write=True)
    raise HTTPException(501, detail={"code": "UNSUPPORTED_CAPABILITY", "capability": "analysis_cache_clear"})


router.include_router(compat_router)


@router.post("/backfill/preview", response_model=dict)
async def preview_backfill(
    payload: BackfillRequest,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Read-only selection/cost estimate; unknown prices remain unknown."""
    from app.ai.models import GovernanceError
    schema = await _owned_schema(session, ctx, payload.schema_id)
    try:
        ids = await analysis.select_backfill_calls(session, tenant_id=ctx.tenant_id,
            environment_id=schema.environment_id, call_ids=payload.call_ids,
            start_date=payload.start_date, end_date=payload.end_date)
        return await analysis.preview_backfill(session, tenant=ctx.tenant, schema=schema, call_ids=ids)
    except LookupError:
        raise HTTPException(404, detail="Call not found in eligible scope") from None
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    except GovernanceError as exc:
        raise HTTPException(409, detail={"code": str(exc.code)}) from None


@router.get("/results/export")
async def export_results(
    schema_id: Optional[uuid.UUID] = Query(default=None),
    call_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Download one bounded, scoped JSON page, including pagination metadata."""
    from fastapi.responses import JSONResponse
    from app.db.models import Call
    environment_id = await _environment(session, ctx)
    if schema_id is not None:
        await _owned_schema(session, ctx, schema_id)
    if call_id is not None:
        owned = await session.scalar(select(Call.id).where(Call.id == call_id,
            Call.tenant_id == ctx.tenant_id, Call.environment_id == environment_id))
        if owned is None:
            raise HTTPException(404, detail="call not found")
    page = await list_results(call_id=call_id, schema_id=schema_id, limit=limit, offset=offset, ctx=ctx, session=session)
    return JSONResponse(page, headers={"Content-Disposition": 'attachment; filename="analysis-results.json"',
                                      "Cache-Control": "no-store"})
