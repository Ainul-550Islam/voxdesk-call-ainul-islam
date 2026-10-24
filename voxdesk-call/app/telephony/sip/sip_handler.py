"""SIP media bridge: the WebSocket the Go SIP gateway connects to.

The gateway (``services/sip-gateway``) terminates SIP signalling, owns the
RTP sockets and bridges the audio to this socket. The payload is **G.711**
(PCMU or PCMA) rather than linear PCM, because that is what the SIP peer
negotiated, and it travels as **binary** frames: the audio is never re-encoded
and never base64 on the wire. The control events are JSON text frames.

    -> {"event": "start", "call_id": ..., "from": ..., "to": ..., "codec": "PCMU"}
    -> binary frames: the raw G.711 audio of the call
    -> {"event": "dtmf", "digit": "5"}
    <- binary frames: the raw G.711 audio the gateway plays out

Authentication is machine-to-machine. The gateway presents the shared
``SIP_GATEWAY_TOKEN`` as a bearer credential *and* a per-call session token:
an HMAC over ``"{call_id}:{timestamp}:{nonce}"`` that only the gateway and
this application can compute. The timestamp bounds the replay window.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    KeypadEntry,
    OutputAudioRawFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketCallbacks,
    FastAPIWebsocketClient,
    FastAPIWebsocketInputTransport,
    FastAPIWebsocketOutputTransport,
    FastAPIWebsocketTransport,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.graceful_shutdown import is_draining, track_active_call
from app.core.logging import log
from app.core.metrics import ACTIVE_CALLS
from app.db.models import Call, CallDirection, CallStatus
from app.db.session import get_sessionmaker
from app.telephony import call_state
from app.telephony.number_provisioning import find_tenant_for_called_number
from app.telephony.sip.sip_codec import g711_to_pcm16, pcm16_to_g711

router = APIRouter(prefix="/telephony/sip", tags=["sip-voice"])

#: How far a gateway session token may be from the server clock.
SESSION_TOKEN_MAX_SKEW_SECONDS = 120
#: The pipeline runs telephony PCM at 8 kHz.
_PIPELINE_SAMPLE_RATE = 8000


def _carrier_settings():
    from app.carriers_v2.settings import get_carrier_v2_settings

    return get_carrier_v2_settings()


def session_token(secret: str, call_id: str, timestamp: str, nonce: str) -> str:
    """The per-call session token shared by the gateway and this app."""
    material = f"{call_id}:{timestamp}:{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def verify_session(
    secret: str,
    *,
    call_id: str,
    timestamp: str,
    nonce: str,
    presented: str,
    now: int | None = None,
) -> bool:
    """Verify the gateway session token and its freshness."""
    if not secret or not call_id or not timestamp or not nonce or not presented:
        return False
    try:
        issued = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if abs(current - issued) > SESSION_TOKEN_MAX_SKEW_SECONDS:
        return False
    expected = session_token(secret, call_id, timestamp, nonce)
    return hmac.compare_digest(expected, presented.strip().lower())


class SipG711Serializer(FrameSerializer):
    """Pipecat serializer for the SIP gateway media protocol.

    The gateway carries the audio as **binary** frames -- the negotiated
    G.711 payload, exactly as it came out of the RTP stream -- and the
    control events (the start message, the in-band DTMF digits) as JSON
    text frames. The payload is never re-encoded on the way in and never
    base64 on the wire.
    """

    def __init__(self, *, stream_sid: str = "", call_sid: str = "", codec: str = "PCMU") -> None:
        super().__init__()
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.codec = (codec or "PCMU").strip().upper()
        if self.codec not in {"PCMU", "PCMA"}:
            raise ValueError(f"unsupported SIP codec: {codec!r}")

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.BINARY

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, (bytes, bytearray)):
            audio = g711_to_pcm16(bytes(data), self.codec)
            if not audio:
                return None
            return InputAudioRawFrame(
                audio=audio, sample_rate=_PIPELINE_SAMPLE_RATE, num_channels=1
            )
        if not isinstance(data, str) or not data.strip():
            return None
        try:
            message = json.loads(data)
        except ValueError:
            return None
        if not isinstance(message, dict):
            return None
        event = str(message.get("event") or message.get("type") or "").strip().lower()
        if event == "dtmf":
            digit = str(message.get("digit") or "").strip()
            if digit:
                try:
                    return InputDTMFFrame(button=KeypadEntry(digit[0]))
                except ValueError:
                    return None
        return None

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if not isinstance(frame, OutputAudioRawFrame):
            return None
        rate = int(getattr(frame, "sample_rate", _PIPELINE_SAMPLE_RATE) or _PIPELINE_SAMPLE_RATE)
        audio = frame.audio
        if rate != 8000:
            from app.telephony.sip.sip_codec import resample_pcm16

            audio = resample_pcm16(audio, source_rate=rate, target_rate=8000)
        if not audio:
            return None
        return pcm16_to_g711(audio, self.codec)


class _SipWebsocketClient(FastAPIWebsocketClient):
    """A WebSocket client that speaks the SIP gateway media protocol.

    The gateway sends the audio as **binary** frames and the control events
    as **text** frames on the same socket, so the stock client (which is
    either binary or text, never both) cannot carry the protocol.
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
            log.debug("sip.ws.send_failed", error=str(exc))

    @property
    def can_send(self) -> bool:
        return self.is_connected and not self.is_closing


