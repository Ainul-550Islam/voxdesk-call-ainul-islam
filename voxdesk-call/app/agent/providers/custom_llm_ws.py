"""Customer-hosted LLM over WebSocket (PART 12, P12-11).

Retell-compatible shape: the *customer* runs the model, we run the call. The contract is a small
set of JSON events in both directions, an HMAC signature on every frame, exponential-backoff
reconnects, and a hard 2 s budget for the first token — after which the pipeline fails over to the
configured fallback LLM rather than leaving the caller in silence.

The transport is injected (`send`), and every message is built by a pure function, so the whole
protocol is testable without a socket. That matters more here than anywhere else in the codebase:
this is the one integration the customer implements themselves, so the shape has to be pinned by
tests rather than described in prose.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Callable

#: Events the customer's server sends us.
INBOUND_EVENTS: tuple[str, ...] = ("config", "update_only", "response_required", "reminder_required")

#: Events we send the customer's server.
OUTBOUND_EVENTS: tuple[str, ...] = ("response", "tool_call", "end_call")

#: First token must arrive within this many seconds or we fail over.
FIRST_TOKEN_TIMEOUT_S = 2.0


class CustomLlmProtocolError(ValueError):
    """A frame that does not match the contract."""


class WebhookSignatureError(CustomLlmProtocolError):
    """The HMAC on an inbound frame did not verify."""


def sign_frame(body: bytes, secret: str) -> str:
    """Hex HMAC-SHA256 of the raw frame body."""
    if not secret:
        raise ValueError("a signing secret is required")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_frame(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time verification. Returns False rather than raising for a bad signature."""
    if not secret or not signature:
        return False
    expected = sign_frame(body, secret)
    return hmac.compare_digest(expected, signature.strip().lower())


@dataclass(frozen=True)
class ReconnectPolicy:
    """Exponential backoff with a cap, plus jitter that stays inside the cap."""

    initial_s: float = 0.5
    factor: float = 2.0
    max_s: float = 8.0
    max_attempts: int = 5

    def delay_for(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt numbers start at 1")
        delay = self.initial_s * (self.factor ** (attempt - 1))
        return round(min(delay, self.max_s), 3)

    def exhausted(self, attempt: int) -> bool:
        return attempt > self.max_attempts

    def sequence(self) -> list[float]:
        return [self.delay_for(attempt) for attempt in range(1, self.max_attempts + 1)]


@dataclass(frozen=True)
class InboundEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_response(self) -> bool:
        return self.name in {"response_required", "reminder_required"}

    @property
    def is_control_only(self) -> bool:
        return self.name in {"config", "update_only"}


def parse_inbound(raw: str | bytes | dict[str, Any], *, secret: str = "", signature: str = "") -> InboundEvent:
    """Parse and (when a secret is configured) authenticate one inbound frame.

    `update_only` carries a config delta with no turn to answer; `response_required` and
    `reminder_required` both demand an assistant turn — the reminder is Retell's way of saying
    "the caller is still waiting", so dropping it leaves dead air.
    """
    body = raw.encode("utf-8") if isinstance(raw, str) else (
        json.dumps(raw, separators=(",", ":")).encode("utf-8") if isinstance(raw, dict) else raw
    )
    if secret and not verify_frame(body, signature, secret):
        raise WebhookSignatureError("inbound frame signature did not verify")

    data = json.loads(body)
    name = str(data.get("event") or data.get("type") or "").strip().lower()
    if name not in INBOUND_EVENTS:
        raise CustomLlmProtocolError(
            f"unknown inbound event {name!r}; expected one of {list(INBOUND_EVENTS)}"
        )
    return InboundEvent(name=name, payload={k: v for k, v in data.items() if k not in {"event", "type"}})


class FirstTokenWatchdog:
    """Tracks the 2 s first-token budget with an injectable clock."""

    def __init__(self, timeout_s: float = FIRST_TOKEN_TIMEOUT_S, *, clock: Callable[[], float] | None = None) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.timeout_s = timeout_s
        self._clock = clock or (lambda: 0.0)
        self._started: float | None = None

    def start(self, at: float | None = None) -> None:
        self._started = self._clock() if at is None else float(at)

    def expired(self, at: float | None = None) -> bool:
        if self._started is None:
            return False
        now = self._clock() if at is None else float(at)
        return (now - self._started) >= self.timeout_s

    def on_token(self, at: float | None = None) -> str:
        """`ok` if the token arrived in budget, `failover` if it did not."""
        if self._started is None:
            self.start(at)
            return "ok"
        return "failover" if self.expired(at) else "ok"


@dataclass(frozen=True)
class CustomLlmConfig:
    """The `config` frame we send when the socket opens."""

    url: str
    model: str = "customer-model"
    voice_id: str = ""
    transcript_id: str = ""
    tools: tuple[dict[str, Any], ...] = ()
    variables: dict[str, Any] = field(default_factory=dict)
    response_timeout_ms: int = 2000

    def to_frame(self) -> dict[str, Any]:
        return {
            "event": "config",
            "config": {
                "model": self.model,
                "voice_id": self.voice_id,
                "transcript_id": self.transcript_id,
                "tools": list(self.tools),
                "variables": dict(self.variables),
                "response_timeout_ms": int(self.response_timeout_ms),
            },
        }


def response_frame(
    *,
    response_id: str,
    content: str,
    content_complete: bool = True,
    end_call: bool = False,
) -> dict[str, Any]:
    return {
        "event": "response",
        "response_id": response_id,
        "content": content,
        "content_complete": content_complete,
        "end_call": end_call,
    }


def tool_call_frame(
    *,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    if not name:
        raise CustomLlmProtocolError("a tool call needs a name")
    return {
        "event": "tool_call",
        "call": {
            "id": call_id,
            "name": name,
            "arguments": arguments,
            "timeout_seconds": timeout_seconds,
        },
    }


def end_call_frame(*, reason: str = "agent_ended") -> dict[str, Any]:
    return {"event": "end_call", "reason": reason}


async def send_frame(send: Callable[[str], Any], frame: dict[str, Any]) -> str:
    """Send one frame; returns the exact bytes (as text) that went out, for the audit log."""
    text = json.dumps(frame, separators=(",", ":"))
    await send(text)
    return text
