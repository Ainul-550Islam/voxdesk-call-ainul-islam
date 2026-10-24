"""Telnyx Call Control v2 webhook and bidirectional AI media streaming.

Endpoints:

``POST /telephony/telnyx/voice``
    Telnyx Call Control / TeXML webhook. Verifies the Ed25519 signature through
    :mod:`app.telephony.providers.webhook_verifier`, resolves the bound
    ``PhoneNumber`` (``inbound_agent_id``) and ``RuntimeConfig``, creates the
    ``Call`` row keyed by ``call_control_id``, and returns the media-stream
    descriptor including a single-use stream token and the effective media
    capability.

``WS /telephony/telnyx/ws`` and ``WS /telephony/telnyx/stream/{call_id}``
    The real AI media path. The handshake is owned by
    :class:`app.telephony.providers.telnyx_media.TelnyxMediaStream` (single-use
    token bound to the call, ``start`` validation, media-format validation),
    then the socket is handed to the *same* Pipecat pipeline the Twilio path
    uses: ``run_voice_agent(..., carrier_provider="telnyx")``. That pipeline
    carries ``LatencyObserver``, the voicemail detector, DTMF handling, the
    transfer tools and the usage tracker -- there is exactly one voice pipeline
    in this repository, and Telnyx is a transport into it, not a second one.

Call-control commands (``answer``, ``transfer``, ``hangup``, ``send_dtmf``) are
executed through :class:`app.telephony.providers.telnyx.TelnyxAdapter`, which
retries 5xx/timeout with a stable ``Idempotency-Key`` and never retries a 4xx.

Honesty rules:

* The media path refuses to start when ``supports_ai_media_stream`` is false
  (missing API key, public key or connection id). It sends
  ``{"event":"error","code":"UNSUPPORTED_CAPABILITY"}`` and closes; it does not
  answer a real caller with silence.
* A rejected signature is a 403; a rejected token closes the socket. Neither
  path logs the credential.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Call, CallDirection, CallStatus, Tenant
from app.db.session import get_session
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony.media.serializers import build_serializer
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.provider_errors import TelephonyError, UnsupportedCapability
from app.telephony.providers import factory as provider_factory
from app.telephony.providers.telnyx import TelnyxAdapter
from app.telephony.providers.telnyx_media import (
    TelnyxMediaStream,
    TelnyxMediaStreamError,
    issue_stream_token,
    verify_webhook_signature,
)

log = structlog.get_logger()

router = APIRouter(prefix="/telephony/telnyx", tags=["telnyx-voice"])

#: How long the media socket may wait for ``start`` before it is closed.
_HANDSHAKE_EVENTS = ("connected", "start")


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


async def _verify_telnyx_signature_if_configured(request: Request, raw_body: bytes) -> None:
    """Reject a forged Telnyx webhook once a public key is configured."""
    public_key = (settings.telnyx_public_key or "").strip()
    if not public_key:
        return
    signature = request.headers.get("telnyx-signature-ed25519", "")
    timestamp = request.headers.get("telnyx-timestamp", "")
    if not signature or not timestamp:
        raise HTTPException(
            status_code=403,
            detail={"code": "signature_missing", "message": "Telnyx signature headers are required"},
        )
    verified, reason = await verify_webhook_signature(
        public_key=public_key,
        signature=signature,
        timestamp=timestamp,
        body=raw_body,
        path=request.url.path,
    )
    if not verified:
        log.warning("telnyx.webhook.signature_rejected", reason=reason, path=request.url.path)
        raise HTTPException(
            status_code=403,
            detail={"code": "signature_rejected", "message": "Invalid Telnyx webhook signature"},
        )


def _stream_urls(host: str, call_id: uuid.UUID) -> tuple[str, str]:
    """The media WebSocket URL and the token that authorizes exactly one use."""
    token = issue_stream_token(str(call_id))
    url = f"wss://{host}/telephony/telnyx/stream/{call_id}?token={token}"
    return url, token


@router.post("/voice")
async def telnyx_inbound_voice(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Handle an inbound Telnyx Call Control v2 ``call.initiated`` webhook."""
    raw_body = await request.body()
    await _verify_telnyx_signature_if_configured(request, raw_body)

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (ValueError, UnicodeDecodeError):
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

    # The effective capability is resolved per tenant: deployment credentials
    # AND the tenant's provider binding both have to be present.
    capability = await provider_factory.resolve_media_capability(session, tenant.id, "telnyx")

    host = request.headers.get("host") or settings.public_host or "localhost:8000"
    stream_ws_url, stream_token = _stream_urls(host, call.id)
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
        media_capability=capability["state"],
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
        "stream_token": stream_token,
        "serializer": "TelnyxFrameSerializer",
        "media_stream": capability,
        "texml": texml,
    }


