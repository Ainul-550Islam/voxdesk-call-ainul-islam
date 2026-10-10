"""Prompt 5 & PART 3: Public Web Widget Bootstrap, Restricted Session, and Chat/Voice Routes.

All endpoints here are strictly scoped to `/api/v1/public/widget/*`:
- Bootstrap (`/config`) and voice bootstrap (`/voice-bootstrap`) require a valid, active,
  non-revoked, non-expired `PublicWidgetKey` (`vdpk_...`) and a server-side allowed
  `Origin`. When voice transport is configured, `/voice-bootstrap` (and `/sessions` with
  `mode="voice"`) returns a real web-call bootstrap (`call_id`, single-use `access_token`,
  `url`); otherwise it returns `NOT_CONFIGURED`.
- `/embed.js` serves the CSP-safe one-line embeddable widget (`widget/embed.js`).
- Session turns (`/sessions/{session_id}/messages`) and termination (`/sessions/{session_id}/end`)
  require a short-lived restricted widget session token (`vdws_...`) bound to the same
  authorized origin.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import client_ip as client_ip_from_request
from app.db.session import get_session
from app.domain.public_widget_models import (
    PublicWidgetBootstrapConfig,
    PublicWidgetEndSessionRequest,
    PublicWidgetMessageSendRequest,
    PublicWidgetMessageSendResponse,
    PublicWidgetSessionCreateRequest,
    PublicWidgetSessionRead,
)
from app.services.public_origin_service import extract_request_origin
from app.services.public_widget_service import (
    create_public_widget_session,
    create_public_widget_voice_bootstrap,
    end_public_widget_session,
    get_public_widget_bootstrap_config,
    get_public_widget_session,
    send_public_widget_message,
)

router = APIRouter(prefix="/api/v1/public/widget", tags=["public-widget"])

_WIDGET_EMBED_PATH = Path(__file__).resolve().parents[2] / "widget" / "embed.js"


class PublicWidgetVoiceBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key: str | None = Field(default=None, max_length=256)
    origin: str | None = Field(default=None, max_length=255)
    dynamic_vars: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _extract_widget_session_token(
    *,
    widget_session_header: str | None,
    authorization_header: str | None,
) -> str | None:
    if widget_session_header and widget_session_header.strip():
        return widget_session_header.strip()
    if authorization_header and authorization_header.strip():
        return authorization_header.strip()
    return None


@router.get("/config", response_model=PublicWidgetBootstrapConfig)
async def get_widget_config(
    request: Request,
    public_key: str | None = Query(None, max_length=256),
    origin: str | None = Query(None, max_length=255),
    x_voxdesk_public_key: str | None = Header(None, alias="X-VoxDesk-Public-Key"),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetBootstrapConfig:
    raw_key = x_voxdesk_public_key or public_key
    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
        explicit_origin=origin,
    )
    ip = client_ip_from_request(request)
    return await get_public_widget_bootstrap_config(
        session,
        raw_public_key=raw_key,
        request_origin=effective_origin,
        client_ip=ip,
    )


@router.post("/voice-bootstrap")
async def create_widget_voice_bootstrap(
    payload: PublicWidgetVoiceBootstrapRequest,
    request: Request,
    x_voxdesk_public_key: str | None = Header(None, alias="X-VoxDesk-Public-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the real web-call bootstrap (public key + origin check) when configured, or NOT_CONFIGURED otherwise."""
    raw_key = x_voxdesk_public_key or payload.public_key
    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
        explicit_origin=payload.origin,
    )
    ip = client_ip_from_request(request)
    return await create_public_widget_voice_bootstrap(
        session,
        raw_public_key=raw_key,
        request_origin=effective_origin,
        dynamic_vars=payload.dynamic_vars,
        metadata=payload.metadata,
        client_ip=ip,
    )


@router.get("/embed.js")
async def get_widget_embed_js() -> Response:
    """Serve the CSP-safe embeddable widget loader (`widget/embed.js`)."""
    if not _WIDGET_EMBED_PATH.is_file():
        raise HTTPException(status_code=404, detail="Widget embed.js not found")
    content = _WIDGET_EMBED_PATH.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="application/javascript; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=300",
        },
    )


@router.post(
    "/sessions",
    response_model=PublicWidgetSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_widget_session(
    payload: PublicWidgetSessionCreateRequest,
    request: Request,
    x_voxdesk_public_key: str | None = Header(None, alias="X-VoxDesk-Public-Key"),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetSessionRead:
    if x_voxdesk_public_key and not payload.public_key:
        payload = payload.model_copy(update={"public_key": x_voxdesk_public_key})

    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
        explicit_origin=payload.origin,
    )
    ip = client_ip_from_request(request)
    ua = request.headers.get("user-agent") or ""
    return await create_public_widget_session(
        session,
        payload=payload,
        request_origin=effective_origin,
        client_ip=ip,
        user_agent=ua,
    )


@router.get("/sessions/{session_id}", response_model=PublicWidgetSessionRead)
async def read_widget_session(
    session_id: uuid.UUID,
    request: Request,
    x_voxdesk_widget_session: str | None = Header(None, alias="X-VoxDesk-Widget-Session"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetSessionRead:
    raw_token = _extract_widget_session_token(
        widget_session_header=x_voxdesk_widget_session,
        authorization_header=authorization,
    )
    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
    )
    ip = client_ip_from_request(request)
    return await get_public_widget_session(
        session,
        session_id=session_id,
        raw_session_token=raw_token,
        request_origin=effective_origin,
        client_ip=ip,
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=PublicWidgetMessageSendResponse,
)
async def post_widget_session_message(
    session_id: uuid.UUID,
    payload: PublicWidgetMessageSendRequest,
    request: Request,
    x_voxdesk_widget_session: str | None = Header(None, alias="X-VoxDesk-Widget-Session"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetMessageSendResponse:
    raw_token = _extract_widget_session_token(
        widget_session_header=x_voxdesk_widget_session,
        authorization_header=authorization,
    )
    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
    )
    ip = client_ip_from_request(request)
    return await send_public_widget_message(
        session,
        session_id=session_id,
        raw_session_token=raw_token,
        request_origin=effective_origin,
        payload=payload,
        client_ip=ip,
    )


@router.post("/sessions/{session_id}/end", response_model=PublicWidgetSessionRead)
async def terminate_widget_session(
    session_id: uuid.UUID,
    request: Request,
    payload: PublicWidgetEndSessionRequest | None = None,
    x_voxdesk_widget_session: str | None = Header(None, alias="X-VoxDesk-Widget-Session"),
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetSessionRead:
    raw_token = _extract_widget_session_token(
        widget_session_header=x_voxdesk_widget_session,
        authorization_header=authorization,
    )
    effective_origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
    )
    ip = client_ip_from_request(request)
    req = payload or PublicWidgetEndSessionRequest()
    return await end_public_widget_session(
        session,
        session_id=session_id,
        raw_session_token=raw_token,
        request_origin=effective_origin,
        payload=req,
        client_ip=ip,
    )