class SipWebsocketTransport(FastAPIWebsocketTransport):
    """Pipecat transport that speaks the SIP gateway media protocol."""

    def __init__(self, websocket, params) -> None:
        super().__init__(websocket, params)
        client = _SipWebsocketClient(websocket, self._callbacks)
        self._client = client
        self._input = FastAPIWebsocketInputTransport(
            self, client, self._params, name=self._input_name
        )
        self._output = FastAPIWebsocketOutputTransport(
            self, client, self._params, name=self._output_name
        )


async def _safe_close(websocket, code: int) -> None:
    try:
        await websocket.close(code=code)
    except (RuntimeError, WebSocketDisconnect) as exc:
        log.debug("sip.ws.close_after_completion", error=str(exc))


@router.websocket("/ws")
async def sip_media_bridge(websocket: WebSocket) -> None:
    """The authenticated media socket the SIP gateway bridges into."""
    if is_draining():
        await websocket.accept()
        await _safe_close(websocket, 1012)
        return

    carrier = _carrier_settings()
    secret = carrier.sip_gateway_token or ""
    if not carrier.sip_gateway_ready():
        await websocket.accept()
        log.warning("sip.ws.rejected_gateway_not_configured")
        await _safe_close(websocket, 1012)
        return

    await websocket.accept()

    headers = websocket.headers
    bearer = str(headers.get("authorization") or "")
    presented_bearer = bearer.split(" ", 1)[1].strip() if bearer.lower().startswith("bearer ") else ""
    if not presented_bearer or not hmac.compare_digest(presented_bearer, secret):
        log.warning("sip.ws.rejected_bad_bearer")
        await _safe_close(websocket, 1008)
        return

    call_id = str(headers.get("x-voxdesk-sip-call-id") or "").strip()
    timestamp = str(headers.get("x-voxdesk-sip-timestamp") or "").strip()
    nonce = str(headers.get("x-voxdesk-sip-nonce") or "").strip()
    presented = str(headers.get("x-voxdesk-sip-session") or "").strip()
    if not verify_session(
        secret, call_id=call_id, timestamp=timestamp, nonce=nonce, presented=presented
    ):
        log.warning("sip.ws.rejected_bad_session_token")
        await _safe_close(websocket, 1008)
        return

    try:
        raw_start = await websocket.receive_text()
    except WebSocketDisconnect:
        return
    try:
        start = json.loads(raw_start)
    except ValueError:
        await _safe_close(websocket, 1008)
        return
    if not isinstance(start, dict) or str(start.get("type") or "").lower() != "start":
        await _safe_close(websocket, 1008)
        return

    start_call_id = str(start.get("call_id") or call_id).strip()
    if start_call_id != call_id:
        log.warning("sip.ws.rejected_call_id_mismatch")
        await _safe_close(websocket, 1008)
        return

    from_number = str(start.get("from") or "").strip() or "+10000000000"
    to_number = str(start.get("to") or "").strip()
    codec = str(start.get("codec") or "PCMU").strip().upper()
    if codec not in {"PCMU", "PCMA"}:
        await _safe_close(websocket, 1008)
        return

    from app.agent.pipeline import build_voice_transport_params, run_voice_agent
    from app.runtime.agent_config_resolver import resolve_runtime_config

    async with get_sessionmaker()() as session:
        tenant = await find_tenant_for_called_number(session, to_number)
        if tenant is None or not tenant.is_active:
            log.warning("sip.ws.unknown_destination", to=to_number[-6:])
            await _safe_close(websocket, 1008)
            return

        call = (
            await session.execute(select(Call).where(Call.call_sid == call_id))
        ).scalar_one_or_none()
        if call is None:
            call = Call(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                call_sid=call_id,
                from_number=from_number,
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
                    await session.execute(select(Call).where(Call.call_sid == call_id))
                ).scalar_one_or_none()
                if call is None:
                    await _safe_close(websocket, 1008)
                    return
        else:
            await session.commit()

        serializer = SipG711Serializer(
            stream_sid=call_id, call_sid=call_id, codec=codec
        )
        runtime_cfg = await resolve_runtime_config(session, call, tenant=tenant)
        params = build_voice_transport_params(runtime_cfg, serializer)
        transport = SipWebsocketTransport(websocket=websocket, params=params)

        log.info(
            "sip.call.bridged",
            call_sid=call_id,
            tenant_id=str(tenant.id),
            codec=codec,
        )

        ACTIVE_CALLS.inc()
        try:
            with track_active_call(call_id):
                await run_voice_agent(
                    websocket=websocket,
                    stream_sid=call_id,
                    call_sid=call_id,
                    session=session,
                    tenant=tenant,
                    call=call,
                    runtime_config=runtime_cfg,
                    carrier_provider="sip",
                    transport=transport,
                )
        except Exception as exc:
            log.error(
                "sip.call.crashed",
                call_sid=call_id,
                tenant_id=str(call.tenant_id),
                error=str(exc)[:200],
            )
            await session.refresh(call)
            if call.status in (CallStatus.RINGING, CallStatus.IN_PROGRESS):
                transition = call_state.apply_status(
                    call, CallStatus.FAILED, reason="sip media error", source="sip_ws"
                )
                await call_state.publish_transition(session, call, transition)
                await session.commit()
        finally:
            ACTIVE_CALLS.dec()


__all__ = [
    "SipG711Serializer",
    "SipWebsocketTransport",
    "router",
    "session_token",
    "verify_session",
]
