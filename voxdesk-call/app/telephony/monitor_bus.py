# File: app/telephony/monitor_bus.py — Redis pub/sub fan-out of per-call audio frames and transcript events
"""Redis pub/sub fan-out of per-call audio frames and transcript events (Part 4 / Gate G5).

Producers:
  - ``app.agent.monitor_tap.MonitorTap`` in the live voice pipeline

Consumers:
  - ``app.api.ws.monitor_ws`` (`/ws/monitor/{call_id}`)

Design guarantees:
  - Zero overhead when no monitoring sessions or WebSocket subscribers exist for a call.
  - Bounded per-subscriber queues with drop-oldest backpressure so a slow supervisor
    socket never blocks the real-time voice pipeline.
  - Cross-worker Redis pub/sub fan-out (`monitor:call:{call_id}`) and Redis active-session
    tracking (`monitor:sessions:{call_id}`) when Redis is configured.
  - Zero PII in logs: never logs audio payloads, transcript text, whisper text, or phone numbers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Coroutine

from app.core.logging import log

DEFAULT_QUEUE_MAXSIZE = 128
MAX_SUBSCRIBERS_PER_CALL = 5
SESSION_KEY_PREFIX = "monitor:sessions:"
CHANNEL_PREFIX = "monitor:call:"
GUIDANCE_CHANNEL_PREFIX = "monitor:guidance:"


class MonitorCapacityError(RuntimeError):
    """Raised when a call already has the maximum number of concurrent supervisor listeners."""

    def __init__(self, call_id: str, max_subscribers: int) -> None:
        super().__init__(
            f"Call {call_id} already has the maximum of {max_subscribers} concurrent monitor subscribers"
        )
        self.call_id = call_id
        self.max_subscribers = max_subscribers


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class MonitorEvent:
    """Single fan-out event emitted for a monitored call."""

    kind: str  # "audio" | "transcript" | "guidance" | "call_ended" | "takeover" | "control"
    call_id: str
    speaker: str = "caller"  # "caller" | "agent" | "system" | "supervisor"
    pcm_bytes: bytes = b""
    sample_rate: int = 16000
    num_channels: int = 1
    text: str = ""
    is_final: bool = True
    seq: int = 0
    timestamp: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_ws_json(self, *, include_audio_b64: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.kind,
            "call_id": self.call_id,
            "speaker": self.speaker,
            "seq": self.seq,
            "timestamp": self.timestamp,
        }
        if self.kind == "audio":
            payload["sample_rate"] = self.sample_rate
            payload["num_channels"] = self.num_channels
            payload["byte_length"] = len(self.pcm_bytes)
            if include_audio_b64 and self.pcm_bytes:
                payload["pcm_b64"] = base64.b64encode(self.pcm_bytes).decode("ascii")
        elif self.kind in ("transcript", "guidance"):
            payload["text"] = self.text
            payload["is_final"] = self.is_final
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def to_redis_payload(self) -> str:
        data = {
            "kind": self.kind,
            "call_id": self.call_id,
            "speaker": self.speaker,
            "sample_rate": self.sample_rate,
            "num_channels": self.num_channels,
            "text": self.text,
            "is_final": self.is_final,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
        if self.pcm_bytes:
            data["pcm_b64"] = base64.b64encode(self.pcm_bytes).decode("ascii")
        return json.dumps(data, separators=(",", ":"))

    @classmethod
    def from_redis_payload(cls, raw: str | bytes) -> "MonitorEvent":
        text_raw = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        data = json.loads(text_raw)
        pcm_b64 = data.get("pcm_b64") or ""
        pcm_bytes = base64.b64decode(pcm_b64) if pcm_b64 else b""
        return cls(
            kind=str(data.get("kind") or "control"),
            call_id=str(data.get("call_id") or ""),
            speaker=str(data.get("speaker") or "caller"),
            pcm_bytes=pcm_bytes,
            sample_rate=int(data.get("sample_rate") or 16000),
            num_channels=int(data.get("num_channels") or 1),
            text=str(data.get("text") or ""),
            is_final=bool(data.get("is_final", True)),
            seq=int(data.get("seq") or 0),
            timestamp=str(data.get("timestamp") or _now_iso()),
            metadata=dict(data.get("metadata") or {}),
        )


class MonitorSubscription:
    """Bounded per-supervisor event queue with drop-oldest backpressure."""

    def __init__(
        self,
        bus: "MonitorBus",
        call_id: str,
        *,
        subscription_id: str | None = None,
        supervisor_id: str = "",
        session_id: str = "",
        max_queue_size: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be >= 1")
        self._bus = bus
        self.call_id = str(call_id)
        self.subscription_id = subscription_id or str(uuid.uuid4())
        self.supervisor_id = str(supervisor_id or "")
        self.session_id = str(session_id or "")
        self.max_queue_size = max_queue_size
        self._queue: asyncio.Queue[MonitorEvent] = asyncio.Queue(maxsize=max_queue_size)
        self.dropped_count: int = 0
        self.received_count: int = 0
        self.closed: bool = False

    @property
    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_frames(self) -> int:
        return self.dropped_count

    def enqueue(self, event: MonitorEvent) -> bool:
        """Non-blocking enqueue with drop-oldest backpressure.

        Returns ``True`` if an older frame had to be dropped to make room.
        """
        if self.closed:
            return False
        dropped = False
        while self._queue.full():
            try:
                self._queue.get_nowait()
                self.dropped_count += 1
                dropped = True
            except asyncio.QueueEmpty:
                break
        try:
            self._queue.put_nowait(event)
            self.received_count += 1
        except asyncio.QueueFull:
            self.dropped_count += 1
            dropped = True
        return dropped

    async def get(self, timeout: float | None = None) -> MonitorEvent:
        if timeout is not None and timeout > 0:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def get_nowait(self) -> MonitorEvent:
        return self._queue.get_nowait()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self._bus.unsubscribe(self)

    async def __aenter__(self) -> "MonitorSubscription":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def __aiter__(self) -> AsyncIterator[MonitorEvent]:
        return self

    async def __anext__(self) -> MonitorEvent:
        if self.closed and self._queue.empty():
            raise StopAsyncIteration
        event = await self._queue.get()
        return event


GuidanceCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]]


class MonitorBus:
    """Fan-out bus for per-call audio frames, transcripts, and whisper-to-AI guidance."""

    def __init__(
        self,
        *,
        redis_client: Any = None,
        redis_getter: Callable[[], Any] | None = None,
        default_queue_size: int = DEFAULT_QUEUE_MAXSIZE,
        max_subscribers_per_call: int = MAX_SUBSCRIBERS_PER_CALL,
    ) -> None:
        self._redis = redis_client if redis_client is not None else (redis_getter() if redis_getter else None)
        self.default_queue_size = max(1, int(default_queue_size))
        self.max_subscribers_per_call = max(1, int(max_subscribers_per_call))
        self._instance_id = uuid.uuid4().hex
        self._subscribers: dict[str, dict[str, MonitorSubscription]] = {}
        self._active_sessions: dict[str, dict[str, dict[str, Any]]] = {}
        self._guidance_handlers: dict[str, GuidanceCallback] = {}
        self._seq_by_call: dict[str, int] = {}
        self._dropped_by_call: dict[str, int] = {}
        self._published_by_call: dict[str, int] = {}
        self._redis_tasks: dict[str, asyncio.Task[None]] = {}

    def set_redis_client(self, redis_client: Any) -> None:
        self._redis = redis_client

    def has_listeners(self, call_id: str | uuid.UUID) -> bool:
        """Fast synchronous check used by ``MonitorTap`` on every pipeline frame.

        Returns ``True`` only when at least one WebSocket subscriber or active
        monitoring session exists for ``call_id``.
        """
        cid = str(call_id)
        subs = self._subscribers.get(cid)
        if subs:
            return True
        sessions = self._active_sessions.get(cid)
        if not sessions:
            return False
        now_ts = time.time()
        expired = [
            sid
            for sid, info in sessions.items()
            if float(info.get("expires_at_epoch", now_ts + 1.0)) <= now_ts
        ]
        for sid in expired:
            sessions.pop(sid, None)
        if not sessions:
            self._active_sessions.pop(cid, None)
            return False
        return True

    async def has_active_listeners(self, call_id: str | uuid.UUID) -> bool:
        """Async check that also consults Redis active-session state across workers."""
        cid = str(call_id)
        if self.has_listeners(cid):
            return True
        if self._redis is not None:
            try:
                count = await self._redis.hlen(f"{SESSION_KEY_PREFIX}{cid}")
                if count and int(count) > 0:
                    return True
            except Exception as exc:
                log.warning(
                    "monitor_bus.redis_hlen_failed",
                    call_id=cid,
                    error_type=type(exc).__name__,
                )
        return False

    def subscriber_count(self, call_id: str | uuid.UUID) -> int:
        cid = str(call_id)
        return len(self._subscribers.get(cid) or {})

    def active_session_count(self, call_id: str | uuid.UUID) -> int:
        cid = str(call_id)
        self.has_listeners(cid)  # prune expired
        return len(self._active_sessions.get(cid) or {})

    def dropped_frames_count(self, call_id: str | uuid.UUID) -> int:
        return int(self._dropped_by_call.get(str(call_id), 0))

    def published_events_count(self, call_id: str | uuid.UUID) -> int:
        return int(self._published_by_call.get(str(call_id), 0))

    async def register_session(
        self,
        call_id: str | uuid.UUID,
        session_id: str | uuid.UUID,
        *,
        supervisor_id: str | uuid.UUID = "",
        mode: str = "listen",
        ttl_seconds: int = 3600,
    ) -> None:
        cid = str(call_id)
        sid = str(session_id)
        now_ts = time.time()
        expires_epoch = now_ts + max(10, int(ttl_seconds))
        info = {
            "session_id": sid,
            "supervisor_id": str(supervisor_id),
            "mode": str(mode),
            "updated_at": _now_iso(),
            "expires_at_epoch": expires_epoch,
        }
        self._active_sessions.setdefault(cid, {})[sid] = info
        if self._redis is not None:
            try:
                key = f"{SESSION_KEY_PREFIX}{cid}"
                await self._redis.hset(key, sid, json.dumps(info, separators=(",", ":")))
                await self._redis.expire(key, max(10, int(ttl_seconds)))
            except Exception as exc:
                log.warning(
                    "monitor_bus.redis_register_session_failed",
                    call_id=cid,
                    session_id=sid,
                    error_type=type(exc).__name__,
                )
        log.info(
            "monitor_bus.session_registered",
            call_id=cid,
            session_id=sid,
            mode=mode,
            active_sessions=len(self._active_sessions.get(cid) or {}),
        )

    async def heartbeat_session(
        self,
        call_id: str | uuid.UUID,
        session_id: str | uuid.UUID,
        *,
        ttl_seconds: int = 3600,
    ) -> bool:
        cid = str(call_id)
        sid = str(session_id)
        sessions = self._active_sessions.get(cid) or {}
        info = sessions.get(sid)
        if info is None:
            return False
        info["updated_at"] = _now_iso()
        info["expires_at_epoch"] = time.time() + max(10, int(ttl_seconds))
        if self._redis is not None:
            try:
                key = f"{SESSION_KEY_PREFIX}{cid}"
                await self._redis.hset(key, sid, json.dumps(info, separators=(",", ":")))
                await self._redis.expire(key, max(10, int(ttl_seconds)))
            except Exception as exc:
                log.warning(
                    "monitor_bus.redis_heartbeat_failed",
                    call_id=cid,
                    session_id=sid,
                    error_type=type(exc).__name__,
                )
        return True

    async def unregister_session(
        self,
        call_id: str | uuid.UUID,
        session_id: str | uuid.UUID,
    ) -> None:
        cid = str(call_id)
        sid = str(session_id)
        sessions = self._active_sessions.get(cid)
        if sessions is not None:
            sessions.pop(sid, None)
            if not sessions:
                self._active_sessions.pop(cid, None)
        if self._redis is not None:
            try:
                key = f"{SESSION_KEY_PREFIX}{cid}"
                await self._redis.hdel(key, sid)
            except Exception as exc:
                log.warning(
                    "monitor_bus.redis_unregister_session_failed",
                    call_id=cid,
                    session_id=sid,
                    error_type=type(exc).__name__,
                )
        log.info(
            "monitor_bus.session_unregistered",
            call_id=cid,
            session_id=sid,
            active_sessions=len(self._active_sessions.get(cid) or {}),
        )

    def register_guidance_handler(
        self,
        call_id: str | uuid.UUID,
        handler: GuidanceCallback,
    ) -> None:
        self._guidance_handlers[str(call_id)] = handler

    def unregister_guidance_handler(self, call_id: str | uuid.UUID) -> None:
        self._guidance_handlers.pop(str(call_id), None)

    async def subscribe(
        self,
        call_id: str | uuid.UUID,
        *,
        supervisor_id: str | uuid.UUID = "",
        session_id: str | uuid.UUID = "",
        max_queue_size: int | None = None,
        max_subscribers_per_call: int | None = None,
    ) -> MonitorSubscription:
        cid = str(call_id)
        cap = (
            int(max_subscribers_per_call)
            if max_subscribers_per_call is not None
            else self.max_subscribers_per_call
        )
        call_subs = self._subscribers.setdefault(cid, {})
        if len(call_subs) >= cap:
            raise MonitorCapacityError(cid, cap)

        sub = MonitorSubscription(
            self,
            cid,
            supervisor_id=str(supervisor_id or ""),
            session_id=str(session_id or ""),
            max_queue_size=max_queue_size or self.default_queue_size,
        )
        call_subs[sub.subscription_id] = sub
        log.info(
            "monitor_bus.subscribed",
            call_id=cid,
            subscription_id=sub.subscription_id,
            subscriber_count=len(call_subs),
        )
        return sub

    async def unsubscribe(self, subscription: MonitorSubscription) -> None:
        cid = subscription.call_id
        call_subs = self._subscribers.get(cid)
        if call_subs is not None:
            call_subs.pop(subscription.subscription_id, None)
            if not call_subs:
                self._subscribers.pop(cid, None)
        log.info(
            "monitor_bus.unsubscribed",
            call_id=cid,
            subscription_id=subscription.subscription_id,
            subscriber_count=len(self._subscribers.get(cid) or {}),
        )

    def _next_seq(self, call_id: str) -> int:
        nxt = self._seq_by_call.get(call_id, 0) + 1
        self._seq_by_call[call_id] = nxt
        return nxt

    async def _fanout_event(self, event: MonitorEvent) -> int:
        cid = event.call_id
        call_subs = list((self._subscribers.get(cid) or {}).values())
        delivered = 0
        for sub in call_subs:
            dropped = sub.enqueue(event)
            if dropped:
                self._dropped_by_call[cid] = self._dropped_by_call.get(cid, 0) + 1
                log.warning(
                    "monitor_bus.frame_dropped",
                    call_id=cid,
                    subscription_id=sub.subscription_id,
                    kind=event.kind,
                    dropped_total=self._dropped_by_call[cid],
                )
            delivered += 1

        if self._redis is not None:
            try:
                await self._redis.publish(
                    f"{CHANNEL_PREFIX}{cid}",
                    event.to_redis_payload(),
                )
            except Exception as exc:
                log.warning(
                    "monitor_bus.redis_publish_failed",
                    call_id=cid,
                    kind=event.kind,
                    error_type=type(exc).__name__,
                )

        self._published_by_call[cid] = self._published_by_call.get(cid, 0) + 1
        return delivered

    async def publish_audio(
        self,
        call_id: str | uuid.UUID,
        pcm_bytes: bytes,
        *,
        speaker: str = "caller",
        sample_rate: int = 16000,
        num_channels: int = 1,
    ) -> int:
        """Publish a caller or agent PCM16 frame if and only if listeners exist."""
        cid = str(call_id)
        if not pcm_bytes or not self.has_listeners(cid):
            return 0
        event = MonitorEvent(
            kind="audio",
            call_id=cid,
            speaker=speaker,
            pcm_bytes=bytes(pcm_bytes),
            sample_rate=int(sample_rate),
            num_channels=int(num_channels),
            seq=self._next_seq(cid),
        )
        return await self._fanout_event(event)

    async def publish_transcript(
        self,
        call_id: str | uuid.UUID,
        text: str,
        *,
        speaker: str = "caller",
        is_final: bool = True,
        seq: int | None = None,
    ) -> int:
        """Publish a transcript event if and only if listeners exist. Never logs transcript PII."""
        cid = str(call_id)
        if not text or not text.strip() or not self.has_listeners(cid):
            return 0
        event = MonitorEvent(
            kind="transcript",
            call_id=cid,
            speaker=speaker,
            text=text,
            is_final=bool(is_final),
            seq=int(seq) if seq is not None else self._next_seq(cid),
        )
        return await self._fanout_event(event)

    async def publish_guidance(
        self,
        call_id: str | uuid.UUID,
        text: str,
        *,
        supervisor_id: str | uuid.UUID = "",
        session_id: str | uuid.UUID = "",
        run_llm: bool = True,
    ) -> bool:
        """Deliver whisper-to-AI guidance to the active pipeline tap and fan out metadata."""
        cid = str(call_id)
        cleaned = (text or "").strip()
        if not cleaned:
            return False

        delivered_to_tap = False
        handler = self._guidance_handlers.get(cid)
        if handler is not None:
            await handler(
                cleaned,
                {
                    "supervisor_id": str(supervisor_id or ""),
                    "session_id": str(session_id or ""),
                    "run_llm": bool(run_llm),
                },
            )
            delivered_to_tap = True

        if self._redis is not None:
            try:
                await self._redis.publish(
                    f"{GUIDANCE_CHANNEL_PREFIX}{cid}",
                    json.dumps(
                        {
                            "call_id": cid,
                            "text": cleaned,
                            "supervisor_id": str(supervisor_id or ""),
                            "session_id": str(session_id or ""),
                            "run_llm": bool(run_llm),
                            "origin_instance": self._instance_id,
                            "timestamp": _now_iso(),
                        },
                        separators=(",", ":"),
                    ),
                )
            except Exception as exc:
                log.warning(
                    "monitor_bus.redis_guidance_publish_failed",
                    call_id=cid,
                    error_type=type(exc).__name__,
                )

        if self.has_listeners(cid):
            event = MonitorEvent(
                kind="guidance",
                call_id=cid,
                speaker="supervisor",
                text=cleaned,
                is_final=True,
                seq=self._next_seq(cid),
                metadata={
                    "supervisor_id": str(supervisor_id or ""),
                    "session_id": str(session_id or ""),
                    "delivered_to_tap": delivered_to_tap,
                },
            )
            await self._fanout_event(event)

        log.info(
            "monitor_bus.guidance_published",
            call_id=cid,
            session_id=str(session_id or ""),
            delivered_to_tap=delivered_to_tap,
            char_length=len(cleaned),
        )
        return delivered_to_tap

    async def publish_call_ended(
        self,
        call_id: str | uuid.UUID,
        *,
        reason: str = "completed",
    ) -> int:
        """Notify all active supervisor subscribers that the call has ended."""
        cid = str(call_id)
        if not self.has_listeners(cid):
            self._active_sessions.pop(cid, None)
            return 0
        event = MonitorEvent(
            kind="call_ended",
            call_id=cid,
            speaker="system",
            seq=self._next_seq(cid),
            metadata={"reason": reason},
        )
        delivered = await self._fanout_event(event)
        self._active_sessions.pop(cid, None)
        if self._redis is not None:
            try:
                await self._redis.delete(f"{SESSION_KEY_PREFIX}{cid}")
            except Exception:
                __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
                pass
        return delivered

    def reset(self, call_id: str | uuid.UUID | None = None) -> None:
        """Clear bus state for a single call or all calls (used by tests and cleanup)."""
        if call_id is not None:
            cid = str(call_id)
            self._subscribers.pop(cid, None)
            self._active_sessions.pop(cid, None)
            self._guidance_handlers.pop(cid, None)
            self._seq_by_call.pop(cid, None)
            self._dropped_by_call.pop(cid, None)
            self._published_by_call.pop(cid, None)
            return
        self._subscribers.clear()
        self._active_sessions.clear()
        self._guidance_handlers.clear()
        self._seq_by_call.clear()
        self._dropped_by_call.clear()
        self._published_by_call.clear()


monitor_bus = MonitorBus()


def get_monitor_bus() -> MonitorBus:
    return monitor_bus
