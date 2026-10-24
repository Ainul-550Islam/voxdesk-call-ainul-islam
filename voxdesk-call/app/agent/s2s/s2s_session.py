"""Provider-neutral speech-to-speech session contract.

A speech-to-speech provider is a *live* WebSocket: audio goes in, audio and
transcripts come out, and the model may call tools in the middle of a turn.
The two providers VoxDesk supports (OpenAI Realtime and Gemini Live) speak
different wire protocols, so the rest of the product talks to this
provider-neutral interface instead:

* :class:`S2SSession` -- the lifecycle (``start`` / ``send_audio`` /
  ``interrupt`` / ``close``) plus an async event stream.
* :class:`S2SEvent` -- one thing that happened (audio out, transcript in,
  transcript out, tool call, interruption, turn end, error).

Everything above this layer -- the Pipecat processor, the tool bridge, the
cost meter, the failover path -- is written against these two types only.
"""

from __future__ import annotations

import asyncio
import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterable


class S2SEventType(str, enum.Enum):
    """The kinds of event a speech-to-speech session emits."""

    AUDIO_OUT = "audio_out"
    TRANSCRIPT_IN = "transcript_in"
    TRANSCRIPT_OUT = "transcript_out"
    TOOL_CALL = "tool_call"
    INTERRUPTED = "interrupted"
    TURN_END = "turn_end"
    ERROR = "error"


@dataclass(frozen=True)
class S2SEvent:
    """One event from a speech-to-speech session."""

    type: S2SEventType
    audio: bytes = b""
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    sample_rate: int = 16000
    provider: str = ""
    model: str = ""
    error: str = ""
    final: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "text": self.text,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "sample_rate": self.sample_rate,
            "provider": self.provider,
            "model": self.model,
            "error": self.error,
            "final": self.final,
            "audio_bytes": len(self.audio),
        }


class S2SError(Exception):
    """Base class for speech-to-speech failures."""

    provider = ""
    category = "unknown"
    retryable = False

    def __init__(self, message: str, *, provider: str = "", category: str = "", retryable: bool = False):
        super().__init__(message)
        self.message = message
        if provider:
            self.provider = provider
        if category:
            self.category = category
        self.retryable = retryable


class S2SProviderError(S2SError):
    """The provider refused or failed the session."""

    category = "provider_error"


class S2SConfigurationError(S2SError):
    """The session cannot start: a credential or parameter is missing."""

    category = "configuration_error"


class S2SSessionTimeout(S2SError):
    """The session exceeded its maximum duration."""

    category = "timeout"


class S2SBudgetExceeded(S2SError):
    """The session exceeded its cost budget."""

    category = "budget_exceeded"


@dataclass
class ReconnectPolicy:
    """How a dropped provider socket is re-established.

    Reconnection is bounded: a provider that is down must not turn into an
    infinite retry loop inside a live call. After ``max_attempts`` the
    session raises, and the failover layer rebuilds the cascaded pipeline.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0

    def delay_for(self, attempt: int) -> float:
        """The backoff delay before attempt ``attempt`` (1-based)."""
        if attempt <= 1:
            return 0.0
        delay = self.base_delay_seconds * (2 ** (attempt - 2))
        return min(delay, self.max_delay_seconds)


class S2SSession(ABC):
    """One live speech-to-speech conversation."""

    provider: str = ""
    model: str = ""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "",
        system_prompt: str = "",
        voice: str = "alloy",
        tools: Iterable[dict[str, Any]] | None = None,
        sample_rate: int = 16000,
        vad_mode: str = "server",
        max_session_seconds: int = 600,
        reconnect_policy: ReconnectPolicy | None = None,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.voice = voice
        self.tools = list(tools or [])
        self.sample_rate = sample_rate
        self.vad_mode = vad_mode
        self.max_session_seconds = int(max_session_seconds)
        self.reconnect_policy = reconnect_policy or ReconnectPolicy()
        self.connect_factory = connect_factory
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self._queue: asyncio.Queue[S2SEvent | None] = asyncio.Queue()
        self._closed = False

    # -- lifecycle -------------------------------------------------------
    @abstractmethod
    async def start(self) -> None:
        """Open the provider session and configure it."""

    async def connect(self) -> None:
        """Open the provider socket eagerly, before the first audio frame.

        The live call path uses this as a pre-flight: a provider that cannot
        be reached fails over to the cascaded pipeline *before* the caller
        hears anything. The default implementation is a no-op, so a provider
        that connects lazily simply starts on the first frame.
        """
        return None

    @abstractmethod
    async def send_audio(self, pcm: bytes, *, sample_rate: int) -> None:
        """Send caller audio to the provider."""

    @abstractmethod
    async def interrupt(self) -> None:
        """Cancel whatever the provider is currently saying (barge-in)."""

    @abstractmethod
    async def close(self) -> None:
        """Close the provider session."""

    @abstractmethod
    def events(self) -> AsyncIterator[S2SEvent]:
        """The event stream for this session."""

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        """Return a tool result to the provider."""
        raise S2SProviderError(
            "This provider does not accept tool results", provider=self.provider
        )

    # -- helpers ---------------------------------------------------------
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at if self.ended_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    def session_expired(self, now: float | None = None) -> bool:
        """True when the session has run longer than its ceiling."""
        if self.max_session_seconds <= 0:
            return False
        current = now if now is not None else time.monotonic()
        if self.started_at is None:
            return False
        return (current - self.started_at) > self.max_session_seconds

    def emit(self, event: S2SEvent) -> None:
        """Publish an event to the consumer."""
        self._queue.put_nowait(event)

    def finish(self) -> None:
        """Signal the end of the event stream."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    @property
    def closed(self) -> bool:
        return self._closed


__all__ = [
    "ReconnectPolicy",
    "S2SBudgetExceeded",
    "S2SConfigurationError",
    "S2SEvent",
    "S2SEventType",
    "S2SError",
    "S2SProviderError",
    "S2SSession",
    "S2SSessionTimeout",
]
