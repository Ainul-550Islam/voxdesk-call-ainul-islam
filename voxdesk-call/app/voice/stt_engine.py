"""STT orchestration with bounded retries and replay-safe failover."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator

from app.agent.errors import ProviderConfigurationError, ProviderError
from app.agent.provider_observability import record_provider_error
from app.agent.stt_stream import STTEvent, STTStreamRequest
from app.core.config import settings
from app.core.logging import log
from app.voice.provider_registry import VoiceProviderRegistry, build_voice_provider_registry


@dataclass(frozen=True)
class STTRuntimePolicy:
    provider: str = ""
    fallback_provider: str = ""
    max_retries: int = 1
    backoff_seconds: float = 0.15
    allow_failover: bool = True


class STTEngine:
    """One bounded stream facade over the configured STT providers.

    A retry/failover of a live stream is allowed only when the caller supplies
    ``STTStreamRequest.replay_audio``. This prevents the engine from silently
    restarting a consumed microphone stream and dropping audio.
    """

    def __init__(self, registry: VoiceProviderRegistry | None = None, *, max_concurrent: int = 16) -> None:
        self.registry = registry or build_voice_provider_registry()
        self._concurrency = asyncio.Semaphore(max(1, min(max_concurrent, 64)))

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        request: STTStreamRequest,
        *,
        policy: STTRuntimePolicy | None = None,
    ) -> AsyncIterator[STTEvent]:
        runtime = policy or STTRuntimePolicy(
            provider=request.provider or settings.stt_provider,
            fallback_provider=settings.stt_fallback_provider,
            max_retries=max(0, min(settings.voice_provider_max_retries, 3)),
            backoff_seconds=settings.voice_provider_retry_backoff_seconds,
            allow_failover=settings.stt_failover_enabled,
        )
        providers = [runtime.provider]
        if runtime.allow_failover and runtime.fallback_provider and runtime.fallback_provider != runtime.provider:
            providers.append(runtime.fallback_provider)
        source = audio
        emitted = False
        last_error: ProviderError | None = None
        for provider_index, provider_name in enumerate(providers):
            for attempt in range(runtime.max_retries + 1):
                if attempt or provider_index:
                    if request.replay_audio is None:
                        break
                    source = request.replay_audio()
                try:
                    provider = self.registry.resolve_stt(provider_name)
                except ValueError as exc:
                    raise ProviderConfigurationError(str(exc), provider=provider_name) from exc
                started = time.perf_counter()
                try:
                    async with self._concurrency:
                        async for event in provider.stream(
                            source,
                            STTStreamRequest(
                                **{**request.__dict__, "provider": provider_name}
                            ),
                        ):
                            emitted = True
                            yield event
                    return
                except asyncio.CancelledError:
                    await provider.cancel(request.request_id)
                    raise
                except ProviderError as exc:
                    last_error = exc
                    record_provider_error(exc.provider, exc.category)
                    log.warning(
                        "stt.provider_failed",
                        provider=exc.provider,
                        category=exc.category,
                        retryable=exc.retryable,
                        attempt=attempt + 1,
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                    )
                    if emitted or not exc.retryable or request.replay_audio is None:
                        break
                    if attempt < runtime.max_retries:
                        await asyncio.sleep(runtime.backoff_seconds * (2**attempt))
                        continue
                    break
            if provider_index + 1 < len(providers) and not emitted and last_error is not None:
                log.warning(
                    "stt.failover",
                    from_provider=provider_name,
                    to_provider=providers[provider_index + 1],
                    category=last_error.category,
                )
                continue
            break
        if last_error is not None:
            raise last_error
        raise ProviderError("STT provider did not produce a stream", provider=request.provider)
