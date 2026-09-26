"""Live entry for governed voice and text.

``invoke`` calls ``app.ai.gateway.govern``. Voice cannot hand Pipecat a
one-shot executor, so ``voice_llm`` runs the same authorization and then the
existing ``build_llm``. ``govern`` does not import this module.

Order, when an utterance is known:

channel, runtime context, effective policy, input guardrails, PII/safety,
approved prompt, tool policy, budget admission, routing, circuit breaker,
timeout, existing gateway, provider, output guardrails, usage/trace, response.

A denied budget, an unpublished production prompt, a client tenant id, or a
client model override never reaches the provider.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_factory import LLMChoice
from app.ai.context import RuntimeContext
from app.ai.errors import RuntimeFailure
from app.ai.gateway import GatewayResult, authorize_live, govern
from app.ai.guardrails.output import enforce as enforce_output
from app.ai.guardrails.input import enforce as enforce_input
from app.ai import trace as trace_mod
from app.auth.dependencies import TenantContext


@dataclass(frozen=True)
class Prepared:
    choice: LLMChoice
    system_prompt: str | None
    blocked_output: tuple[str, ...]
    timeout_ms: int
    prompt_version: int
    approval_state: str
    trace_id: str
    environment_kind: str


def guard_spoken_text(text: str, *, blocked_substrings: tuple[str, ...] = ()) -> str:
    """Output gate used by the voice processor and the text loop."""
    checked = enforce_output(text or "", blocked_substrings=blocked_substrings)
    return checked.text


def guard_heard_text(text: str):
    """Input gate for a voice transcript. A signal is not a block."""
    return enforce_input(text or "", channel="voice")


async def invoke(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    text: str,
    executor,
    channel: str = "text",
    environment_kind: str = "production",
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    requested_preset: str | None = None,
    tools: tuple[str, ...] = (),
    tokens_estimate: int = 1,
    request_id: str = "",
    blocked_output: tuple[str, ...] = (),
    record_usage: bool = False,
    call_id: uuid.UUID | None = None,
    principal: str = "user",
) -> GatewayResult:
    """Application entry. Authorization runs first; ``govern`` calls the provider."""
    await authorize_live(
        session,
        ctx.tenant,
        channel=channel,
        environment_kind=environment_kind,
        text=text,
        claimed_tenant_id=claimed_tenant_id,
        requested_preset=requested_preset,
        tokens_estimate=tokens_estimate,
        tools=tools,
        principal=principal,
        environment_id=environment_id,
        ctx=ctx,
        request_id=request_id,
        call_id=call_id,
    )
    return await govern(
        session,
        ctx,
        text=text,
        channel=channel,
        environment_kind=environment_kind,
        environment_id=environment_id,
        claimed_tenant_id=claimed_tenant_id,
        executor=executor,
        tokens_estimate=tokens_estimate,
        record_usage=record_usage,
        request_id=request_id,
        blocked_output=blocked_output,
        requested_preset=requested_preset,
        budget_checked=True,
        circuit_checked=True,
    )


async def voice_llm(
    session: AsyncSession,
    tenant,
    call,
    *,
    temperature: float,
    max_tokens: int,
    blocked_output: tuple[str, ...] = (),
):
    """Governed Pipecat adapter. Policy denial happens before ``build_llm``."""
    prepared = await prepare_voice(
        session, tenant, call, blocked_output=blocked_output
    )
    from app.agent.llm_factory import build_llm

    service = build_llm(
        prepared.choice.provider,
        prepared.choice.model,
        temperature=temperature,
        max_tokens=max_tokens,
        allow_key_fallback=False,
    )
    return service, prepared


async def prepare_voice(
    session: AsyncSession,
    tenant,
    call,
    *,
    blocked_output: tuple[str, ...] = (),
) -> Prepared:
    environment_id = getattr(call, "environment_id", None)
    kind = await _environment_kind(session, tenant.id, environment_id)
    live = await authorize_live(
        session,
        tenant,
        channel="voice",
        environment_kind=kind,
        text="",
        check_input=False,
        tokens_estimate=1,
        environment_id=environment_id,
        call_id=getattr(call, "id", None),
        principal="agent_runtime",
        request_id=str(getattr(call, "id", "") or ""),
    )
    return Prepared(
        choice=live.choice,
        system_prompt=live.prompt.get("body"),
        blocked_output=blocked_output,
        timeout_ms=live.deadline_ms,
        prompt_version=int(live.prompt.get("version") or 0),
        approval_state=str(live.prompt.get("approval_state") or ""),
        trace_id=live.trace_id,
        environment_kind=kind,
    )


async def govern_text_reply(agent, history, user_text: str) -> dict:
    """Text boundary. Session-less unit tests still run guardrails and the loop.

    A live channel handler always has a session. That path loads policy, admits
    budget, and refuses an unpublished production prompt before any SDK call.
    The completion itself stays ``_complete_openai`` / ``_complete_anthropic``.
    """
    session = getattr(agent.handlers, "session", None)
    channel = agent.channel or "text"
    if session is None:
        heard = enforce_input(user_text, channel="text" if channel != "voice" else "voice")
        if not heard.allowed:
            raise RuntimeFailure("input_rejected")
        result = await agent.complete_turn(history, user_text)
        result["reply"] = guard_spoken_text(
            result["reply"], blocked_substrings=tuple(getattr(agent, "_blocked_output", ()) or ())
        )
        trace_mod.finish(
            trace_mod.start(tenant_id=agent.tenant.id, channel=channel),
            provider=agent.provider,
            model=agent.model,
            status="completed",
            outcome="completed",
            executed=True,
            cost_known=False,
            usage_recorded=False,
            guardrail="signal" if heard.injection_signal else "none",
            principal="agent_runtime",
        )
        return result

    call = getattr(agent.handlers, "call", None)
    environment_id = getattr(call, "environment_id", None)
    kind = await _environment_kind(session, agent.tenant.id, environment_id)
    live = await authorize_live(
        session,
        agent.tenant,
        channel="text" if channel != "voice" else "voice",
        environment_kind=kind,
        text=user_text,
        tokens_estimate=1,
        environment_id=environment_id,
        call_id=getattr(call, "id", None),
        principal="agent_runtime",
    )
    if live.prompt.get("body"):
        agent._runtime_prompt = live.prompt["body"]
    result = await agent.complete_turn(history, user_text)
    result["reply"] = guard_spoken_text(
        result["reply"], blocked_substrings=tuple(getattr(agent, "_blocked_output", ()) or ())
    )
    tokens = result.get("tokens")
    usage_recorded = False
    cost_known = False
    provider_cost = None
    if isinstance(tokens, int) and tokens > 0:
        from app.ai.usage import record

        recorded = await record(
            session,
            tenant_id=agent.tenant.id,
            tokens=tokens,
            request_id=live.trace_id,
            provider=agent.provider,
            model=agent.model,
            environment_id=environment_id,
            call_id=getattr(call, "id", None),
        )
        usage_recorded = bool(recorded["recorded"])
        cost_known = bool(recorded["cost_known"])
        provider_cost = recorded["provider_cost_usd"]
    trace_mod.finish(
        {
            "trace_id": live.trace_id,
            "request_id": live.trace_id,
            "tenant_id": str(agent.tenant.id),
            "channel": channel,
            "call_id": "" if call is None else str(getattr(call, "id", "")),
            "turn_id": "",
        },
        provider=agent.provider,
        model=agent.model,
        status="completed",
        outcome="completed",
        executed=True,
        cost_known=cost_known,
        usage_recorded=usage_recorded,
        provider_cost_usd=provider_cost,
        prompt_version=live.prompt.get("version") or 0,
        approval_state=live.prompt.get("approval_state") or "",
        guardrail=live.guardrail,
        principal="agent_runtime",
        environment_id="" if environment_id is None else str(environment_id),
        tokens=tokens or 0,
    )
    return result


async def _environment_kind(session, tenant_id, environment_id) -> str:
    if environment_id is None:
        return "production"
    from app.db.models import Environment

    env = await session.get(Environment, environment_id)
    if env is None or env.tenant_id != tenant_id:
        return "production"
    return env.kind or "production"


def open_context(
    *,
    tenant_id,
    channel: str,
    environment_kind: str,
    principal: str,
    request_id: str,
    trace_id: str,
    environment_id=None,
    call_id=None,
    user_id=None,
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=tenant_id,
        environment_id=environment_id,
        environment_kind=environment_kind,
        channel=channel,
        principal=principal,
        request_id=request_id,
        trace_id=trace_id,
        call_id=call_id,
        user_id=user_id,
    )
