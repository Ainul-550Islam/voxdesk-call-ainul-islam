from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api_tools.service import ApiToolError, execute, validate_definition
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import ApiTool
from app.db.session import get_session

router = APIRouter(prefix="/api/api-tools", tags=["api-tools"])


class ToolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    method: str
    url_template: str
    parameter_schema: dict = Field(default_factory=dict)
    body_schema: dict = Field(default_factory=dict)
    result_schema: dict = Field(default_factory=dict)
    auth_ref: str | None = None
    timeout_seconds: float = Field(default=10, ge=0.1, le=30)
    retry_max_attempts: int = Field(default=3, ge=1, le=5)


class ExecuteRequest(BaseModel):
    arguments: dict = Field(default_factory=dict)


@router.get("")
async def list_tools(
    ctx: TenantContext = Depends(require_permission(Permission.API_TOOL_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(ApiTool).where(ApiTool.tenant_id == ctx.tenant_id).order_by(ApiTool.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "method": r.method,
            "url_template": r.url_template,
            "enabled": r.enabled,
        }
        for r in rows
    ]


@router.post("", status_code=201)
async def create_tool(
    body: ToolCreate,
    ctx: TenantContext = Depends(require_permission(Permission.API_TOOL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        validate_definition(
            method=body.method,
            url_template=body.url_template,
            parameter_schema=body.parameter_schema,
            body_schema=body.body_schema,
            result_schema=body.result_schema,
        )
    except ApiToolError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.auth_ref and not body.auth_ref.startswith("secret://"):
        raise HTTPException(422, "auth_ref must be an opaque secret:// reference")
    row = ApiTool(
        tenant_id=ctx.tenant_id,
        name=body.name,
        description=body.description,
        method=body.method.upper(),
        url_template=body.url_template,
        parameter_schema=body.parameter_schema,
        body_schema=body.body_schema,
        result_schema=body.result_schema,
        auth_ref=body.auth_ref,
        timeout_seconds=body.timeout_seconds,
        retry_max_attempts=body.retry_max_attempts,
    )
    session.add(row)
    await session.commit()
    return {"id": str(row.id), "name": row.name, "method": row.method}


@router.post("/{tool_id}/execute")
async def execute_tool(
    tool_id: uuid.UUID,
    body: ExecuteRequest,
    idempotency_key: str = Header("", alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(Permission.API_TOOL_EXECUTE)),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(ApiTool).where(ApiTool.id == tool_id, ApiTool.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "API tool not found")
    try:
        result = await execute(session, row, body.arguments, idempotency_key=idempotency_key)
    except ApiToolError as exc:
        raise HTTPException(422, str(exc)) from exc
    await session.commit()
    if not result.ok:
        raise HTTPException(
            result.status_code if result.status_code >= 400 else 502,
            {"error": result.error_category, "attempts": result.attempts},
        )
    return {
        "ok": True,
        "status_code": result.status_code,
        "data": result.data,
        "attempts": result.attempts,
        "duration_ms": round(result.duration_ms, 2),
    }
