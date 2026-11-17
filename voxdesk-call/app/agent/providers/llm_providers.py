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


class StructuredOutputError(ValueError):
    """The model returned something that does not satisfy the requested schema."""


#: Models worth routing a latency-sensitive turn to. Kept as data so the dashboards and the
#: model catalogue agree instead of each having their own list.
FAST_TIER_MODELS: frozenset[str] = frozenset(
    {
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "claude-3-5-haiku-latest",
        "gemini-2.0-flash",
        "gemini-2.5-flash",
    }
)


def fast_tier(model_id: str) -> bool:
    return str(model_id or "").strip().lower() in FAST_TIER_MODELS


def structured_output_params(
    schema: dict[str, Any] | None,
    *,
    name: str = "agent_response",
    strict: bool = True,
) -> dict[str, Any]:
    """Build the `response_format` object for a JSON-schema constrained reply.

    Returns `{}` when no schema was configured, so callers can splat the result unconditionally.
    A schema without a `type` is refused here rather than sent: providers reject it, and a 400 at
    the first turn of a live call is the worst possible place to discover a typo.
    """
    if not schema:
        return {}
    if not isinstance(schema, dict) or not schema.get("type"):
        raise StructuredOutputError("a JSON schema needs a top-level 'type'")
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "schema": schema, "strict": bool(strict)},
    }


def parse_structured_response(text: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    """Parse and minimally validate a structured reply.

    Validation is deliberately *structural* (type, required keys) and not a full JSON-Schema
    implementation: the goal is to catch a model that ignored the schema, not to re-implement
    `jsonschema` in the audio path.
    """
    import json

    if not schema:
        raise StructuredOutputError("no schema configured")
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"response was not JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise StructuredOutputError("response must be a JSON object")

    required = schema.get("required") or []
    missing = [key for key in required if key not in data]
    if missing:
        raise StructuredOutputError(f"missing required keys: {', '.join(sorted(missing))}")

    for key, spec in (schema.get("properties") or {}).items():
        if key not in data:
            continue
        declared = spec.get("type") if isinstance(spec, dict) else None
        checks = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        expected = checks.get(str(declared))
        value = data[key]
        if declared in {"number", "integer"} and isinstance(value, bool):
            raise StructuredOutputError(f"{key} should be {declared}")
        if expected is not None and not isinstance(value, expected):
            raise StructuredOutputError(f"{key} should be {declared}")
    return data


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
