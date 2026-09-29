"""Provider-agnostic governed translation engine.

The optional provider callable is an injection point for tests and deployments;
production fallback uses the existing ``app.agent.text_agent`` provider path and
never invents a translation when that path is unavailable.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.llm_factory import api_key_for
from app.agent.text_agent import _complete_anthropic, _complete_openai, _openai_style_client
from app.governance.models import ModelVersion
from app.specialized_agents.context import SpecializedAgentContext
from app.specialized_agents.enums import ReviewState
from app.specialized_agents.exceptions import AgentExecutionError, UnsupportedFeatureError

from .glossary import Glossary
from .quality import validate_translation
from .schemas import TranslationJobResult, TranslationSegment, TranslationSegmentResult

Provider = Callable[[str, str, str, SpecializedAgentContext], str | Awaitable[str]]


class TranslationEngine:
    def __init__(self, session=None, provider: Provider | None = None):
        self.session = session
        self.provider = provider

    async def _existing_provider(
        self,
        prompt: str,
        source_language: str,
        target_language: str,
        context: SpecializedAgentContext,
    ) -> tuple[str, str, str]:
        if self.session is None:
            raise UnsupportedFeatureError("translation provider context is unavailable")
        model = await self.session.get(ModelVersion, context.model_version_id)
        if model is None or not model.provider or not model.model_name:
            raise UnsupportedFeatureError("approved translation model metadata is unavailable")
        provider = model.provider
        api_key = api_key_for(provider)
        if not api_key:
            raise UnsupportedFeatureError("configured credentials for the approved provider are unavailable")
        messages = [{"role": "user", "content": prompt}]
        if provider in {"openai", "google"}:
            client = _openai_style_client(provider, api_key)
            text, _calls, _raw, _tokens = await _complete_openai(
                client, model.model_name, messages, [], 0.0, provider=provider
            )
        elif provider == "anthropic":
            text, _calls, _raw, _tokens = await _complete_anthropic(
                api_key, model.model_name, "", messages, [], 0.0, provider=provider
            )
        else:
            raise UnsupportedFeatureError(f"unsupported translation provider: {provider}")
        return text, provider, model.model_name

    async def translate(
        self,
        *,
        source_language: str,
        target_language: str,
        segments: list[TranslationSegment],
        glossary: Glossary,
        context: SpecializedAgentContext,
    ) -> dict[str, Any]:
        if source_language.casefold() == target_language.casefold():
            raise UnsupportedFeatureError("source and target languages must differ")
        if not segments:
            raise AgentExecutionError("at least one translation segment is required")
        translated: list[TranslationSegment] = []
        provider_name: str | None = None
        model_name: str | None = None
        for segment in segments:
            prompt = (
                "Translate exactly one segment. Return only the translation. "
                f"Source language: {source_language}. Target language: {target_language}. "
                f"Glossary version: {glossary.version}. Glossary terms: {glossary.prompt_terms()}. "
                f"Preserve placeholders, URLs, email addresses, and numbers. Source: {segment.source_text}"
            )
            if self.provider is not None:
                result = self.provider(prompt, source_language, target_language, context)
                translated_text = await result if inspect.isawaitable(result) else result
                provider_name = "injected_provider"
                model_name = None
            else:
                translated_text, provider_name, model_name = await self._existing_provider(
                    prompt, source_language, target_language, context
                )
            if not isinstance(translated_text, str):
                raise AgentExecutionError("translation provider returned a non-text result")
            translated.append(
                segment.model_copy(
                    update={
                        "translated_text": translated_text,
                        "source_fingerprint": __import__("hashlib").sha256(segment.source_text.encode()).hexdigest(),
                        "target_fingerprint": __import__("hashlib").sha256(translated_text.encode()).hexdigest(),
                    }
                )
            )
        quality = validate_translation(translated, glossary)
        result = TranslationJobResult(
            source_language=source_language,
            target_language=target_language,
            glossary_version=glossary.version,
            segments=[
                TranslationSegmentResult(
                    segment_id=segment.segment_id,
                    source_fingerprint=segment.source_fingerprint or "",
                    target_fingerprint=segment.target_fingerprint or "",
                    translated_text=segment.translated_text or "",
                )
                for segment in translated
            ],
            quality_status=quality.status,
            quality_flags=list(quality.flags),
            review_required=quality.review_required,
            review_state=ReviewState.REQUIRED.value if quality.review_required else ReviewState.NOT_REQUIRED.value,
            provider=provider_name,
            model=model_name,
        )
        return result.model_dump(mode="json")