async def _read_handshake(websocket: WebSocket) -> dict[str, Any]:
    """Read frames up to and including ``start``. Returns the start payload."""
    timeout = float(settings.telnyx_media_handshake_timeout_seconds or 0.0)

    async def _loop() -> dict[str, Any]:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError as exc:
                raise TelnyxMediaStreamError("media handshake frame is not JSON") from exc
            name = str(message.get("event") or "").strip().lower()
            if name == "connected":
                continue
            if name == "start":
                return message
            if name in {"stop", "error"}:
                raise TelnyxMediaStreamError(
                    f"carrier ended the handshake with {name!r}", code="stream_aborted"
                )

    return await (asyncio.wait_for(_loop(), timeout) if timeout > 0 else _loop())


async def _close_with_error(websocket: WebSocket, code: str, message: str, *, close_code: int = 1011) -> None:
    """Tell the carrier why the stream will not run, then close. Never silent."""
    try:
        await websocket.send_text(json.dumps({"event": "error", "code": code, "message": message}))
    except Exception:  # noqa: BLE001 - the socket may already be gone
        log.info("telnyx.stream.error_frame_undeliverable", code=code)
    try:
        await websocket.close(code=close_code)
    except Exception:  # noqa: BLE001 - closing twice is not an error worth raising
        log.info("telnyx.stream.close_skipped", code=code)


async def _resolve_call_for_stream(
    session: AsyncSession, *, call_id: str, call_control_id: str
) -> tuple[Call, Tenant] | None:
    """Find the call this stream belongs to, by internal id or carrier id."""
    call: Call | None = None
    try:
        call = await session.get(Call, uuid.UUID(call_id))
    except (TypeError, ValueError):
        call = None
    if call is None and call_control_id:
        call = (
            await session.execute(select(Call).where(Call.call_sid == call_control_id))
        ).scalars().first()
    if call is None:
        return None
    tenant = await session.get(Tenant, call.tenant_id)
    if tenant is None:
        return None
    return call, tenant


