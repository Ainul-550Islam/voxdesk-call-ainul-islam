"""Privacy-aware telemetry.

The allowlist is the guarantee. Prompts, responses, transcripts, tool payloads
and credentials are dropped, not truncated into a log line.
"""

from __future__ import annotations

from app.ai.guardrails.pii import redact
from app.core.logging import log

ALLOWED = frozenset(
    {
        "request_id",
        "tenant_id",
        "environment_id",
        "provider",
        "model",
        "latency_ms",
        "tokens",
        "status",
        "retry_count",
        "fallback_used",
        "guardrail",
        "cost_known",
        "provider_cost_usd",
        "channel",
        "executed",
        "trace_id",
        "call_id",
        "turn_id",
        "outcome",
        "code",
        "prompt_version",
        "approval_state",
        "tool",
        "principal",
        "agent_id",
        "usage_recorded",
    }
)

DROPPED = frozenset(
    {
        "prompt",
        "system_prompt",
        "user_prompt",
        "response",
        "completion",
        "transcript",
        "api_key",
        "authorization",
        "tool_arguments",
        "tool_result",
        "secret",
        "password",
        "token",
    }
)


def safe_fields(raw: dict) -> dict:
    cleaned: dict = {}
    for key, value in raw.items():
        name = str(key).lower()
        if name in DROPPED or name not in ALLOWED:
            continue
        if isinstance(value, str) and (
            value.startswith("sk-") or value.startswith("sk_live") or "bearer " in value.lower()
        ):
            continue
        cleaned[name] = redact(value) if isinstance(value, str) else value
    return cleaned


def emit(raw: dict) -> dict:
    fields = safe_fields(raw)
    log.info("ai.governance", **fields)
    return fields
