"""Live browser web-call HTTP & WebSocket routes (PART 3 — Gate G4).

Endpoints:
- `POST /api/web-calls` — Create a browser web call using a server API key / JWT (`Permission.CALL_WRITE`)
- `POST /api/public/web-calls` — Create a browser web call using a scoped `PublicWidgetKey` (`vdpk_...`) + `Origin` check
- `GET /api/web-calls/{call_id}` — Get tenant-scoped web call status, transcript turns, and latency metrics (`Permission.CALL_READ`)
- `POST /api/web-calls/{call_id}/end` — End an active tenant-scoped web call (`Permission.CALL_WRITE`)
- `WS /telephony/web/ws` — Browser audio WebSocket speaking Pipecat Protobuf frames
- `POST /telephony/web/offer` — Phase-2 SmallWebRTC SDP signaling endpoint
- `GET /widget/embed.js` — Serve the CSP-safe one-line embeddable widget script
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.rate_limit import client_ip as client_ip_from_request
from app.db.session import get_session
from app.services import public_auth_boundary_service as _boundary_svc
from app.services.public_origin_service import extract_request_origin
from app.telephony.web_call import (
    DEFAULT_WEB_CALL_TTL_SECONDS,
    WebCallSecurityError,
    create_public_web_call,
    create_web_call,
    end_web_call,
    get_web_call,
)
from app.telephony.web_transport import router as web_transport_router

# Register `/api/public/web-calls` and `/widget/embed.js` in the public widget boundary
# so `vdpk_*` keys are accepted on `/api/public/web-calls` while remaining blocked on
# all private `/api/*` routes.
for _prefix in ("/api/public/web-calls", "/widget/embed.js"):
    if _prefix not in _boundary_svc.PUBLIC_WIDGET_PREFIX_ROUTES:
        _boundary_svc.PUBLIC_WIDGET_PREFIX_ROUTES = (
            *_boundary_svc.PUBLIC_WIDGET_PREFIX_ROUTES,
            _prefix,
        )

router = APIRouter(tags=["web-calls-live"])
router.include_router(web_transport_router)

_WIDGET_EMBED_PATH = Path(__file__).resolve().parents[2] / "widget" / "embed.js"


class WebCallCreateServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=120)
    version: int | None = Field(default=None, ge=1)
    agent_version: int | None = Field(default=None, ge=1)
    dynamic_vars: dict[str, Any] = Field(default_factory=dict)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    transport: str = Field(default="websocket", pattern="^(websocket|webrtc)$")
    ttl_seconds: int = Field(default=DEFAULT_WEB_CALL_TTL_SECONDS, ge=30, le=3600)
    origin: str | None = Field(default=None, max_length=255)


class WebCallCreatePublicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key: str | None = Field(default=None, max_length=256)
    agent_id: str | None = Field(default=None, max_length=120)
    version: int | None = Field(default=None, ge=1)
    dynamic_vars: dict[str, Any] = Field(default_factory=dict)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    transport: str = Field(default="websocket", pattern="^(websocket|webrtc)$")
    origin: str | None = Field(default=None, max_length=255)


class WebCallEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="ended_by_client", max_length=200)


@router.post(
    "/api/web-calls",
    status_code=status.HTTP_201_CREATED,
)
async def create_web_call_server_endpoint(
    payload: WebCallCreateServerRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a browser web call authenticated by a server API key or operator JWT."""
    effective_version = payload.version if payload.version is not None else payload.agent_version
    merged_vars = {**payload.dynamic_variables, **payload.dynamic_vars}
    req_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
        explicit_origin=payload.origin,
    )
    try:
        return await create_web_call(
            session,
            tenant=ctx.tenant,
            agent_id=payload.agent_id,
            version=effective_version,
            dynamic_vars=merged_vars,
            metadata=payload.metadata,
            environment_id=ctx.environment_id,
            origin=req_origin,
            transport=payload.transport,
            ttl_seconds=payload.ttl_seconds,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
        )
    except WebCallSecurityError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=exc.status_code,
            content={
                "detail": {"code": exc.code, "message": exc.message},
                "error": {"code": exc.code, "message": exc.message},
            },
        )


@router.post(
    "/api/public/web-calls",
    status_code=status.HTTP_201_CREATED,
)
async def create_web_call_public_endpoint(
    payload: WebCallCreatePublicRequest,
    request: Request,
    x_voxdesk_public_key: str | None = Header(default=None, alias="X-VoxDesk-Public-Key"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a browser web call using a scoped `PublicWidgetKey` (`vdpk_...`) and validated `Origin`."""
    raw_key = x_voxdesk_public_key or payload.public_key
    if not raw_key and authorization:
        cleaned = authorization.strip()
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        if cleaned.startswith("vdpk_"):
            raw_key = cleaned

    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
        explicit_origin=payload.origin,
    )
    merged_vars = {**payload.dynamic_variables, **payload.dynamic_vars}
    ip = client_ip_from_request(request)
    try:
        return await create_public_web_call(
            session,
            raw_public_key=raw_key,
            request_origin=effective_origin,
            agent_id=payload.agent_id,
            version=payload.version,
            dynamic_vars=merged_vars,
            metadata=payload.metadata,
            transport=payload.transport,
            client_ip=ip,
        )
    except WebCallSecurityError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=exc.status_code,
            content={
                "detail": {"code": exc.code, "message": exc.message},
                "error": {"code": exc.code, "message": exc.message},
            },
        )


@router.get("/api/web-calls/{call_id}")
async def get_web_call_endpoint(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve a tenant-scoped web call's status, transcript turns, and latency metrics."""
    try:
        return await get_web_call(
            session,
            tenant_id=ctx.tenant_id,
            call_id=call_id,
        )
    except WebCallSecurityError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=exc.status_code,
            content={
                "detail": {"code": exc.code, "message": exc.message},
                "error": {"code": exc.code, "message": exc.message},
            },
        )


@router.post("/api/web-calls/{call_id}/end")
async def end_web_call_endpoint(
    call_id: uuid.UUID,
    payload: WebCallEndRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """End an active tenant-scoped web call."""
    req = payload or WebCallEndRequest()
    try:
        return await end_web_call(
            session,
            tenant_id=ctx.tenant_id,
            call_id=call_id,
            reason=req.reason,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
        )
    except WebCallSecurityError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=exc.status_code,
            content={
                "detail": {"code": exc.code, "message": exc.message},
                "error": {"code": exc.code, "message": exc.message},
            },
        )


@router.get("/widget/embed.js")
async def serve_widget_embed_script() -> Response:
    """Serve the CSP-safe one-line embeddable widget script (`widget/embed.js`)."""
    if not _WIDGET_EMBED_PATH.is_file():
        raise HTTPException(status_code=404, detail="Widget script not found")
    content = _WIDGET_EMBED_PATH.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="application/javascript; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=300",
        },
    )
