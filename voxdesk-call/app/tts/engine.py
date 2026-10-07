"""Streaming TTS orchestration with bounded retry and policy-driven failover."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator

from app.agent.errors import ProviderError
from app.agent.provider_observability import record_provider_error
from app.core.config import settings
from app.core.logging import log
from app.core.tracing import span
from app.tts.provider import TTSChunk, TTSRequest
from app.tts.registry import TTSRegistry, build_tts_registry


@dataclass(frozen=True)
class TTSRuntimePolicy:
    provider: str
    fallback_provider: str = ""
    max_retries: int = 1
    backoff_seconds: float = 0.15
    allow_failover: bool = True


class TTSEngine:
    """Provider-neutral stream facade.

    Async iteration supplies natural backpressure: the provider is not asked
    for another chunk until the media consumer requests it. A fallback is
    attempted only before any audio has been emitted, avoiding duplicate audio
    after a mid-utterance failure.
    """

    def __init__(self, registry: TTSRegistry | None = None, *, max_concurrent: int = 16) -> None:
        self.registry = registry or build_tts_registry()
        self._concurrency = asyncio.Semaphore(max(1, min(max_concurrent, 64)))
        self._active_providers: dict[str, str] = {}

    async def stream(
        self, request: TTSRequest, *, policy: TTSRuntimePolicy | None = None
    ) -> AsyncIterator[TTSChunk]:
        runtime = policy or TTSRuntimePolicy(
            provider=request.provider or settings.tts_provider,
            fallback_provider=settings.tts_fallback_provider,
            max_retries=max(0, min(settings.voice_provider_max_retries, 3)),
            backoff_seconds=settings.voice_provider_retry_backoff_seconds,
            allow_failover=settings.tts_failover_enabled,
        )
        providers = [runtime.provider]
        if runtime.allow_failover and runtime.fallback_provider and runtime.fallback_provider != runtime.provider:
            providers.append(runtime.fallback_provider)
        last_error: ProviderError | None = None
        emitted = False
        for provider_index, provider_name in enumerate(providers):
            for attempt in range(runtime.max_retries + 1):
                provider_request = TTSRequest(
                    **{**request.__dict__, "provider": provider_name}
                )
                provider = self.registry.resolve(provider_name)
                if not provider.capabilities.streaming:
                    raise ProviderError(
                        "configured TTS provider does not support streaming",
                        provider=provider_name,
                        category="unsupported_feature",
                        retryable=False,
                    )
                if request.sample_rate not in provider.capabilities.sample_rates or request.encoding not in provider.capabilities.encodings:
                    raise ProviderError(
                        "configured TTS provider does not support the requested audio format",
                        provider=provider_name,
                        category="unsupported_feature",
                        retryable=False,
                    )
                self._active_providers[request.request_id] = provider_name
                async with self._concurrency:
                    started = time.perf_counter()
                    try:
                        with span(
                            "voxdesk.tts.stream",
                            provider=provider_name,
                            request_id=request.request_id,
                            session_id=request.session_id,
                        ):
                            async for chunk in provider.stream(provider_request):
                                emitted = emitted or bool(chunk.audio)
                                yield chunk
                        self._active_providers.pop(request.request_id, None)
                        return
                    except asyncio.CancelledError:
                        await provider.cancel(request.request_id)
                        self._active_providers.pop(request.request_id, None)
                        raise
                    except ProviderError as exc:
                        self._active_providers.pop(request.request_id, None)
                        last_error = exc
                        record_provider_error(exc.provider, exc.category)
                        log.warning(
                            "tts.provider_failed",
                            provider=exc.provider,
                            category=exc.category,
                            retryable=exc.retryable,
                            attempt=attempt + 1,
                            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                        )
                        if emitted or not exc.retryable:
                            break
                        if attempt < runtime.max_retries:
                            await asyncio.sleep(runtime.backoff_seconds * (2**attempt))
                            continue
                        break
                    except Exception:
                        self._active_providers.pop(request.request_id, None)
                        raise
            if provider_index + 1 < len(providers) and not emitted and last_error is not None:
                log.warning(
                    "tts.failover",
                    from_provider=provider_name,
                    to_provider=providers[provider_index + 1],
                    category=last_error.category,
                )
                continue
            break
        if last_error is not None:
            raise last_error
        raise ProviderError("TTS provider did not produce audio", provider=request.provider)

    async def cancel(self, request_id: str, provider: str | None = None) -> None:
        provider_name = provider or self._active_providers.get(request_id)
        if provider_name:
            await self.registry.resolve(provider_name).cancel(request_id)


# Backward-compatible name for code that imported the old skeleton.
TTSService = TTSEngine
