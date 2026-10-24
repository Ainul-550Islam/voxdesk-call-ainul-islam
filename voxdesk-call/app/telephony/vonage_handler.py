"""Vonage Voice API media streaming.

Three surfaces:

* ``GET|POST /telephony/vonage/answer`` -- Vonage's answer URL. It answers
  with an NCCO that opens a bidirectional WebSocket back to
  ``/telephony/vonage/ws`` carrying L16 audio at 16 kHz.
* ``POST /telephony/vonage/events`` -- Vonage's event webhook. The
  signature (JWT or HMAC) is verified before anything is read from the body.
* ``WS /telephony/vonage/ws`` -- the media socket. Binary frames are 16-bit
  linear PCM at 16 kHz; text frames are Vonage's JSON control events.

The pipeline is the same one every other carrier uses: the number is
resolved to a tenant, the tenant's bound agent is resolved through the
existing runtime resolver, and the Pipecat pipeline (latency observer,
DTMF, voicemail detection, transfer tools) runs unchanged. The only
difference is the transport, because Vonage mixes binary audio and JSON
control events on one socket.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketCallbacks,
    FastAPIWebsocketClient,
    FastAPIWebsocketInputTransport,
    FastAPIWebsocketOutputTransport,
    FastAPIWebsocketTransport,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.graceful_shutdown import is_draining, track_active_call
from app.core.logging import log
from app.core.metrics import ACTIVE_CALLS
from app.db.models import Call, CallDirection, CallStatus, Tenant
from app.db.session import get_session, get_sessionmaker
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony import call_state
from app.telephony.number_provisioning import find_tenant_for_called_number
from app.telephony.providers.vonage import VonageAdapter
from app.telephony.providers.vonage_media import (
    PIPELINE_SAMPLE_RATE,
    VONAGE_SAMPLE_RATE,
    VonageFrameSerializer,
)

router = APIRouter(prefix="/telephony/vonage", tags=["vonage-voice"])

#: Prefix that separates a Vonage media token from every other token the
#: product mints. A token minted for one carrier can never open another
#: carrier's socket.
_TOKEN_PREFIX = "voxdesk-vonage-stream-v1"
#: Vonage may open the socket seconds after the answer URL was served.
_TOKEN_TTL_SECONDS = 300


def _sign(payload: str) -> str:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"{_TOKEN_PREFIX}:{payload}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_vonage_stream_token(call_sid: str, *, issued_at: int | None = None) -> str:
    """Mint a short-lived token bound to one Vonage call."""
    issued = int(issued_at if issued_at is not None else time.time())
    return f"{issued}.{_sign(f'{call_sid}:{issued}')}"


def verify_vonage_stream_token(call_sid: str, token: str | None, *, now: int | None = None) -> bool:
    """Constant-time verification of a Vonage media token."""
    if not token or not call_sid:
        return False
    issued_raw, _, signature = token.partition(".")
    if not signature:
        return False
    try:
        issued = int(issued_raw)
    except ValueError:
        return False
    current = int(now if now is not None else time.time())
    if not (-5 <= current - issued <= _TOKEN_TTL_SECONDS):
        return False
    return hmac.compare_digest(_sign(f"{call_sid}:{issued}"), signature)


def _not_configured() -> HTTPException:
    """501: the deployment cannot stream Vonage media."""
    return HTTPException(
        status_code=501,
        detail={
            "code": "unsupported_capability",
            "message": (
                "Vonage AI media streaming is not enabled on this deployment: the "
                "Vonage credential set is incomplete."
            ),
        },
    )


def _carrier_settings():
    """The Batch 02 carrier settings (used by the capability checks)."""
    from app.carriers_v2.settings import get_carrier_v2_settings

    return get_carrier_v2_settings()


# ----------------------------------------------------------------------
# Transport
# ----------------------------------------------------------------------
class _VonageWebsocketClient(FastAPIWebsocketClient):
    """A WebSocket client that speaks Vonage's mixed frame protocol.

    Vonage sends audio as **binary** frames and control events as **text**
    frames on the same socket, so the stock client (which is either binary
    or text, never both) cannot carry the protocol. This client yields
    whichever arrives and sends binary payloads as binary frames.
    """

    def __init__(self, websocket, callbacks: FastAPIWebsocketCallbacks) -> None:
        super().__init__(websocket, False, callbacks)

    async def receive(self):
        while True:
            message = await self._websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(int(message.get("code") or 1000))
            text = message.get("text")
            if text is not None:
                yield text
                continue
            data = message.get("bytes")
            if data is not None:
                yield data

    async def send(self, data: str | bytes) -> None:
        if not self.can_send:
            return
        try:
            if isinstance(data, (bytes, bytearray)):
                await self._websocket.send_bytes(bytes(data))
            else:
                await self._websocket.send_text(str(data))
        except (RuntimeError, WebSocketDisconnect) as exc:
            log.debug("vonage.ws.send_failed", error=str(exc))

    @property
    def can_send(self) -> bool:
        return self.is_connected and not self.is_closing


class VonageWebsocketTransport(FastAPIWebsocketTransport):
    """Pipecat transport that speaks the Vonage WebSocket media protocol."""

    def __init__(self, websocket, params) -> None:
        super().__init__(websocket, params)
        client = _VonageWebsocketClient(websocket, self._callbacks)
        self._client = client
        self._input = FastAPIWebsocketInputTransport(
            self, client, self._params, name=self._input_name
        )
        self._output = FastAPIWebsocketOutputTransport(
            self, client, self._params, name=self._output_name
        )


# ----------------------------------------------------------------------
# Answer URL
# ----------------------------------------------------------------------
def _build_ncco(ws_url: str, token: str, event_url: str) -> list[dict]:
    """The NCCO that connects Vonage to the VoxDesk media socket."""
    return [
        {
            "action": "connect",
            "type": "websocket",
            "uri": ws_url,
            "contentType": f"audio/l16;rate={VONAGE_SAMPLE_RATE}",
            "headers": {"X-VoxDesk-Vonage-Token": token},
            "eventUrl": [event_url],
            "eventMethod": "POST",
        }
    ]


@router.get("/answer")
@router.post("/answer")
async def vonage_answer(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Answer an inbound Vonage call with a media-streaming NCCO."""
    if is_draining():
        raise HTTPException(
            status_code=503,
            detail={"code": "draining", "message": "Node is draining"},
            headers={"Retry-After": "5"},
        )

    adapter = VonageAdapter()
    if not adapter.supports_ai_media_stream:
        raise _not_configured()

    params = request.query_params
    to_number = str(params.get("to") or "").strip()
    from_number = str(params.get("from") or "").strip()
    call_ref = str(params.get("uuid") or "").strip() or f"vonage_{uuid.uuid4().hex[:16]}"
    conversation = str(params.get("conversation_uuid") or "").strip()

    if not to_number:
        raise HTTPException(
            status_code=400, detail={"code": "validation", "message": "Missing `to` number"}
        )

    tenant = await find_tenant_for_called_number(session, to_number)
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Number not configured"}
        )

    call = (
        await session.execute(select(Call).where(Call.call_sid == call_ref))
    ).scalar_one_or_none()
    if call is None:
        call = Call(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            call_sid=call_ref,
            from_number=from_number or "+10000000000",
            to_number=to_number,
            direction=CallDirection.INBOUND,
            status=CallStatus.IN_PROGRESS,
        )
        session.add(call)
        try:
            await session.flush()
            await resolve_runtime_config(session, call, tenant=tenant)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            call = (
                await session.execute(select(Call).where(Call.call_sid == call_ref))
            ).scalar_one_or_none()
            if call is None:
                raise
    else:
        await session.commit()

    token = create_vonage_stream_token(call_ref)
    ws_url = (
        f"{settings.ws_base_url}/telephony/vonage/ws"
        f"?call_id={call_ref}"
        f"&token={token}"
    )
    event_url = f"{settings.public_base_url.rstrip('/')}/telephony/vonage/events"
    ncco = _build_ncco(ws_url, token, event_url)

    log.info(
        "vonage.inbound.answered",
        tenant_id=str(tenant.id),
        call_sid=call_ref,
        conversation_uuid=conversation or None,
    )
    return {
        "ncco": ncco,
        "conversation_uuid": conversation,
        "uuid": call_ref,
    }


