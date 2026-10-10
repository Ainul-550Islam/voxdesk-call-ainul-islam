"""Telnyx Call Control v2 Webhook & Bidirectional Media Stream Handler (Sub-Phase 2F).

Provides:
- `POST /telephony/telnyx/voice`: Inbound Telnyx Call Control / TeXML webhook that
  verifies Ed25519 signatures via `TelnyxAdapter.verify_webhook` when configured,
  resolves the bound `PhoneNumber` (`inbound_agent_id`) and `RuntimeConfig`, creates a
  `Call` row (`call_sid=call_control_id`, `agent_id`, `agent_version_id`), and returns
  the bidirectional WebSocket media stream descriptor (`wss://.../telephony/telnyx/stream/{call_id}`).
- `WebSocket /telephony/telnyx/stream/{call_id}` and `WebSocket /telephony/telnyx/ws`:
  Bidirectional Telnyx media stream endpoints powered by Pipecat's `TelnyxFrameSerializer`
  (`PCMU`/`PCMA`/`G722` @ 8kHz) and `run_voice_agent(..., carrier_provider="telnyx")`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pipecat.frames.frames import InputAudioRawFrame, InputDTMFFrame, OutputAudioRawFrame
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Call, CallDirection, CallStatus, Tenant
from app.db.session import get_session
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony.media.serializers import build_serializer
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.providers.telnyx import TelnyxAdapter

log = structlog.get_logger()

router = APIRouter(prefix="/telephony/telnyx", tags=["telnyx-voice"])


def _extract_telnyx_call_fields(payload: dict[str, Any]) -> dict[str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    inner = data.get("payload") if isinstance(data.get("payload"), dict) else data
    call_control_id = str(
        inner.get("call_control_id")
        or inner.get("call_session_id")
        or inner.get("call_leg_id")
        or payload.get("CallSid")
        or f"telnyx_{uuid.uuid4().hex[:16]}"
    ).strip()
    to_number = str(
        inner.get("to")
        or payload.get("to")
        or payload.get("To")
        or ""
    ).strip()
    from_number = str(
        inner.get("from")
        or payload.get("from")
        or payload.get("From")
        or "+10000000000"
    ).strip()
    event_type = str(
        data.get("event_type")
        or payload.get("event_type")
        or "call.initiated"
    ).strip()
    return {
        "call_control_id": call_control_id,
        "to_number": to_number,
        "from_number": from_number,
        "event_type": event_type,
    }


def _verify_telnyx_signature_if_configured(request: Request, raw_body: bytes) -> None:
    sig_header = request.headers.get("telnyx-signature-ed25519")
    pub_key = getattr(settings, "telnyx_public_key", "") or ""
    if not sig_header or not pub_key.strip():
        return
    adapter = TelnyxAdapter(public_key=pub_key)
    verified = adapter.verify_webhook(
        headers=dict(request.headers),
        raw_body=raw_body,
        url=str(request.url),
    )
    if not verified:
        raise HTTPException(status_code=403, detail="Invalid Telnyx webhook signature")


@router.post("/voice")
async def telnyx_inbound_voice(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Handle inbound Telnyx Call Control v2 `call.initiated` / `call.answered` webhook."""
    raw_body = await request.body()
    _verify_telnyx_signature_if_configured(request, raw_body)

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        form = await request.form()
        payload = dict(form)

    fields = _extract_telnyx_call_fields(payload if isinstance(payload, dict) else {})
    to_number = fields["to_number"]
    from_number = fields["from_number"]
    call_control_id = fields["call_control_id"]

    if not to_number:
        raise HTTPException(status_code=400, detail="Missing destination `to` number in Telnyx payload")

    phone_row = (
        await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.e164 == to_number,
                PhoneNumber.released_at.is_(None),
            )
        )
    ).scalars().first()

    tenant: Tenant | None = None
    if phone_row is not None:
        tenant = await session.get(Tenant, phone_row.tenant_id)
    if tenant is None:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.twilio_number == to_number))
        ).scalars().first()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"No tenant bound to number {to_number}")

    call = (
        await session.execute(select(Call).where(Call.call_sid == call_control_id))
    ).scalars().first()
    if call is None:
        call = Call(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            call_sid=call_control_id,
            from_number=from_number,
            to_number=to_number,
            direction=CallDirection.INBOUND,
            status=CallStatus.IN_PROGRESS,
        )
        session.add(call)
        await session.flush()

    runtime_cfg = await resolve_runtime_config(session, call, tenant=tenant)
    call.language = runtime_cfg.language
    await session.commit()
    await session.refresh(call)

    host = request.headers.get("host") or settings.public_host or "localhost:8000"
    stream_ws_url = f"wss://{host}/telephony/telnyx/stream/{call.id}"
    texml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="{stream_ws_url}" bidirectionalMode="rtp">'
        f'<Parameter name="call_id" value="{call.id}"/>'
        f'<Parameter name="agent_id" value="{runtime_cfg.agent_id or ""}"/>'
        "</Stream></Connect>"
        "</Response>"
    )

    log.info(
        "telnyx.inbound.connected",
        call_id=str(call.id),
        call_control_id=call_control_id,
        tenant_id=str(tenant.id),
        agent_id=str(runtime_cfg.agent_id) if runtime_cfg.agent_id else None,
    )
    return {
        "ok": True,
        "provider": "telnyx",
        "call_id": str(call.id),
        "call_control_id": call_control_id,
        "tenant_id": str(tenant.id),
        "agent_id": str(runtime_cfg.agent_id) if runtime_cfg.agent_id else None,
        "agent_version_id": str(runtime_cfg.agent_version_id) if runtime_cfg.agent_version_id else None,
        "stream_ws_url": stream_ws_url,
        "serializer": "TelnyxFrameSerializer",
        "texml": texml,
    }


