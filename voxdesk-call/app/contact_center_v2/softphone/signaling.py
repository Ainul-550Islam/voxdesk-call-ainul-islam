"""One-time tickets and tenant/call-scoped WebRTC signaling."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import PermissionDenied, Validation
from app.contact_center_v2.repository import as_utc, utcnow
from app.db.contact_center_v2_models import SoftphoneCall, SoftphoneSession

_EVENT_TYPES = frozenset({
    "call.updated",
    "call.attached",
    "call.detached",
    "session.heartbeat",
    "assist.updated",
    "webrtc.offer",
    "webrtc.answer",
    "webrtc.ice",
    "call.dtmf",
    "signaling.ack",
    "pong",
    "error",
})
_MEDIA_EVENTS = frozenset({"webrtc.offer", "webrtc.answer", "webrtc.ice", "call.dtmf"})
_DTMF = re.compile(r"^[0-9*#A-Da-d]{1,32}$")


def ticket_digest(ticket: str) -> str:
    if len(ticket) < 32 or len(ticket) > 512:
        raise Validation("Invalid signaling ticket")
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


async def issue_websocket_ticket(
    session: AsyncSession,
    row: SoftphoneSession,
    *,
    now: datetime | None = None,
) -> str:
    moment = now or utcnow()
    if row.status != "active" or as_utc(row.expires_at) <= moment:
        raise PermissionDenied("Softphone session is not active")
    ticket = secrets.token_urlsafe(48)
    row.ws_ticket_hash = ticket_digest(ticket)
    await session.flush()
    # The raw ticket is returned once and is never persisted or logged.
    setattr(row, "_cc2_websocket_ticket", ticket)
    return ticket


async def consume_websocket_ticket(
    session: AsyncSession,
    ticket: str,
    *,
    tenant_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> SoftphoneSession:
    digest = ticket_digest(ticket)
    moment = now or utcnow()
    conditions = [
        SoftphoneSession.ws_ticket_hash == digest,
        SoftphoneSession.status == "active",
        SoftphoneSession.expires_at > moment,
    ]
    if tenant_id is not None:
        conditions.append(SoftphoneSession.tenant_id == tenant_id)
    session_id = (
        await session.execute(
            update(SoftphoneSession)
            .where(*conditions)
            .values(ws_ticket_hash="")
            .returning(SoftphoneSession.id)
        )
    ).scalar_one_or_none()
    if session_id is None:
        raise PermissionDenied("Invalid, expired, or already-used signaling ticket")
    row = await session.get(SoftphoneSession, session_id)
    if row is None:
        raise PermissionDenied("Invalid, expired, or already-used signaling ticket")
    row.ws_ticket_hash = ""
    await session.flush()
    return row


def ticket_matches(row: SoftphoneSession, ticket: str) -> bool:
    return bool(row.ws_ticket_hash) and hmac.compare_digest(row.ws_ticket_hash, ticket_digest(ticket))


def event_envelope(
    *,
    event_id: uuid.UUID | None = None,
    event_type: str,
    call_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in _EVENT_TYPES:
        raise Validation("Unsupported softphone signaling event")
    body = dict(payload or {})
    for key in list(body):
        if any(part in key.lower() for part in ("token", "secret", "password", "raw_transcript")):
            body.pop(key)
    return {
        "id": str(event_id or uuid.uuid4()),
        "type": event_type,
        "call_id": str(call_id) if call_id else None,
        "payload": body,
    }


def encode_event(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"), ensure_ascii=True)


async def publish_softphone_event(
    *,
    tenant_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    event_id: str | None = None,
) -> bool:
    from app.realtime.publisher import publish_event

    return await publish_event(
        tenant_id=str(tenant_id),
        room=f"agent:{agent_user_id}",
        kind=event_type,
        payload=payload,
        event_id=event_id,
    )


@dataclass(eq=False)
class SignalingPeer:
    tenant_id: uuid.UUID
    environment_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    send: Callable[[dict[str, Any]], Awaitable[Any]]
    calls: set[uuid.UUID] = field(default_factory=set)


class SignalingHub:
    """In-process fan-out; durable call state remains authoritative."""

    def __init__(self) -> None:
        self._rooms: dict[tuple[uuid.UUID, uuid.UUID], set[SignalingPeer]] = {}

    @staticmethod
    def _key(peer: SignalingPeer, call_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return peer.tenant_id, call_id

    def attach(self, peer: SignalingPeer, call_id: uuid.UUID) -> None:
        room = self._rooms.setdefault(self._key(peer, call_id), set())
        if peer not in room and len(room) >= 32:
            raise Validation("This call has too many signaling participants")
        room.add(peer)
        peer.calls.add(call_id)

    def detach(self, peer: SignalingPeer, call_id: uuid.UUID) -> bool:
        key = self._key(peer, call_id)
        room = self._rooms.get(key)
        removed = bool(room and peer in room)
        if room:
            room.discard(peer)
            if not room:
                self._rooms.pop(key, None)
        peer.calls.discard(call_id)
        return removed

    def disconnect(self, peer: SignalingPeer) -> None:
        for call_id in list(peer.calls):
            self.detach(peer, call_id)

    def attached(self, peer: SignalingPeer, call_id: uuid.UUID) -> bool:
        return peer in self._rooms.get(self._key(peer, call_id), set())

    async def relay(
        self,
        peer: SignalingPeer,
        *,
        call_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        include_sender: bool = False,
    ) -> int:
        if event_type not in _MEDIA_EVENTS | {"call.updated"}:
            raise Validation("This event cannot be relayed")
        if not self.attached(peer, call_id):
            raise PermissionDenied("Attach to the call before relaying media or controls")
        room = self._rooms.get(self._key(peer, call_id), set())
        delivered = 0
        for recipient in list(room):
            if recipient is peer and not include_sender:
                continue
            if recipient.tenant_id != peer.tenant_id or recipient.environment_id != peer.environment_id:
                continue
            await recipient.send(event_envelope(event_type=event_type, call_id=call_id, payload=payload))
            delivered += 1
        return delivered


signaling_hub = SignalingHub()


def _message_call_id(message: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(message.get("call_id") or ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise Validation("A valid call_id is required") from exc


def _payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise Validation("Signaling payload must be an object")
    return payload


def _sdp(payload: dict[str, Any], expected: str) -> dict[str, str]:
    description = payload.get("description")
    if not isinstance(description, dict):
        raise Validation("An SDP description object is required")
    kind = description.get("type")
    sdp = description.get("sdp")
    if kind != expected or not isinstance(sdp, str) or not 1 <= len(sdp) <= 65_536:
        raise Validation(f"A bounded {expected} SDP description is required")
    return {"type": expected, "sdp": sdp}


def _ice(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        raise Validation("An ICE candidate object is required")
    value = candidate.get("candidate")
    if not isinstance(value, str) or len(value) > 4096:
        raise Validation("ICE candidate text is invalid")
    result: dict[str, Any] = {"candidate": value}
    for key in ("sdpMid", "usernameFragment"):
        item = candidate.get(key)
        if item is not None and (not isinstance(item, str) or len(item) > 256):
            raise Validation(f"ICE {key} is invalid")
        if item is not None:
            result[key] = item
    index = candidate.get("sdpMLineIndex")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= 64):
        raise Validation("ICE sdpMLineIndex is invalid")
    if index is not None:
        result["sdpMLineIndex"] = index
    return result


async def _audit(
    session: AsyncSession,
    peer: SignalingPeer,
    *,
    event_type: str,
    call_id: uuid.UUID,
) -> None:
    from app.auth.service import record_audit
    from app.db.models import AuditAction

    await record_audit(
        session,
        action=AuditAction.GOVERNANCE_EVENT,
        event_type=event_type,
        tenant_id=peer.tenant_id,
        environment_id=peer.environment_id,
        actor_user_id=peer.user_id,
        actor_type="human",
        resource_type="softphone_call",
        resource_id=call_id,
        detail={},
        commit=False,
    )


async def _owned_call(session: AsyncSession, peer: SignalingPeer, call_id: uuid.UUID) -> SoftphoneCall:
    call = await session.get(SoftphoneCall, call_id)
    if call is None or call.tenant_id != peer.tenant_id:
        raise PermissionDenied("Call is outside the signaling tenant")
    if call.environment_id != peer.environment_id or call.agent_user_id != peer.user_id:
        raise PermissionDenied("Call belongs to another agent or environment")
    if call.session_id != peer.session_id:
        raise PermissionDenied("Call belongs to another softphone registration")
    return call


async def handle_signaling_message(
    session: AsyncSession,
    hub: SignalingHub,
    peer: SignalingPeer,
    registration: SoftphoneSession,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate and execute one authenticated client message."""
    if not isinstance(message, dict):
        raise Validation("Signaling message must be an object")
    if (
        registration.id != peer.session_id
        or registration.tenant_id != peer.tenant_id
        or registration.environment_id != peer.environment_id
        or registration.user_id != peer.user_id
        or registration.status != "active"
        or as_utc(registration.expires_at) <= utcnow()
    ):
        raise PermissionDenied("Softphone registration is no longer active")
    kind = message.get("type")
    if kind in {"ping", "heartbeat"}:
        registration.last_heartbeat_at = utcnow()
        await session.flush()
        return event_envelope(event_type="pong", payload={"session_id": str(peer.session_id)})
    if kind not in {
        "call.attach", "call.detach", "call.hold", "call.resume", "call.mute",
        "call.dtmf", "webrtc.offer", "webrtc.answer", "webrtc.ice",
    }:
        raise Validation("Unsupported softphone signaling request")
    call_id = _message_call_id(message)
    payload = _payload(message)
    call = await _owned_call(session, peer, call_id)

    if kind == "call.attach":
        hub.attach(peer, call_id)
        await _audit(session, peer, event_type="cc2.softphone.call_attached", call_id=call_id)
        return event_envelope(event_type="call.attached", call_id=call_id, payload={"status": call.status})
    if kind == "call.detach":
        removed = hub.detach(peer, call_id)
        await _audit(session, peer, event_type="cc2.softphone.call_detached", call_id=call_id)
        return event_envelope(event_type="call.detached", call_id=call_id, payload={"detached": removed})

    if kind in {"call.hold", "call.resume", "call.mute"}:
        if not hub.attached(peer, call_id):
            raise PermissionDenied("Attach to the call before changing its controls")
        from app.auth.dependencies import TenantContext
        from app.contact_center_v2.softphone.service import control_call
        from app.db.models import Tenant, User

        user = await session.get(User, peer.user_id)
        tenant = await session.get(Tenant, peer.tenant_id)
        if user is None or tenant is None or not user.is_active or user.tenant_id != peer.tenant_id:
            raise PermissionDenied("The signaling agent is no longer active")
        action = "mute" if kind == "call.mute" else "hold"
        enabled = kind != "call.resume"
        if kind == "call.mute":
            value = payload.get("enabled")
            if not isinstance(value, bool):
                raise Validation("Mute requires a boolean enabled value")
            enabled = value
        context = TenantContext(user=user, tenant=tenant)
        call = await control_call(session, context, call_id=call_id, action=action, enabled=enabled)
        event_payload = {"status": call.status, "muted": call.muted, "on_hold": call.on_hold}
        await _audit(session, peer, event_type=f"cc2.softphone.{action}_signaled", call_id=call_id)
        await hub.relay(peer, call_id=call_id, event_type="call.updated", payload=event_payload)
        return event_envelope(event_type="call.updated", call_id=call_id, payload=event_payload)

    if kind == "call.dtmf":
        digits = payload.get("digits")
        if not isinstance(digits, str) or not _DTMF.fullmatch(digits):
            raise Validation("DTMF must contain 1–32 digits, *, #, or A–D characters")
        delivered = await hub.relay(
            peer, call_id=call_id, event_type=kind, payload={"digits": digits}
        )
        # Digits are deliberately absent from audit details and acknowledgements.
        await _audit(session, peer, event_type="cc2.softphone.dtmf_signaled", call_id=call_id)
        return event_envelope(event_type="signaling.ack", call_id=call_id, payload={"action": kind, "delivered": delivered})

    if kind == "webrtc.offer":
        clean = {"description": _sdp(payload, "offer")}
    elif kind == "webrtc.answer":
        clean = {"description": _sdp(payload, "answer")}
    else:
        clean = {"candidate": _ice(payload)}
    delivered = await hub.relay(peer, call_id=call_id, event_type=kind, payload=clean)
    return event_envelope(
        event_type="signaling.ack", call_id=call_id,
        payload={"action": kind, "delivered": delivered},
    )


__all__ = [
    "SignalingHub",
    "SignalingPeer",
    "consume_websocket_ticket",
    "encode_event",
    "event_envelope",
    "handle_signaling_message",
    "issue_websocket_ticket",
    "publish_softphone_event",
    "signaling_hub",
    "ticket_digest",
    "ticket_matches",
]