# ----------------------------------------------------------------------
# Event webhook
# ----------------------------------------------------------------------
@router.post("/events")
async def vonage_events(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """Handle a signed Vonage call event."""
    from app.telephony.providers.webhook_verifier import verify as verify_provider_webhook

    verdict = await verify_provider_webhook("vonage", request)
    if not verdict.valid:
        raise HTTPException(
            status_code=403,
            detail={"code": "signature_rejected", "message": "Invalid Vonage signature"},
        )

    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"code": "validation", "message": "Malformed event body"}
        ) from None
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail={"code": "validation", "message": "Malformed event body"}
        )

    call_ref = str(payload.get("uuid") or payload.get("CallSid") or "").strip()
    if not call_ref:
        return {"ok": True, "applied": False}

    call = (
        await session.execute(select(Call).where(Call.call_sid == call_ref))
    ).scalar_one_or_none()
    if call is None:
        log.info("vonage.event.unknown_call", call_sid=call_ref)
        return {"ok": True, "applied": False}

    status = str(payload.get("status") or payload.get("CallStatus") or "").strip()
    duration_raw = payload.get("duration") or payload.get("duration_seconds") or 0
    try:
        duration = float(duration_raw or 0)
    except (TypeError, ValueError):
        duration = 0.0

    result = call_state.apply_provider_status(
        call, status, duration_seconds=duration, source="vonage_event"
    )
    await call_state.publish_transition(session, call, result)
    await session.commit()
    log.info(
        "vonage.event.applied",
        call_sid=call_ref,
        status=status or None,
        applied=result.applied,
    )
    return {"ok": True, "applied": bool(result.applied)}