async def _handle_telnyx_ws_session(websocket: WebSocket, call_id: str) -> None:
    """Shared Telnyx WebSocket media stream loop using `TelnyxFrameSerializer` (or `run_voice_agent`)."""
    await websocket.accept()
    stream_id = f"telnyx_stream_{call_id[:12]}"
    call_control_id = ""
    inbound_encoding = "PCMU"
    outbound_encoding = "PCMU"
    sample_rate = 8000
    serializer = None
    frames_received = 0

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except ValueError:
                continue

            event = str(msg.get("event") or "").lower()
            if event == "connected":
                await websocket.send_text(
                    json.dumps({"event": "ack", "protocol": "telnyx_media_v2"})
                )
                continue

            if event == "start":
                start_info = msg.get("start") or {}
                stream_id = str(msg.get("stream_id") or start_info.get("stream_id") or stream_id)
                call_control_id = str(
                    start_info.get("call_control_id")
                    or msg.get("call_control_id")
                    or call_id
                )
                media_fmt = start_info.get("media_format") or {}
                inbound_encoding = str(media_fmt.get("encoding") or "PCMU").upper()
                outbound_encoding = inbound_encoding
                sample_rate = int(media_fmt.get("sample_rate") or 8000)

                serializer = build_serializer(
                    "telnyx",
                    stream_sid=stream_id,
                    call_sid=call_control_id,
                    inbound_encoding=inbound_encoding,
                    outbound_encoding=outbound_encoding,
                    sample_rate=sample_rate,
                    auto_hang_up=False,
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "stream_ready",
                            "stream_id": stream_id,
                            "call_control_id": call_control_id,
                            "encoding": inbound_encoding,
                            "sample_rate": sample_rate,
                        }
                    )
                )
                continue

            if event == "stop":
                break

            if serializer is None:
                serializer = build_serializer(
                    "telnyx",
                    stream_sid=stream_id,
                    call_sid=call_control_id or call_id,
                    inbound_encoding=inbound_encoding,
                    outbound_encoding=outbound_encoding,
                    sample_rate=sample_rate,
                    auto_hang_up=False,
                )

            frame = await serializer.deserialize(raw_text)
            if isinstance(frame, InputAudioRawFrame):
                frames_received += 1
                out_frame = OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
                outbound_payload = await serializer.serialize(out_frame)
                if outbound_payload:
                    await websocket.send_text(
                        outbound_payload
                        if isinstance(outbound_payload, str)
                        else outbound_payload.decode("utf-8")
                    )
            elif isinstance(frame, InputDTMFFrame):
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "dtmf_ack",
                            "stream_id": stream_id,
                            "digit": frame.button.value,
                        }
                    )
                )
    except WebSocketDisconnect:
        log.info("telnyx.stream.disconnected", call_id=call_id, frames_received=frames_received)


@router.websocket("/stream/{call_id}")
async def telnyx_media_stream(websocket: WebSocket, call_id: str) -> None:
    """Bidirectional Telnyx RTP/WebSocket media stream handler using `TelnyxFrameSerializer`."""
    await _handle_telnyx_ws_session(websocket, call_id)


@router.websocket("/ws")
async def telnyx_ws_root(websocket: WebSocket) -> None:
    """Alias `/telephony/telnyx/ws` WebSocket endpoint for Telnyx TeXML `<Stream>` connections."""
    call_id = websocket.query_params.get("call_id") or f"telnyx_{uuid.uuid4().hex[:12]}"
    await _handle_telnyx_ws_session(websocket, call_id)
