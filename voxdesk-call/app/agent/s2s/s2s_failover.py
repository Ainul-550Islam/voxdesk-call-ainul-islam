"""Failover from speech-to-speech to the cascaded STT -> LLM -> TTS pipeline.

A realtime provider can fail in the middle of a live call: the socket
drops, the provider rate-limits, the session exceeds its ceiling, the cost
budget is spent. Ending the call is the wrong answer -- the caller is
already on the line.

This module rebuilds the *cascaded* pipeline (STT -> LLM -> TTS) with the
conversation history carried over, so the assistant keeps its place in the
conversation instead of starting again. Every failover is counted in
``voxdesk_s2s_failover_total{reason}`` so an operator can see how often the
realtime path actually failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prometheus_client import Counter

from app.agent.s2s.s2s_session import (
    S2SBudgetExceeded,
    S2SError,
    S2SProviderError,
    S2SSessionTimeout,
)
from app.core.logging import log

#: Failover counter. The label set is closed on purpose: a reason code and
#: nothing else, so the series count stays bounded.
S2S_FAILOVER_TOTAL = Counter(
    "voxdesk_s2s_failover_total",
    "Speech-to-speech sessions that fell back to the cascaded pipeline",
    ["reason"],
)

#: The closed set of failover reasons.
REASON_PROVIDER_ERROR = "provider_error"
REASON_TIMEOUT = "timeout"
REASON_BUDGET_EXCEEDED = "budget_exceeded"
REASON_CONNECTION_LOST = "connection_lost"
REASON_DISABLED = "fallback_disabled"


def failover_reason(error: BaseException | str) -> str:
    """Map a failure onto the closed reason vocabulary."""
    if isinstance(error, S2SSessionTimeout):
        return REASON_TIMEOUT
    if isinstance(error, S2SBudgetExceeded):
        return REASON_BUDGET_EXCEEDED
    if isinstance(error, S2SProviderError):
        return REASON_PROVIDER_ERROR
    if isinstance(error, S2SError):
        return REASON_PROVIDER_ERROR
    text = str(error or "")
    if any(
        token in text.lower()
        for token in ("connect", "socket", "closed", "unreachable")
    ):
        return REASON_CONNECTION_LOST
    if "timeout" in text.lower():
        return REASON_TIMEOUT
    if "budget" in text.lower():
        return REASON_BUDGET_EXCEEDED
    return REASON_PROVIDER_ERROR


@dataclass
class CascadedFallback:
    """The cascaded services that replaced a failed speech-to-speech session."""

    stt: Any = None
    llm: Any = None
    tts: Any = None
    #: The governed LLM preparation (prompt, timeout, blocked output).
    prepared: Any = None
    blocked_output: tuple[str, ...] = ()
    system_prompt: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    provider: str = ""
    llm_provider: str = ""
    llm_model: str = ""

    def context(self):
        """An LLM context seeded with the carried-over conversation."""
        from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext

        messages: list[dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        for entry in self.history:
            role = "user" if entry.get("role") == "user" else "assistant"
            content = str(entry.get("content") or "")
            if content:
                messages.append({"role": role, "content": content})
        return OpenAILLMContext(messages=messages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "history_turns": len(self.history),
            "has_stt": self.stt is not None,
            "has_llm": self.llm is not None,
            "has_tts": self.tts is not None,
        }


async def build_cascaded_fallback(
    *,
    session,
    tenant,
    call,
    runtime_config,
    history: list[dict[str, str]] | None = None,
    provider: str = "",
) -> CascadedFallback:
    """Build the cascaded services, seeded with the conversation so far."""
    from app.agent.stt import build_stt_for_runtime
    from app.agent.tts import build_tts_for_runtime
    from app.ai.runtime import voice_llm

    llm, prepared = await voice_llm(
        session,
        tenant,
        call,
        temperature=getattr(runtime_config, "temperature", None),
        max_tokens=getattr(runtime_config, "max_tokens", None) or 110,
    )
    stt = build_stt_for_runtime(runtime_config)
    tts = build_tts_for_runtime(runtime_config)
    choice = getattr(prepared, "choice", None)
    return CascadedFallback(
        stt=stt,
        llm=llm,
        tts=tts,
        prepared=prepared,
        blocked_output=tuple(getattr(prepared, "blocked_output", ()) or ()),
        system_prompt=str(getattr(prepared, "system_prompt", "") or ""),
        history=list(history or []),
        provider=provider,
        llm_provider=str(getattr(choice, "provider", "") or ""),
        llm_model=str(getattr(choice, "model", "") or ""),
    )


async def failover_to_cascaded(
    *,
    error: BaseException | str,
    session,
    tenant,
    call,
    runtime_config,
    history: list[dict[str, str]] | None = None,
    fallback_enabled: bool = True,
    provider: str = "",
) -> CascadedFallback | None:
    """Fail over to the cascaded pipeline; returns ``None`` when disabled.

    The caller keeps the call alive. ``None`` means "fallback is off" and
    the caller must end the call deliberately.
    """
    reason = failover_reason(error)
    if not fallback_enabled:
        S2S_FAILOVER_TOTAL.labels(reason=REASON_DISABLED).inc()
        log.warning("s2s.failover.disabled", provider=provider, reason=reason)
        return None
    S2S_FAILOVER_TOTAL.labels(reason=reason).inc()
    log.warning("s2s.failover.engaged", provider=provider, reason=reason)
    fallback = await build_cascaded_fallback(
        session=session,
        tenant=tenant,
        call=call,
        runtime_config=runtime_config,
        history=history,
        provider=provider,
    )
    details = fallback.as_dict()
    details["provider"] = provider or details.get("provider", "")
    log.info("s2s.failover.ready", **details)
    return fallback


__all__ = [
    "CascadedFallback",
    "REASON_BUDGET_EXCEEDED",
    "REASON_CONNECTION_LOST",
    "REASON_DISABLED",
    "REASON_PROVIDER_ERROR",
    "REASON_TIMEOUT",
    "S2S_FAILOVER_TOTAL",
    "build_cascaded_fallback",
    "failover_reason",
    "failover_to_cascaded",
]
