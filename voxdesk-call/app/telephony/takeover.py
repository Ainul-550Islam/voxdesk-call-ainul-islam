# File: app/telephony/takeover.py — Live supervisor call takeover via provider TwiML replacement with AI rollback
"""Supervisor live call takeover and rollback to AI (Part 4 / Gate G5).

Responsibilities:
  1. Attach ``Call.takeover_pending`` (backed by ``Call.transfer_context["takeover_pending"]``
     plus in-memory attribute) so the voice pipeline sees ``call.takeover_pending == True``
     when the media stream closes during a TwiML replacement and does NOT finalize the
     call as a hang-up.
  2. Replace the active call's TwiML with ``<Dial>`` / ``<Conference>`` bridging the
     supervisor (E.164 phone, WebRTC client, SIP URI, or Conference room) while
     preserving recording continuity.
  3. Emit state-machine events (``takeover_started``, ``takeover_completed``,
     ``takeover_failed``) and roll back cleanly to the AI agent if the provider
     update or supervisor leg fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.ext.asyncio import AsyncSession
from twilio.twiml.voice_response import Connect, Dial, VoiceResponse

from app.core.config import settings
from app.core.logging import log
from app.db.models import Call, CallStatus, Speaker, Tenant, TransferState, Turn
from app.telephony import call_state, phone
from app.telephony.provider import FakeTelephonyProvider, TelephonyProvider, get_provider, set_provider
from app.telephony.provider_errors import UnsupportedCapability
from app.telephony.providers.base import TelephonyAdapter
from app.telephony.providers.twilio import TwilioAdapter

TAKEOVER_STARTED = "takeover_started"
TAKEOVER_COMPLETED = "takeover_completed"
TAKEOVER_FAILED = "takeover_failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _get_call_takeover_pending(call: Call) -> bool:
    ctx = getattr(call, "transfer_context", None)
    if isinstance(ctx, dict) and "takeover_pending" in ctx:
        return bool(ctx.get("takeover_pending"))
    explicit = getattr(call, "_takeover_pending", None)
    if explicit is not None:
        return bool(explicit)
    return False


def _set_call_takeover_pending(call: Call, value: bool) -> None:
    flag = bool(value)
    object.__setattr__(call, "_takeover_pending", flag)
    ctx = dict(getattr(call, "transfer_context", None) or {})
    ctx["takeover_pending"] = flag
    call.transfer_context = ctx


if not isinstance(getattr(Call, "takeover_pending", None), property):
    Call.takeover_pending = property(_get_call_takeover_pending, _set_call_takeover_pending)  # type: ignore[attr-defined]


def is_takeover_pending(call: Any) -> bool:
    """Return True if ``call`` has an in-flight or active supervisor takeover."""
    if call is None:
        return False
    ctx = getattr(call, "transfer_context", None)
    if isinstance(ctx, dict) and "takeover_pending" in ctx:
        return bool(ctx.get("takeover_pending"))
    return bool(getattr(call, "takeover_pending", False))


def set_takeover_pending(
    call: Any,
    pending: bool,
    *,
    destination: str = "",
    reason: str = "",
) -> None:
    """Set ``takeover_pending`` on ``call`` and persist in ``transfer_context``."""
    flag = bool(pending)
    ctx = dict(getattr(call, "transfer_context", None) or {})
    ctx["takeover_pending"] = flag
    if destination:
        ctx["supervisor_destination"] = destination
    if reason:
        ctx["takeover_reason"] = reason
    try:
        call.transfer_context = ctx
    except Exception:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        pass
    try:
        call.takeover_pending = flag
    except Exception:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        object.__setattr__(call, "_takeover_pending", flag)


def normalize_takeover_destination(
    supervisor_destination: str | None = None,
    *,
    webrtc_client: str | None = None,
    conference_name: str | None = None,
) -> str:
    """Normalize a supervisor phone number, WebRTC client id, SIP URI, or conference room."""
    if webrtc_client and str(webrtc_client).strip():
        raw_client = str(webrtc_client).strip()
        if raw_client.startswith(("client:", "webrtc:")):
            ident = raw_client.split(":", 1)[1].strip()
        else:
            ident = raw_client
        if not ident:
            raise ValueError("webrtc_client identifier must not be empty")
        return f"client:{ident}"

    raw = (supervisor_destination or "").strip()
    if not raw and conference_name and str(conference_name).strip():
        return f"conference:{str(conference_name).strip()}"
    if not raw:
        raise ValueError("supervisor_destination or webrtc_client is required for takeover")

    lower = raw.lower()
    if lower.startswith(("client:", "webrtc:")):
        ident = raw.split(":", 1)[1].strip()
        if not ident:
            raise ValueError("WebRTC client identifier must not be empty")
        return f"client:{ident}"
    if lower.startswith("conference:"):
        room = raw.split(":", 1)[1].strip()
        if not room:
            raise ValueError("Conference room name must not be empty")
        return f"conference:{room}"
    if lower.startswith(("sip:", "sips:")):
        return raw
    try:
        return phone.normalize(raw)
    except Exception as exc:
        raise ValueError(f"Invalid supervisor takeover destination: {raw!r}") from exc


def takeover_action_url(call_id: str = "") -> str:
    base = str(settings.public_base_url or "https://localhost").rstrip("/")
    suffix = f"?call_id={call_id}" if call_id else ""
    return f"{base}/telephony/transfer-status{suffix}"


def build_takeover_twiml(
    destination: str,
    *,
    whisper_text: str | None = None,
    record: bool = True,
    conference_name: str | None = None,
    caller_id: str | None = None,
    timeout: int = 25,
    call_id: str = "",
) -> str:
    """Construct provider TwiML (`<Dial>` / `<Conference>`) for supervisor takeover."""
    response = VoiceResponse()
    if whisper_text and whisper_text.strip():
        response.say(xml_escape(whisper_text.strip()[:400]), voice="Polly.Joanna")

    dial_kwargs: dict[str, Any] = {
        "timeout": max(5, int(timeout)),
        "answer_on_bridge": True,
        "record": "record-from-answer-dual" if record else "do-not-record",
        "action": takeover_action_url(call_id),
        "method": "POST",
    }
    if caller_id:
        dial_kwargs["caller_id"] = caller_id

    dial = Dial(**dial_kwargs)
    if conference_name or destination.startswith("conference:"):
        room = (
            conference_name
            or destination.split(":", 1)[1].strip()
        )
        dial.conference(
            room,
            start_conference_on_enter=True,
            end_conference_on_exit=False,
            record="record-from-start" if record else "do-not-record",
        )
    elif destination.startswith(("client:", "webrtc:")):
        client_id = destination.split(":", 1)[1].strip()
        dial.client(client_id)
    elif destination.startswith(("sip:", "sips:")):
        dial.sip(destination)
    else:
        dial.number(destination)

    response.append(dial)
    return str(response)


def build_ai_rollback_twiml(
    call_sid: str,
    *,
    call_id: str = "",
    stream_url: str | None = None,
    prompt: str | None = "The supervisor is unavailable. Reconnecting you to our assistant.",
) -> str:
    """Construct TwiML that reconnects the caller back to the AI media stream on takeover failure."""
    response = VoiceResponse()
    if prompt:
        response.say(prompt, voice="Polly.Joanna")

    connect = Connect()
    if not stream_url:
        ws_base = str(getattr(settings, "ws_base_url", "") or "wss://localhost").rstrip("/")
        try:
            from app.telephony.twilio_handler import create_stream_token

            token_q = f"?token={create_stream_token(call_sid)}" if call_sid else ""
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            token_q = ""
        stream_url = f"{ws_base}/telephony/ws{token_q}"

    stream = connect.stream(url=stream_url)
    if call_id:
        stream.parameter(name="call_id", value=str(call_id))
    if call_sid:
        stream.parameter(name="call_sid", value=str(call_sid))
    response.append(connect)
    return str(response)


@dataclass(frozen=True)
class TakeoverResult:
    ok: bool
    call_id: str
    call_sid: str
    state: str
    destination: str
    twiml: str = ""
    recording_continuity: bool = True
    takeover_pending: bool = False
    rolled_back_to_ai: bool = False
    rollback_twiml: str = ""
    events: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "call_id": self.call_id,
            "call_sid": self.call_sid,
            "state": self.state,
            "destination": self.destination,
            "recording_continuity": self.recording_continuity,
            "takeover_pending": self.takeover_pending,
            "rolled_back_to_ai": self.rolled_back_to_ai,
            "events": list(self.events),
            "error": self.error,
        }


def _append_context_event(
    call: Call,
    event_name: str,
    *,
    destination: str = "",
    recording_continuity: bool = True,
    rolled_back_to_ai: bool = False,
    error: str | None = None,
) -> list[str]:
    ctx = dict(getattr(call, "transfer_context", None) or {})
    history = list(ctx.get("takeover_events") or [])
    entry: dict[str, Any] = {
        "event": event_name,
        "at": _now_iso(),
        "recording_continuity": recording_continuity,
    }
    if destination:
        entry["destination"] = (
            phone.redact(destination)
            if destination.startswith("+")
            else destination
        )
    if rolled_back_to_ai:
        entry["rolled_back_to_ai"] = True
    if error:
        entry["error"] = error[:240]
    history.append(entry)
    ctx["takeover_events"] = history
    ctx["takeover_state"] = event_name
    ctx["recording_continuity"] = recording_continuity
    ctx["rolled_back_to_ai"] = rolled_back_to_ai
    call.transfer_context = ctx
    return [str(item.get("event")) for item in history if isinstance(item, dict) and item.get("event")]


async def _record_system_turn(
    session: AsyncSession | None,
    call: Call,
    text: str,
) -> None:
    if session is None or getattr(call, "id", None) is None:
        return
    session.add(Turn(call_id=call.id, speaker=Speaker.SYSTEM, text=text))
    await session.flush()


async def takeover(
    call: Call,
    supervisor_destination: str | None = None,
    *,
    webrtc_client: str | None = None,
    session: AsyncSession | None = None,
    tenant: Tenant | None = None,
    adapter: TelephonyAdapter | None = None,
    provider: TelephonyProvider | None = None,
    whisper_text: str | None = None,
    reason: str = "",
    record: bool | None = None,
    conference_name: str | None = None,
    caller_id: str | None = None,
    confirm_immediately: bool = True,
) -> TakeoverResult:
    """Execute a live supervisor takeover on ``call``.

    1. Marks ``call.takeover_pending = True`` and records ``takeover_started`` so that
       when the provider TwiML replacement closes the AI media stream, ``pipeline.py``
       does NOT finalize the call as a hang-up.
    2. Calls ``adapter.bridge_to(...)`` to replace the call's TwiML with ``<Dial>`` /
       ``<Conference>`` (preserving recording continuity).
    3. On provider success, transitions to ``takeover_completed`` (or keeps ``takeover_started``
       until ``complete_takeover`` if ``confirm_immediately=False``).
    4. On provider failure, rolls back ``call.takeover_pending = False``, keeps the call
       active with the AI (``CallStatus.IN_PROGRESS``), and records ``takeover_failed``.
    """
    if call_state.is_terminal(call.status):
        raise ValueError(f"Call {call.id} is already in terminal state {call.status.value}")

    destination = normalize_takeover_destination(
        supervisor_destination,
        webrtc_client=webrtc_client,
        conference_name=conference_name,
    )
    record_enabled = True if record is None else bool(record)
    effective_caller_id = caller_id or (
        phone.try_normalize(getattr(tenant, "twilio_number", None))
        if tenant is not None
        else None
    )

    # 1. Set takeover_pending BEFORE touching the provider so media stream teardown
    # is recognized as an intentional takeover handoff, not a caller hang-up.
    previous_status = call.status
    set_takeover_pending(call, True, destination=destination, reason=reason)
    call.escalated = True
    call.transfer_state = TransferState.REQUESTED
    call.transfer_requested_at = call.transfer_requested_at or _now()
    call.transfer_destination = destination[:64]
    call.transfer_reason = (reason or "supervisor_takeover")[:400]
    call.transfer_attempts = int(call.transfer_attempts or 0) + 1
    call.transfer_error = None

    events = _append_context_event(
        call,
        TAKEOVER_STARTED,
        destination=destination,
        recording_continuity=record_enabled,
    )
    await _record_system_turn(session, call, "Supervisor takeover started.")
    if session is not None and getattr(call, "id", None) is not None:
        from app.webhooks.call_event_bridge import publish_call_event

        await publish_call_event(session, call, "transfer_started")
        await session.flush()

    twiml = build_takeover_twiml(
        destination,
        whisper_text=whisper_text,
        record=record_enabled,
        conference_name=conference_name,
        caller_id=effective_caller_id,
    )

    effective_adapter = adapter or TwilioAdapter()
    if not getattr(effective_adapter, "supports_takeover", False):
        # Roll back pending flag before raising UnsupportedCapability
        set_takeover_pending(call, False)
        call.transfer_state = TransferState.NONE
        raise UnsupportedCapability(
            f"Provider '{effective_adapter.name}' does not support live call takeover",
            provider=effective_adapter.name,
        )

    prev_provider = get_provider()
    try:
        if provider is not None:
            set_provider(provider)
        bridge_res = await effective_adapter.bridge_to(
            call.call_sid,
            destination,
            whisper_text=whisper_text,
            record=record_enabled,
            conference_name=conference_name,
            caller_id=effective_caller_id,
        )
        if bridge_res is not None and isinstance(getattr(bridge_res, "details", None), dict):
            twiml = str(bridge_res.details.get("twiml") or twiml)
    except UnsupportedCapability:
        set_takeover_pending(call, False)
        call.transfer_state = TransferState.NONE
        raise
    except Exception as exc:
        return await rollback_to_ai(
            call,
            session=session,
            provider=provider or prev_provider,
            previous_status=previous_status,
            destination=destination,
            recording_continuity=record_enabled,
            reconnect_stream=True,
            reason=str(exc) or "provider_bridge_failed",
        )
    finally:
        if provider is not None:
            set_provider(prev_provider)

    # 2. Provider accepted the TwiML replacement
    call.transfer_started_at = _now()
    if confirm_immediately:
        call.transfer_state = TransferState.CONNECTED
        call.transfer_completed_at = _now()
        events = _append_context_event(
            call,
            TAKEOVER_COMPLETED,
            destination=destination,
            recording_continuity=record_enabled,
        )
        await _record_system_turn(session, call, "Supervisor takeover completed.")
        if session is not None and getattr(call, "id", None) is not None:
            from app.webhooks.call_event_bridge import publish_call_event

            await publish_call_event(session, call, "transfer_completed")
            await session.flush()
        final_state = "completed"
    else:
        call.transfer_state = TransferState.DIALING
        final_state = "started"

    log.info(
        "takeover.succeeded",
        call_id=str(getattr(call, "id", "")),
        call_sid=call.call_sid,
        state=final_state,
        recording_continuity=record_enabled,
    )
    return TakeoverResult(
        ok=True,
        call_id=str(getattr(call, "id", "")),
        call_sid=call.call_sid,
        state=final_state,
        destination=destination,
        twiml=twiml,
        recording_continuity=record_enabled,
        takeover_pending=is_takeover_pending(call),
        rolled_back_to_ai=False,
        events=events,
    )


async def complete_takeover(
    call: Call,
    *,
    session: AsyncSession | None = None,
) -> TakeoverResult:
    """Mark an in-flight takeover (`takeover_started`) as `takeover_completed`."""
    ctx = dict(getattr(call, "transfer_context", None) or {})
    destination = str(call.transfer_destination or ctx.get("supervisor_destination") or "")
    record_enabled = bool(ctx.get("recording_continuity", True))
    call.transfer_state = TransferState.CONNECTED
    call.transfer_completed_at = _now()
    events = _append_context_event(
        call,
        TAKEOVER_COMPLETED,
        destination=destination,
        recording_continuity=record_enabled,
    )
    await _record_system_turn(session, call, "Supervisor takeover completed.")
    if session is not None and getattr(call, "id", None) is not None:
        from app.webhooks.call_event_bridge import publish_call_event

        await publish_call_event(session, call, "transfer_completed")
        await session.flush()

    return TakeoverResult(
        ok=True,
        call_id=str(getattr(call, "id", "")),
        call_sid=call.call_sid,
        state="completed",
        destination=destination,
        recording_continuity=record_enabled,
        takeover_pending=is_takeover_pending(call),
        rolled_back_to_ai=False,
        events=events,
    )


async def rollback_to_ai(
    call: Call,
    *,
    session: AsyncSession | None = None,
    provider: TelephonyProvider | None = None,
    previous_status: CallStatus = CallStatus.IN_PROGRESS,
    destination: str = "",
    recording_continuity: bool = True,
    reconnect_stream: bool = True,
    reason: str = "supervisor_leg_failed",
) -> TakeoverResult:
    """Roll back a failed supervisor takeover to the AI voice agent.

    Clears ``call.takeover_pending``, keeps ``call.status`` as ``IN_PROGRESS`` (never
    finalized as ``COMPLETED`` or ``FAILED``), records ``takeover_failed``, and when
    ``reconnect_stream=True`` issues a TwiML redirect back to the AI media stream.
    """
    set_takeover_pending(call, False)
    if call.status in (CallStatus.RINGING, CallStatus.IN_PROGRESS, CallStatus.TRANSFERRED):
        call.status = (
            previous_status
            if previous_status in (CallStatus.IN_PROGRESS, CallStatus.RINGING)
            else CallStatus.IN_PROGRESS
        )
    call.ended_at = None
    call.transfer_state = TransferState.FAILED
    call.transfer_failed_at = _now()
    call.transfer_error = reason[:300]

    rollback_twiml = ""
    if reconnect_stream and call.call_sid:
        rollback_twiml = build_ai_rollback_twiml(
            call.call_sid,
            call_id=str(getattr(call, "id", "") or ""),
        )
        eff_provider = provider or get_provider()
        if isinstance(eff_provider, FakeTelephonyProvider) or (
            (settings.twilio_account_sid or "").strip()
            and (settings.twilio_auth_token or "").strip()
        ):
            try:
                await eff_provider.redirect_call(call.call_sid, rollback_twiml)
            except Exception as exc:
                log.warning(
                    "takeover.rollback_redirect_failed",
                    call_id=str(getattr(call, "id", "")),
                    error_type=type(exc).__name__,
                )

    _append_context_event(
        call,
        TAKEOVER_FAILED,
        destination=destination or str(call.transfer_destination or ""),
        recording_continuity=recording_continuity,
        rolled_back_to_ai=True,
        error=reason,
    )
    events = _append_context_event(
        call,
        "takeover_rolled_back_to_ai",
        destination=destination or str(call.transfer_destination or ""),
        recording_continuity=recording_continuity,
        rolled_back_to_ai=True,
        error=reason,
    )
    await _record_system_turn(
        session,
        call,
        "Supervisor takeover failed; rolled back to AI agent.",
    )
    if session is not None and getattr(call, "id", None) is not None:
        from app.webhooks.call_event_bridge import publish_call_event

        await publish_call_event(session, call, "transfer_failed")
        await session.flush()

    log.warning(
        "takeover.rolled_back_to_ai",
        call_id=str(getattr(call, "id", "")),
        call_sid=call.call_sid,
        reason=reason[:120],
    )
    return TakeoverResult(
        ok=False,
        call_id=str(getattr(call, "id", "")),
        call_sid=call.call_sid,
        state="failed",
        destination=destination or str(call.transfer_destination or ""),
        recording_continuity=recording_continuity,
        takeover_pending=False,
        rolled_back_to_ai=True,
        rollback_twiml=rollback_twiml,
        events=events,
        error=reason,
    )