# ----------------------------------------------------------------------
# Media socket
# ----------------------------------------------------------------------
@router.websocket("/ws")
async def vonage_media_stream(websocket: WebSocket) -> None:
    """The Vonage bidirectional media socket."""
    if is_draining():
        await websocket.accept()
        await websocket.close(code=1012)
        return

    await websocket.accept()

    call_ref = (websocket.query_params.get("call_id") or "").strip()
    token = websocket.query_params.get("token") or websocket.headers.get(
        "x-voxdesk-vonage-token"
    )
    if not call_ref or not verify_vonage_stream_token(call_ref, token):
        log.warning("vonage.ws.rejected_bad_token")
        await websocket.close(code=1008)
        return

    from app.agent.pipeline import build_voice_transport_params, run_voice_agent

    async with get_sessionmaker()() as session:
        call = (
            await session.execute(select(Call).where(Call.call_sid == call_ref))
        ).scalar_one_or_none()
        if call is None:
            await websocket.close(code=1000)
            return
        tenant = await session.get(Tenant, call.tenant_id)
        if tenant is None:
            await websocket.close(code=1000)
            return

        control_events: list[dict] = []

        async def _on_control(message: dict) -> None:
            control_events.append(message)
            log.info(
                "vonage.ws.control",
                call_sid=call_ref,
                event=str(message.get("event") or "")[:32],
            )

        serializer = VonageFrameSerializer(
            stream_sid=call_ref,
            call_sid=call_ref,
            sample_rate=PIPELINE_SAMPLE_RATE,
            outbound_sample_rate=VONAGE_SAMPLE_RATE,
            control_handler=_on_control,
        )
        runtime_cfg = await resolve_runtime_config(session, call, tenant=tenant)
        params = build_voice_transport_params(runtime_cfg, serializer)
        transport = VonageWebsocketTransport(websocket, params)

        ACTIVE_CALLS.inc()
        try:
            with track_active_call(call_ref):
                await run_voice_agent(
                    websocket=websocket,
                    stream_sid=call_ref,
                    call_sid=call_ref,
                    session=session,
                    tenant=tenant,
                    call=call,
                    runtime_config=runtime_cfg,
                    carrier_provider="vonage",
                    transport=transport,
                )
        except Exception as exc:
            log.error(
                "vonage.call.crashed",
                call_sid=call_ref,
                tenant_id=str(call.tenant_id),
                error=str(exc)[:200],
            )
            await session.refresh(call)
            if call.status in (CallStatus.RINGING, CallStatus.IN_PROGRESS):
                transition = call_state.apply_status(
                    call, CallStatus.FAILED, reason="vonage media error", source="vonage_ws"
                )
                await call_state.publish_transition(session, call, transition)
                await session.commit()
        finally:
            ACTIVE_CALLS.dec()


__all__ = [
    "VonageWebsocketTransport",
    "create_vonage_stream_token",
    "router",
    "verify_vonage_stream_token",
]
