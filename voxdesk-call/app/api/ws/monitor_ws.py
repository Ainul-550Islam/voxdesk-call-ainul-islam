# File: app/api/ws/monitor_ws.py — WebSocket /ws/monitor/{call_id} for live supervisor audio + transcript streaming
"""Supervisor Live Monitoring WebSocket `/ws/monitor/{call_id}` (Part 4 / Gate G5).

Features:
  - Short-lived signed JWT authentication (`calls.monitor` scope, bound to `tenant_id`,
    `call_id`, `supervisor_id`, and `session_id`).
  - Enforces tenant isolation and active call/session status before streaming.
  - Enforces a hard cap of ``MAX_SUPERVISORS_PER_CALL`` (5) concurrent supervisors per call.
  - Streams caller/agent PCM16 binary frames (`send_bytes`) and transcript/control JSON
    events (`send_json`) from ``monitor_bus``.
  - Supports in-band supervisor heartbeat and whisper-to-AI guidance commands.
  - Closes cleanly when the monitored call ends.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import jwt
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db.enterprise_models import LiveCallSession
from app.db.models import Call, CallStatus
from app.db.session import get_session
from app.telephony.monitor_bus import (
    MAX_SUBSCRIBERS_PER_CALL,
    MonitorCapacityError,
    get_monitor_bus,
)
from app.telephony.takeover import is_takeover_pending

router = APIRouter(tags=["live-monitoring-ws"])

MAX_SUPERVISORS_PER_CALL = MAX_SUBSCRIBERS_PER_CALL
DEFAULT_WS_TOKEN_TTL_SECONDS = 300
MONITOR_TOKEN_SCOPE = "calls.monitor"


class MonitorTokenError(ValueError):
    """Base error for live monitoring WebSocket token validation."""

    code = "invalid_monitor_token"


class MonitorTokenExpiredError(MonitorTokenError):
    """Raised when a short-lived monitoring WebSocket token has expired."""

    code = "expired_monitor_token"


def _signing_secret() -> str:
    import hashlib

    for attr in ("media_stream_signing_secret", "jwt_secret_key", "secret_key"):
        val = (getattr(settings, attr, None) or "").strip()
        if val:
            if len(val.encode("utf-8")) < 32:
                return hashlib.sha256(val.encode("utf-8")).hexdigest()
            return val
    if getattr(settings, "environment", "development").lower() == "production":
        raise RuntimeError("Monitoring WebSocket signing secret is not configured")
    return "dev-voxdesk-monitor-ws-signing-secret-do-not-use-in-prod"


def issue_monitor_ws_token(
    *,
    call_id: str | uuid.UUID,
    tenant_id: str | uuid.UUID,
    supervisor_id: str | uuid.UUID,
    session_id: str | uuid.UUID | None = None,
    mode: str = "listen",
    ttl_seconds: int = DEFAULT_WS_TOKEN_TTL_SECONDS,
) -> str:
    """Mint a short-lived JWT authorizing `/ws/monitor/{call_id}`."""
    now = int(time.time())
    exp = now + max(1, int(ttl_seconds))
    payload: dict[str, Any] = {
        "sub": str(supervisor_id),
        "tenant_id": str(tenant_id),
        "call_id": str(call_id),
        "session_id": str(session_id) if session_id else "",
        "mode": str(mode or "listen"),
        "scope": MONITOR_TOKEN_SCOPE,
        "iat": now,
        "exp": exp,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _signing_secret(), algorithm="HS256")


def verify_monitor_ws_token(
    token: str | None,
    *,
    expected_call_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    """Verify a monitoring WebSocket token and return its claims."""
    if not token or not str(token).strip():
        raise MonitorTokenError("Monitoring token is required")
    raw = str(token).strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    try:
        claims = jwt.decode(
            raw,
            _signing_secret(),
            algorithms=["HS256"],
            options={"require": ["sub", "tenant_id", "call_id", "scope", "exp"]},
        )
    except ExpiredSignatureError as exc:
        raise MonitorTokenExpiredError("Monitoring token has expired") from exc
    except InvalidTokenError as exc:
        raise MonitorTokenError("Monitoring token is invalid") from exc

    if claims.get("scope") != MONITOR_TOKEN_SCOPE:
        raise MonitorTokenError("Monitoring token is missing calls.monitor scope")
    if expected_call_id is not None and str(claims.get("call_id")) != str(expected_call_id):
        raise MonitorTokenError("Monitoring token call_id mismatch")
    return claims


def _extract_ws_token(websocket: WebSocket) -> str | None:
    token = websocket.query_params.get("token")
    if token:
        return token
    auth_header = websocket.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


@router.websocket("/ws/monitor/{call_id}")
async def monitor_call_ws(
    websocket: WebSocket,
    call_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Stream caller/agent PCM16 frames and transcript JSON to an authorized supervisor."""
    await websocket.accept()
    token = _extract_ws_token(websocket)
    try:
        claims = verify_monitor_ws_token(token, expected_call_id=call_id)
    except MonitorTokenExpiredError:
        await websocket.send_json({"type": "error", "code": "token_expired"})
        await websocket.close(code=1008)
        return
    except MonitorTokenError:
        await websocket.send_json({"type": "error", "code": "unauthorized"})
        await websocket.close(code=1008)
        return

    try:
        tenant_uuid = uuid.UUID(str(claims["tenant_id"]))
        supervisor_uuid = uuid.UUID(str(claims["sub"]))
    except (ValueError, TypeError):
        await websocket.send_json({"type": "error", "code": "invalid_principal"})
        await websocket.close(code=1008)
        return

    call = await session.get(Call, call_id)
    if call is None or call.tenant_id != tenant_uuid:
        await websocket.send_json({"type": "error", "code": "call_not_found"})
        await websocket.close(code=1008)
        return

    if (
        call.status in (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELLED, CallStatus.NO_ANSWER)
        and not is_takeover_pending(call)
    ):
        await websocket.send_json({"type": "error", "code": "call_ended"})
        await websocket.close(code=1008)
        return

    session_id_str = str(claims.get("session_id") or "").strip()
    mode = str(claims.get("mode") or "listen")
    if session_id_str:
        try:
            sess_uuid = uuid.UUID(session_id_str)
        except ValueError:
            await websocket.send_json({"type": "error", "code": "invalid_session"})
            await websocket.close(code=1008)
            return
        sess_row = await session.get(LiveCallSession, sess_uuid)
        if (
            sess_row is None
            or sess_row.tenant_id != tenant_uuid
            or sess_row.call_id != call_id
            or sess_row.status != "active"
        ):
            await websocket.send_json({"type": "error", "code": "session_not_active"})
            await websocket.close(code=1008)
            return
        mode = sess_row.mode or mode

    bus = get_monitor_bus()
    try:
        subscription = await bus.subscribe(
            call_id,
            supervisor_id=supervisor_uuid,
            session_id=session_id_str,
            max_subscribers_per_call=MAX_SUPERVISORS_PER_CALL,
        )
    except MonitorCapacityError:
        await websocket.send_json({"type": "error", "code": "max_supervisors_exceeded"})
        await websocket.close(code=1008)
        return

    effective_session_id = session_id_str or subscription.subscription_id
    await bus.register_session(
        call_id,
        effective_session_id,
        supervisor_id=supervisor_uuid,
        mode=mode,
        ttl_seconds=DEFAULT_WS_TOKEN_TTL_SECONDS,
    )

    await websocket.send_json(
        {
            "type": "connected",
            "call_id": str(call_id),
            "session_id": effective_session_id,
            "mode": mode,
        }
    )

    stop_event = asyncio.Event()

    async def _writer() -> None:
        try:
            while not stop_event.is_set():
                event = await subscription.get()
                if event.kind == "audio":
                    if event.pcm_bytes:
                        await websocket.send_bytes(event.pcm_bytes)
                elif event.kind == "call_ended":
                    await websocket.send_json(
                        {
                            "type": "call_ended",
                            "call_id": str(call_id),
                            "reason": event.metadata.get("reason", "completed"),
                        }
                    )
                    stop_event.set()
                    break
                else:
                    await websocket.send_json(event.to_ws_json())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug(
                "monitor_ws.writer_closed",
                call_id=str(call_id),
                error_type=type(exc).__name__,
            )
            stop_event.set()

    async def _reader() -> None:
        try:
            while not stop_event.is_set():
                msg = await websocket.receive()
                msg_type = msg.get("type")
                if msg_type == "websocket.disconnect":
                    stop_event.set()
                    break
                raw_text = msg.get("text")
                if not raw_text:
                    continue
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError:
                    continue
                cmd = str(data.get("type") or "").lower()
                if cmd in ("ping", "heartbeat"):
                    await bus.heartbeat_session(call_id, effective_session_id)
                    await websocket.send_json({"type": "pong", "call_id": str(call_id)})
                elif cmd in ("whisper", "guidance"):
                    if mode not in ("whisper_ai", "whisper", "takeover"):
                        await websocket.send_json(
                            {"type": "error", "code": "whisper_not_permitted_in_mode"}
                        )
                        continue
                    guidance_text = str(data.get("text") or "").strip()
                    if guidance_text:
                        delivered = await bus.publish_guidance(
                            call_id,
                            guidance_text,
                            supervisor_id=supervisor_uuid,
                            session_id=effective_session_id,
                            run_llm=bool(data.get("run_llm", True)),
                        )
                        await websocket.send_json(
                            {
                                "type": "whisper_ack",
                                "call_id": str(call_id),
                                "delivered": delivered,
                            }
                        )
                elif cmd in ("stop", "close"):
                    stop_event.set()
                    break
        except WebSocketDisconnect:
            stop_event.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            stop_event.set()

    writer_task = asyncio.create_task(_writer())
    reader_task = asyncio.create_task(_reader())
    try:
        await asyncio.wait(
            {writer_task, reader_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        stop_event.set()
        for t in (writer_task, reader_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(writer_task, reader_task, return_exceptions=True)
        await subscription.close()
        if not session_id_str:
            await bus.unregister_session(call_id, effective_session_id)
        try:
            await websocket.close(code=1000)
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            pass
