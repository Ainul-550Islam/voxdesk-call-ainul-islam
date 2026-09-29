"""Correlation for one governed call.

The emitted event uses the telemetry allowlist. Prompt text, responses and
credentials are not fields on this payload, so they cannot be logged from here.
"""

from __future__ import annotations

import uuid

from app.ai.telemetry import emit


def start(
    *,
    tenant_id,
    channel: str,
    request_id: str = "",
    call_id=None,
    turn_id=None,
) -> dict:
    return {
        "trace_id": str(uuid.uuid4()),
        "request_id": request_id or str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "channel": channel,
        "call_id": "" if call_id is None else str(call_id),
        "turn_id": "" if turn_id is None else str(turn_id),
    }


def finish(trace: dict, **fields) -> dict:
    payload = {
        "trace_id": trace.get("trace_id", ""),
        "request_id": trace.get("request_id", ""),
        "tenant_id": trace.get("tenant_id", ""),
        "call_id": trace.get("call_id", ""),
        "turn_id": trace.get("turn_id", ""),
        "channel": trace.get("channel", ""),
        "environment_id": fields.get("environment_id", ""),
        "provider": fields.get("provider", ""),
        "model": fields.get("model", ""),
        "status": fields.get("status", ""),
        "outcome": fields.get("outcome", ""),
        "code": fields.get("code", ""),
        "latency_ms": fields.get("latency_ms", 0),
        "tokens": fields.get("tokens"),
        "retry_count": fields.get("retry_count", 0),
        "fallback_used": fields.get("fallback_used", False),
        "guardrail": fields.get("guardrail", "none"),
        "cost_known": fields.get("cost_known", False),
        "provider_cost_usd": fields.get("provider_cost_usd"),
        "executed": fields.get("executed", False),
        "prompt_version": fields.get("prompt_version", ""),
        "approval_state": fields.get("approval_state", ""),
        "tool": fields.get("tool", ""),
        "principal": fields.get("principal", ""),
        "agent_id": fields.get("agent_id", ""),
        "usage_recorded": fields.get("usage_recorded", False),
    }
    return emit(payload)
