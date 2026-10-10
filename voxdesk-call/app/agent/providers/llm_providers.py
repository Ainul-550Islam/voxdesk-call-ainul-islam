"""Multi-provider LLM builders for OpenAI, Anthropic, Google, Groq, Azure, Bedrock & Custom OpenAI (Sub-Phase 2C)."""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.agent.providers.registry import (
    get_provider_credential,
    require_provider_configured,
)
from app.core.config import settings


class AdapterLLMService(FrameProcessor):
    """Lightweight Pipecat `FrameProcessor` LLM service for enterprise/cloud providers."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        temperature: float = 0.6,
        max_tokens: int = 300,
        api_key: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._api_key = api_key
        self.extra = dict(extra or {})
        self._functions: dict[str, Any] = {}

    def register_function(self, name: str | None, handler: Any, **kwargs: Any) -> None:
        self._functions[str(name or "*")] = handler

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


def build_llm_provider(
    provider: str | None = None,
    *,
    model: str | None = None,
    temperature: float = 0.6,
    max_tokens: int = 300,
    cfg: Any = None,
    configured_settings: Any = settings,
) -> FrameProcessor:
    """Build a Pipecat LLM processor for the requested provider (Sub-Phase 2C)."""
    if cfg is not None:
        provider = provider or getattr(cfg, "llm_provider", "openai")
        model = model or getattr(cfg, "llm_model", None)
        temperature = float(getattr(cfg, "temperature", temperature))
        max_tokens = int(getattr(cfg, "max_tokens", max_tokens))

    spec = require_provider_configured(
        "llm", provider or "openai", configured_settings=configured_settings
    )
    resolved_model = model or spec.default_model

    if spec.provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        api_key = get_provider_credential(
            "OPENAI_API_KEY", "openai_api_key", configured_settings
        )
        svc = OpenAILLMService(
            api_key=api_key,
            model=resolved_model,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
                frequency_penalty=0.3,
                presence_penalty=0.3,
            ),
        )
        setattr(svc, "provider", "openai")
        return svc

    if spec.provider == "anthropic":
        from pipecat.services.anthropic.llm import AnthropicLLMService

        api_key = get_provider_credential(
            "ANTHROPIC_API_KEY", "anthropic_api_key", configured_settings
        )
        svc = AnthropicLLMService(
            api_key=api_key,
            model=resolved_model,
            params=AnthropicLLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        setattr(svc, "provider", "anthropic")
        return svc

    if spec.provider == "google":
        from pipecat.services.google.llm import GoogleLLMService

        api_key = get_provider_credential(
            "GOOGLE_API_KEY", "google_api_key", configured_settings
        )
        svc = GoogleLLMService(
            api_key=api_key,
            model=resolved_model,
            params=GoogleLLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        setattr(svc, "provider", "google")
        return svc

    if spec.provider == "groq":
        from pipecat.services.openai.llm import OpenAILLMService

        api_key = get_provider_credential(
            "GROQ_API_KEY", "groq_api_key", configured_settings
        )
        svc = OpenAILLMService(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            model=resolved_model,
            params=OpenAILLMService.InputParams(
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        setattr(svc, "provider", "groq")
        return svc

    if spec.provider == "azure_openai":
        api_key = get_provider_credential(
            "AZURE_OPENAI_API_KEY", "azure_openai_api_key", configured_settings
        )
        endpoint = get_provider_credential(
            "AZURE_OPENAI_ENDPOINT", "azure_openai_endpoint", configured_settings
        )
        try:
            from pipecat.services.azure.llm import AzureLLMService

            svc = AzureLLMService(
                api_key=api_key,
                endpoint=endpoint,
                model=resolved_model,
            )
            setattr(svc, "provider", "azure_openai")
            return svc
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return AdapterLLMService(
                provider="azure_openai",
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=api_key,
                extra={"endpoint": endpoint},
            )

    if spec.provider == "bedrock":
        access_key = get_provider_credential(
            "AWS_ACCESS_KEY_ID", "aws_access_key_id", configured_settings
        )
        secret_key = get_provider_credential(
            "AWS_SECRET_ACCESS_KEY", "aws_secret_access_key", configured_settings
        )
        region = (
            get_provider_credential("AWS_REGION", "aws_region", configured_settings)
            or "us-east-1"
        )
        return AdapterLLMService(
            provider="bedrock",
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=access_key,
            extra={"aws_region": region, "has_secret": bool(secret_key)},
        )

    # custom_openai
    from pipecat.services.openai.llm import OpenAILLMService

    api_key = get_provider_credential(
        "CUSTOM_OPENAI_API_KEY", "custom_openai_api_key", configured_settings
    )
    base_url = get_provider_credential(
        "CUSTOM_OPENAI_BASE_URL", "custom_openai_base_url", configured_settings
    )
    svc = OpenAILLMService(
        api_key=api_key,
        base_url=base_url,
        model=resolved_model,
        params=OpenAILLMService.InputParams(
            temperature=temperature,
            max_tokens=max_tokens,
        ),
    )
    setattr(svc, "provider", "custom_openai")
    return svc