async def _handle_telnyx_ws_session(websocket: WebSocket, call_id: str) -> None:
    """Authorize, bind the agent, then hand the socket to the one voice pipeline."""
    from app.core.graceful_shutdown import is_draining, track_active_call
    from app.db.session import get_sessionmaker

    await websocket.accept()

    if is_draining():
        log.warning("telnyx.stream.rejected_node_draining", call_id=call_id)
        await _close_with_error(websocket, "node_draining", "The node is draining; retry another instance.", close_code=1012)
        return

    token = websocket.query_params.get("token")
    media = TelnyxMediaStream(call_id=call_id, stream_auth_token=token)
    if not media.authorize(token):
        log.warning("telnyx.stream.rejected_bad_token", call_id=call_id)
        await _close_with_error(websocket, "stream_unauthorized", "Stream token missing, expired or already used.", close_code=1008)
        return

    try:
        start_message = await _read_handshake(websocket)
        start_event = media.handle(start_message)
    except TelnyxMediaStreamError as exc:
        log.warning("telnyx.stream.bad_handshake", call_id=call_id, error=exc.message)
        await _close_with_error(websocket, exc.code, exc.message, close_code=1008)
        return
    except (WebSocketDisconnect, asyncio.TimeoutError):
        log.warning("telnyx.stream.handshake_incomplete", call_id=call_id)
        return

    stream_id = start_event.stream_id or f"telnyx_stream_{call_id[:12]}"
    call_control_id = start_event.call_control_id
    log.info(
        "telnyx.stream.started",
        call_id=call_id,
        stream_id=stream_id,
        call_control_id=call_control_id,
        encoding=start_event.encoding,
        sample_rate=start_event.sample_rate,
    )

    adapter = TelnyxAdapter()
    async with get_sessionmaker()() as session:
        resolved = await _resolve_call_for_stream(
            session, call_id=call_id, call_control_id=call_control_id
        )
        if resolved is None:
            log.warning("telnyx.stream.unknown_call", call_id=call_id, call_control_id=call_control_id)
            await _close_with_error(websocket, "call_unknown", "No call is bound to this stream.", close_code=1008)
            return
        call, tenant = resolved

        try:
            capability = await provider_factory.require_media_capability(session, tenant.id, "telnyx")
        except UnsupportedCapability as exc:
            log.warning(
                "telnyx.stream.capability_absent",
                call_id=str(call.id),
                tenant_id=str(tenant.id),
                reason=exc.message,
            )
            await _close_with_error(websocket, "UNSUPPORTED_CAPABILITY", exc.message)
            return

        # Answer a still-ringing call through Call Control v2. A failure here is
        # real and is surfaced: the pipeline is not started for a call the
        # carrier did not answer.
        if call.status in {CallStatus.RINGING, CallStatus.QUEUED} and call_control_id:
            try:
                await adapter.answer(call_control_id)
            except TelephonyError as exc:
                log.error("telnyx.stream.answer_failed", call_id=str(call.id), error=exc.message)
                await _close_with_error(websocket, exc.code, "Call Control answer failed.")
                return

        runtime_cfg = await resolve_runtime_config(session, call, tenant=tenant)
        from app.agent.pipeline import run_voice_agent

        from app.core import metrics

        metrics.ACTIVE_CALLS.inc()
        try:
            with track_active_call(call.call_sid):
                await run_voice_agent(
                    websocket=websocket,
                    stream_sid=stream_id,
                    call_sid=call_control_id or call.call_sid,
                    session=session,
                    tenant=tenant,
                    call=call,
                    runtime_config=runtime_cfg,
                    carrier_provider="telnyx",
                )
        except Exception as exc:  # noqa: BLE001 - a crashed stream must be recorded
            log.error(
                "telnyx.stream.crashed",
                call_id=str(call.id),
                tenant_id=str(tenant.id),
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
            await session.refresh(call)
            if call.status not in {CallStatus.COMPLETED, CallStatus.FAILED}:
                call.status = CallStatus.FAILED
                await session.commit()
        finally:
            metrics.ACTIVE_CALLS.dec()
            log.info(
                "telnyx.stream.finished",
                call_id=str(call.id),
                media=media.state.as_dict(),
                capability=capability["state"],
            )


@router.websocket("/stream/{call_id}")
async def telnyx_media_stream(websocket: WebSocket, call_id: str) -> None:
    """Bidirectional Telnyx media stream for one call, bound via the phone number."""
    await _handle_telnyx_ws_session(websocket, call_id)


@router.websocket("/ws")
async def telnyx_ws_root(websocket: WebSocket) -> None:
    """Alias WebSocket for Telnyx TeXML ``<Stream>`` connections.

    The call id travels in the query string (``?call_id=...``) because the
    handshake frame is not available before the socket is accepted; when it is
    absent the ``start`` frame's ``call_control_id`` is used to find the call.
    """
    call_id = websocket.query_params.get("call_id") or "unknown"
    await _handle_telnyx_ws_session(websocket, call_id)


# --------------------------------------------------------------------------- #
# Call Control v2 helpers, used by the control-plane routes and by tests
# --------------------------------------------------------------------------- #


async def answer_call(call_control_id: str) -> dict[str, Any]:
    """Answer a ringing Telnyx call. Raises a typed error, never a silent no-op."""
    result = await TelnyxAdapter().answer(call_control_id)
    log.info("telnyx.call_control.answer", call_control_id=call_control_id, status=result.status)
    return {"provider": result.provider, "call_control_id": result.external_id, "status": result.status}


async def transfer_call(call_control_id: str, destination: str) -> dict[str, Any]:
    """Warm-transfer a live Telnyx call to ``destination`` (E.164)."""
    result = await TelnyxAdapter().transfer_call(call_control_id, destination)
    log.info(
        "telnyx.call_control.transfer",
        call_control_id=call_control_id,
        destination=result.to_number,
        status=result.status,
    )
    return {
        "provider": result.provider,
        "call_control_id": result.external_id,
        "destination": result.to_number,
        "status": result.status,
    }


async def hangup_call(call_control_id: str) -> dict[str, Any]:
    """Hang up a live Telnyx call."""
    result = await TelnyxAdapter().hangup_call(call_control_id)
    log.info("telnyx.call_control.hangup", call_control_id=call_control_id, status=result.status)
    return {"provider": result.provider, "call_control_id": result.external_id, "status": result.status}


async def send_dtmf(call_control_id: str, digits: str) -> dict[str, Any]:
    """Send DTMF digits on a live Telnyx call."""
    result = await TelnyxAdapter().send_dtmf(call_control_id, digits)
    ledger_digits = "*" * len(digits or "")
    log.info(
        "telnyx.call_control.dtmf",
        call_control_id=call_control_id,
        digits=ledger_digits,
        status=result.status,
    )
    return {"provider": result.provider, "call_control_id": result.external_id, "status": result.status}


def build_media_serializer(*, stream_id: str, call_control_id: str, encoding: str = "PCMU"):
    """The Pipecat serializer used by the pipeline for a Telnyx stream."""
    return build_serializer(
        "telnyx",
        stream_sid=stream_id,
        call_sid=call_control_id,
        inbound_encoding=encoding,
        outbound_encoding=encoding,
        sample_rate=settings.telnyx_media_sample_rate,
        auto_hang_up=False,
    )
