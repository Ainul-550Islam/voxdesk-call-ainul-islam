"""
app/telephony/media_gateway.py
Real-time bidirectional audio / media gateway with frame validation,
turn-taking state, barge-in / interruption flushing, and clean teardown.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.telephony.enums import MediaSessionState
from app.telephony.exceptions import MediaSessionError

SUPPORTED_ENCODINGS: frozenset[str] = frozenset(
    {"pcm16", "linear16", "mulaw", "pcmu", "alaw", "pcma", "opus"}
)
SUPPORTED_SAMPLE_RATES: frozenset[int] = frozenset({8000, 16000, 22050, 24000, 44100, 48000})
MAX_FRAME_BYTES: int = 65536


@dataclass
class AudioFrame:
    sequence: int
    encoding: str
    sample_rate: int
    payload_bytes: bytes
    timestamp_ms: int
    direction: str  # "inbound" | "outbound"


@dataclass
class MediaGatewaySession:
    call_id: UUID
    tenant_id: UUID
    provider_call_id: str
    encoding: str = "mulaw"
    sample_rate: int = 8000
    state: MediaSessionState = MediaSessionState.CONNECTING
    inbound_frames_received: int = 0
    outbound_frames_sent: int = 0
    inbound_bytes_received: int = 0
    outbound_bytes_sent: int = 0
    barge_in_count: int = 0
    inbound_buffer: deque[AudioFrame] = field(default_factory=lambda: deque(maxlen=256))
    outbound_queue: deque[AudioFrame] = field(default_factory=lambda: deque(maxlen=256))
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "call_id": str(self.call_id),
            "organization_id": str(self.tenant_id),
            "provider_call_id": self.provider_call_id,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
            "state": self.state.value,
            "inbound_frames_received": self.inbound_frames_received,
            "outbound_frames_sent": self.outbound_frames_sent,
            "inbound_bytes_received": self.inbound_bytes_received,
            "outbound_bytes_sent": self.outbound_bytes_sent,
            "barge_in_count": self.barge_in_count,
            "outbound_queue_depth": len(self.outbound_queue),
            "connected_at": self.connected_at.isoformat(),
            "last_activity_at": self.last_activity_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


class MediaGatewayManager:
    """Manages active real-time media sessions and enforces frame/protocol invariants."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, MediaGatewaySession] = {}

    def open_session(
        self,
        *,
        call_id: UUID,
        tenant_id: UUID,
        provider_call_id: str,
        encoding: str = "mulaw",
        sample_rate: int = 8000,
    ) -> MediaGatewaySession:
        enc_norm = (encoding or "mulaw").strip().lower()
        if enc_norm not in SUPPORTED_ENCODINGS:
            raise MediaSessionError(
                f"Unsupported audio encoding: {encoding!r}",
                detail={"encoding": encoding, "supported": sorted(SUPPORTED_ENCODINGS)},
            )
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise MediaSessionError(
                f"Unsupported audio sample rate: {sample_rate}",
                detail={"sample_rate": sample_rate, "supported": sorted(SUPPORTED_SAMPLE_RATES)},
            )
        session = MediaGatewaySession(
            call_id=call_id,
            tenant_id=tenant_id,
            provider_call_id=provider_call_id,
            encoding=enc_norm,
            sample_rate=sample_rate,
            state=MediaSessionState.STREAMING,
        )
        self._sessions[call_id] = session
        return session

    def get_session(self, call_id: UUID) -> MediaGatewaySession | None:
        return self._sessions.get(call_id)

    def ingest_inbound_frame(
        self,
        call_id: UUID,
        *,
        payload: bytes | str,
        encoding: str | None = None,
        sample_rate: int | None = None,
        timestamp_ms: int = 0,
        speech_detected: bool = False,
    ) -> dict[str, Any]:
        session = self._require_active_session(call_id)
        raw_bytes = self._decode_payload(payload)

        if encoding and encoding.strip().lower() not in SUPPORTED_ENCODINGS:
            raise MediaSessionError(f"Unsupported audio encoding: {encoding!r}")
        if sample_rate is not None and sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise MediaSessionError(f"Unsupported audio sample rate: {sample_rate}")

        barge_in_triggered = False
        if speech_detected and (
            session.state == MediaSessionState.SPEAKING or len(session.outbound_queue) > 0
        ):
            self.trigger_barge_in(call_id, reason="caller_speech_detected")
            barge_in_triggered = True

        session.inbound_frames_received += 1
        session.inbound_bytes_received += len(raw_bytes)
        session.last_activity_at = datetime.utcnow()
        if session.state not in {MediaSessionState.SPEAKING, MediaSessionState.INTERRUPTED}:
            session.state = MediaSessionState.LISTENING

        frame = AudioFrame(
            sequence=session.inbound_frames_received,
            encoding=(encoding or session.encoding).lower(),
            sample_rate=sample_rate or session.sample_rate,
            payload_bytes=raw_bytes,
            timestamp_ms=timestamp_ms,
            direction="inbound",
        )
        session.inbound_buffer.append(frame)
        return {
            "sequence": frame.sequence,
            "bytes": len(raw_bytes),
            "state": session.state.value,
            "barge_in_triggered": barge_in_triggered,
        }

    def enqueue_outbound_frame(
        self,
        call_id: UUID,
        *,
        payload: bytes | str,
        timestamp_ms: int = 0,
    ) -> AudioFrame:
        session = self._require_active_session(call_id)
        raw_bytes = self._decode_payload(payload)
        session.outbound_frames_sent += 1
        session.outbound_bytes_sent += len(raw_bytes)
        session.state = MediaSessionState.SPEAKING
        session.last_activity_at = datetime.utcnow()

        frame = AudioFrame(
            sequence=session.outbound_frames_sent,
            encoding=session.encoding,
            sample_rate=session.sample_rate,
            payload_bytes=raw_bytes,
            timestamp_ms=timestamp_ms,
            direction="outbound",
        )
        session.outbound_queue.append(frame)
        return frame

    def trigger_barge_in(
        self,
        call_id: UUID,
        *,
        reason: str = "caller_barge_in",
    ) -> dict[str, Any]:
        session = self._require_active_session(call_id)
        flushed_frames = len(session.outbound_queue)
        session.outbound_queue.clear()
        session.barge_in_count += 1
        session.state = MediaSessionState.INTERRUPTED
        session.last_activity_at = datetime.utcnow()
        return {
            "call_id": str(call_id),
            "barge_in_triggered": True,
            "flushed_outbound_frames": flushed_frames,
            "barge_in_count": session.barge_in_count,
            "reason": reason,
            "state": session.state.value,
        }

    def close_session(self, call_id: UUID) -> dict[str, Any] | None:
        session = self._sessions.pop(call_id, None)
        if session is None:
            return None
        session.outbound_queue.clear()
        session.inbound_buffer.clear()
        session.state = MediaSessionState.DISCONNECTED
        session.closed_at = datetime.utcnow()
        return session.snapshot()

    def _require_active_session(self, call_id: UUID) -> MediaGatewaySession:
        session = self._sessions.get(call_id)
        if session is None or session.state == MediaSessionState.DISCONNECTED:
            raise MediaSessionError(
                f"No active media gateway session for call '{call_id}'.",
                detail={"call_id": str(call_id)},
            )
        return session

    @staticmethod
    def _decode_payload(payload: bytes | str) -> bytes:
        if isinstance(payload, bytes):
            raw = payload
        elif isinstance(payload, str):
            stripped = payload.strip()
            if not stripped:
                raise MediaSessionError("Audio frame payload cannot be empty.")
            try:
                raw = base64.b64decode(stripped, validate=True)
            except Exception as exc:
                raise MediaSessionError(
                    "Invalid base64 audio frame payload.",
                    detail={"error": str(exc)},
                ) from exc
        else:
            raise MediaSessionError("Unsupported audio frame payload type.")

        if len(raw) == 0:
            raise MediaSessionError("Decoded audio frame is empty.")
        if len(raw) > MAX_FRAME_BYTES:
            raise MediaSessionError(
                f"Audio frame exceeds maximum size ({len(raw)} > {MAX_FRAME_BYTES} bytes)."
            )
        return raw


media_gateway_manager = MediaGatewayManager()
