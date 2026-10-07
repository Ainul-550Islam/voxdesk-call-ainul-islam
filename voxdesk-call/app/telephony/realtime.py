"""
app/telephony/realtime.py
Real-time voice runtime and WebSocket media session handler connecting
`TelephonyCallSession`, `MediaGatewayManager`, DTMF, and agent conversation turns.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Agent, AgentVersion
from app.telephony.agent_version import (
    AgentVersionResolutionError,
    resolve_telephony_agent_version,
    snapshot_profile,
)
from app.db.telephony_models import TelephonyCallSession
from app.telephony.call_session import (
    CallSessionManager,
    append_runtime_event,
    append_transcript_turn,
)
from app.telephony.dtmf import process_call_dtmf
from app.telephony.enums import (
    TERMINAL_CALL_STATES,
    InternalTelephonyEventType,
    MediaSessionState,
    TelephonyCallState,
)
from app.telephony.exceptions import (
    CallSessionNotFoundError as CallSessionNotFoundError,
    MediaSessionError,
    TelephonyConfigurationError as ConfigurationError,
    TelephonyAuthorizationError as TelephonyAuthorizationError,
)
from app.telephony.media_gateway import media_gateway_manager


def _media_signing_key() -> bytes:
    # The authoritative setting is jwt_secret; jwt_secret_key is not a Settings field.
    secret = settings.jwt_secret
    if not secret or not secret.strip():
        raise ConfigurationError(
            "Media stream signing requires JWT_SECRET.",
            status_code=501,
            code="MEDIA_STREAM_NOT_CONFIGURED",
        )
    return secret.encode("utf-8")


def issue_media_stream_token(
    *,
    call_id: UUID,
    tenant_id: UUID,
    ttl_seconds: int = 3600,
) -> str:
    exp = int(time.time()) + ttl_seconds
    secret = _media_signing_key()
    msg = f"{call_id}:{tenant_id}:{exp}".encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_media_stream_token(
    token: str | None,
    *,
    call_id: UUID,
    tenant_id: UUID,
) -> bool:
    if not token or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    secret = _media_signing_key()
    msg = f"{call_id}:{tenant_id}:{exp}".encode("utf-8")
    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.strip(), expected)


async def resolve_agent_runtime_profile(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: str | None,
    agent_version_id: str | None = None,
    agent_version_number: int | None = None,
    environment_id: UUID | None = None,
) -> dict[str, Any]:
    """Load exactly the persisted published snapshot pinned to the call.

    Missing agents, missing version pointers, draft versions, and mismatched
    snapshots fail closed. This runtime never invents a default agent profile
    and never chooses the numerically newest version as a substitute.
    """
    if not agent_id:
        raise MediaSessionError(
            "A persisted agent binding is required for the voice runtime.",
            status_code=503,
            code="AGENT_RUNTIME_NOT_CONFIGURED",
            detail={"state": "NOT_CONFIGURED"},
        )

    try:
        agent_uuid = UUID(str(agent_id))
    except (TypeError, ValueError):
        raise MediaSessionError(
            "The persisted agent binding is not a valid agent UUID.",
            status_code=503,
            code="AGENT_RUNTIME_BINDING_INVALID",
            detail={"agent_id": str(agent_id)},
        ) from None

    agent_row = await session.scalar(
        select(Agent).where(
            Agent.id == agent_uuid,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
            Agent.archived_at.is_(None),
        )
    )
    if agent_row is None:
        raise MediaSessionError(
            "The bound agent is not available in this tenant.",
            status_code=503,
            code="AGENT_RUNTIME_AGENT_NOT_FOUND",
            detail={"agent_id": str(agent_uuid)},
        )

    if environment_id is None:
        raise MediaSessionError(
            "The call session has no persisted environment binding.",
            status_code=503,
            code="AGENT_RUNTIME_ENVIRONMENT_NOT_CONFIGURED",
            detail={"agent_id": str(agent_uuid)},
        )

    requested_number = agent_version_number
    if agent_version_id is not None:
        try:
            pinned_version_id = UUID(str(agent_version_id))
        except (TypeError, ValueError):
            raise MediaSessionError(
                "The call's persisted agent version id is invalid.",
                status_code=503,
                code="AGENT_RUNTIME_VERSION_ID_INVALID",
                detail={"agent_id": str(agent_uuid)},
            ) from None
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == pinned_version_id,
                AgentVersion.tenant_id == tenant_id,
                AgentVersion.agent_id == agent_row.id,
            )
        )
        if version is None or (requested_number is not None and version.version_number != requested_number):
            raise MediaSessionError(
                "The call's persisted agent version id and number do not resolve to the same snapshot.",
                status_code=503,
                code="AGENT_RUNTIME_VERSION_MISMATCH",
                detail={"agent_id": str(agent_uuid), "agent_version_number": requested_number},
            )
        requested_number = int(version.version_number)

    try:
        version_row = await resolve_telephony_agent_version(
            session,
            tenant_id=tenant_id,
            agent=agent_row,
            environment_id=environment_id,
            requested_version_number=requested_number,
        )
        profile = snapshot_profile(version_row, agent_row)
    except AgentVersionResolutionError as exc:
        raise MediaSessionError(
            str(exc),
            status_code=503,
            code=exc.code,
            detail=exc.detail,
        ) from None

    profile["environment_id"] = str(environment_id)
    return profile


class RealtimeVoiceSessionOrchestrator:
    """Orchestrates real-time media streaming, utterances, barge-in, and DTMF over WebSocket or API."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start_media_session(
        self,
        *,
        call_session: TelephonyCallSession,
        encoding: str = "mulaw",
        sample_rate: int = 8000,
    ) -> dict[str, Any]:
        if TelephonyCallState(call_session.status) in TERMINAL_CALL_STATES:
            raise MediaSessionError(
                f"Cannot open media stream for call in terminal state {call_session.status}."
            )

        call_metadata = (
            call_session.metadata_json if isinstance(call_session.metadata_json, dict) else {}
        )
        profile = await resolve_agent_runtime_profile(
            self.session,
            tenant_id=call_session.tenant_id,
            agent_id=call_session.agent_id,
            agent_version_id=call_metadata.get("resolved_agent_version_id"),
            agent_version_number=call_session.agent_version_number,
            environment_id=call_session.environment_id,
        )

        manager = CallSessionManager(self.session)
        if call_session.status in {
            TelephonyCallState.CREATED.value,
            TelephonyCallState.DIALING.value,
            TelephonyCallState.RINGING.value,
        }:
            await manager.transition_state(
                call_session,
                TelephonyCallState.ANSWERED,
                event_detail={
                    "trigger": "media_session_started",
                    "agent_version_id": profile["version_id"],
                    "agent_version_number": profile["version_number"],
                },
            )
        if call_session.status == TelephonyCallState.ANSWERED.value:
            await manager.transition_state(
                call_session,
                TelephonyCallState.IN_PROGRESS,
                event_detail={"encoding": encoding, "sample_rate": sample_rate},
            )

        gw = media_gateway_manager.open_session(
            call_id=call_session.id,
            tenant_id=call_session.tenant_id,
            provider_call_id=call_session.provider_call_id,
            encoding=encoding,
            sample_rate=sample_rate,
        )
        call_session.media_state = gw.state.value
        call_session.agent_version_number = int(profile["version_number"])
        call_metadata = dict(call_metadata)
        call_metadata["resolved_agent_version_id"] = str(profile["version_id"])
        call_metadata["resolved_agent_version_number"] = int(profile["version_number"])
        call_session.metadata_json = call_metadata

        append_runtime_event(
            call_session,
            event_type=InternalTelephonyEventType.CALL_MEDIA_STARTED,
            detail={
                "encoding": gw.encoding,
                "sample_rate": gw.sample_rate,
                "agent_name": profile["agent_name"],
                "agent_version_id": profile["version_id"],
                "version_number": profile["version_number"],
            },
        )

        # The deterministic greeting payload exists only in an explicitly
        # marked simulation. A live call must never receive UTF-8 prompt text
        # masquerading as PCM/mu-law or a locally synthesized success reply.
        if call_session.is_simulation and not call_session.transcript_turns:
            greeting_text = str(profile["greeting"])
            append_transcript_turn(
                call_session,
                role="agent",
                content=greeting_text,
                agent_id=call_session.agent_id,
                metadata={
                    "event": "simulation_initial_greeting",
                    "voice_id": profile["voice_id"],
                    "synthetic_media": True,
                },
            )
            greeting_bytes = greeting_text.encode("utf-8")[:160].ljust(160, b"\x7f")
            media_gateway_manager.enqueue_outbound_frame(
                call_session.id,
                payload=greeting_bytes,
                timestamp_ms=0,
            )
            call_session.media_state = MediaSessionState.SPEAKING.value

        await self.session.flush()
        return {
            "type": "media.started",
            "call_id": str(call_session.id),
            "status": call_session.status,
            "media_state": call_session.media_state,
            "agent_profile": profile,
            "synthetic_media": bool(call_session.is_simulation),
            "gateway": gw.snapshot(),
        }

    async def handle_ws_message(
        self,
        *,
        call_session: TelephonyCallSession,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        msg_type = str(message.get("type") or message.get("event") or "").strip().lower()

        if msg_type in {"start", "media.start"}:
            return await self.start_media_session(
                call_session=call_session,
                encoding=str(message.get("encoding") or "mulaw"),
                sample_rate=int(message.get("sample_rate") or 8000),
            )

        if msg_type in {"audio", "media", "media.audio"}:
            if media_gateway_manager.get_session(call_session.id) is None:
                await self.start_media_session(call_session=call_session)
            payload_b64 = message.get("payload") or (
                message.get("media", {}) if isinstance(message.get("media"), dict) else {}
            ).get("payload")
            if not payload_b64:
                raise MediaSessionError("Missing 'payload' in media.audio message.")
            frame_info = media_gateway_manager.ingest_inbound_frame(
                call_session.id,
                payload=payload_b64,
                encoding=message.get("encoding"),
                sample_rate=message.get("sample_rate"),
                timestamp_ms=int(message.get("timestamp_ms") or 0),
                speech_detected=bool(message.get("speech_detected", False)),
            )
            call_session.media_state = frame_info["state"]
            await self.session.flush()
            return {
                "type": "media.ack",
                "call_id": str(call_session.id),
                **frame_info,
            }

        if msg_type in {"barge_in", "media.barge_in", "interrupt"}:
            if media_gateway_manager.get_session(call_session.id) is None:
                await self.start_media_session(call_session=call_session)
            res = media_gateway_manager.trigger_barge_in(
                call_session.id,
                reason=str(message.get("reason") or "caller_barge_in"),
            )
            call_session.media_state = res["state"]
            append_runtime_event(
                call_session,
                event_type="CALL_BARGE_IN",
                detail=res,
            )
            await self.session.flush()
            return {
                "type": "media.barge_in.ack",
                **res,
            }

        if msg_type in {"dtmf", "media.dtmf"}:
            digits = str(message.get("digits") or message.get("digit") or "")
            dtmf_res = await process_call_dtmf(
                self.session,
                call_session=call_session,
                digits=digits,
                source="media_stream",
            )
            return {
                "type": "media.dtmf.ack",
                **dtmf_res.model_dump(mode="json"),
            }

        if msg_type in {"utterance", "media.utterance", "transcript"}:
            if media_gateway_manager.get_session(call_session.id) is None:
                await self.start_media_session(call_session=call_session)
            caller_text = str(message.get("text") or message.get("content") or "").strip()
            if not caller_text:
                raise MediaSessionError("Utterance text cannot be empty.")
            if not call_session.is_simulation:
                raise MediaSessionError(
                    "A live agent/LLM/TTS runtime is not configured for this media session.",
                    status_code=503,
                    code="LIVE_AGENT_RUNTIME_NOT_CONFIGURED",
                    detail={"state": "NOT_CONFIGURED", "agent_id": call_session.agent_id},
                )

            append_transcript_turn(
                call_session,
                role="caller",
                content=caller_text,
                metadata={"source": "realtime_ws"},
            )
            call_metadata = (
                call_session.metadata_json if isinstance(call_session.metadata_json, dict) else {}
            )
            profile = await resolve_agent_runtime_profile(
                self.session,
                tenant_id=call_session.tenant_id,
                agent_id=call_session.agent_id,
                agent_version_id=call_metadata.get("resolved_agent_version_id"),
                agent_version_number=call_session.agent_version_number,
                environment_id=call_session.environment_id,
            )
            reply_text = (
                f"[{profile['agent_name']}] Understood your request regarding: {caller_text}"
            )
            append_transcript_turn(
                call_session,
                role="agent",
                content=reply_text,
                agent_id=call_session.agent_id,
                metadata={
                    "version_id": profile["version_id"],
                    "version_number": profile["version_number"],
                    "synthetic_simulation": True,
                },
            )
            audio_bytes = reply_text.encode("utf-8")[:160].ljust(160, b"\x7f")
            frame = media_gateway_manager.enqueue_outbound_frame(
                call_session.id,
                payload=audio_bytes,
                timestamp_ms=int(time.time() * 1000) % 1_000_000,
            )
            call_session.media_state = MediaSessionState.SPEAKING.value
            await self.session.flush()
            return {
                "type": "media.agent_response",
                "call_id": str(call_session.id),
                "caller_text": caller_text,
                "agent_text": reply_text,
                "audio_payload": base64.b64encode(frame.payload_bytes).decode("ascii"),
                "sequence": frame.sequence,
                "media_state": call_session.media_state,
                "synthetic_media": True,
                "execution_kind": call_session.execution_kind,
            }

        if msg_type in {"stop", "media.stop", "close"}:
            snapshot = media_gateway_manager.close_session(call_session.id)
            call_session.media_state = MediaSessionState.DISCONNECTED.value
            call_session.updated_at = datetime.utcnow()
            await self.session.flush()
            return {
                "type": "media.stopped",
                "call_id": str(call_session.id),
                "media_state": call_session.media_state,
                "gateway": snapshot,
            }

        raise MediaSessionError(
            f"Unsupported WebSocket message type: {msg_type!r}",
            detail={"type": msg_type},
        )
