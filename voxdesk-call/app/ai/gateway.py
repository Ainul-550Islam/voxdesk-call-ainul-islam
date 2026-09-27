"""Governance boundary around a model call.

``govern`` is the one-shot provider boundary. Live voice and text enter through
``app.ai.runtime``, which calls ``authorize_live`` and then ``govern`` when a
principal is present. This module does not import the runtime, so the two
cannot recurse.

When no executor is attached, the result says so and does not invent a completion.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ProviderError
from app.agent.llm_factory import LLMChoice
from app.ai import circuit_breaker, costs, fallback, timeouts
from app.ai.budget import admit, attribute_usage, reconcile, release
from app.ai.guardrails.input import check as check_input
from app.ai.guardrails.output import check as check_output
from app.ai.models import (
    AIModelPolicy,
    DeadlineExceeded,
    GovernanceError,
    PolicyView,
)
from app.ai.routing import catalogue, select
from app.ai.telemetry import emit
from app.auth.dependencies import TenantContext
from app.billing.metering import usage_idempotency_key
from app.db.models import UsageMetric
from app.evaluation.evaluator import Case, Check, run_suite
from app.tenancy.isolation import ValidationFailed
from app.tenancy.policy import bind_tenant

MAX_EVAL_CASES = 20


@dataclass
class GatewayResult:
    provider: str
    model: str
    text: str
    executed: bool
    fallback_used: bool
    attempts: int
    telemetry: dict

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "text": self.text,
            "executed": self.executed,
            "fallback_used": self.fallback_used,
            "attempts": self.attempts,
            "telemetry": self.telemetry,
        }


def default_policy(tenant_id: uuid.UUID) -> PolicyView:
    return PolicyView(
        tenant_id=tenant_id,
        status="active",
        allowed_presets=(),
        disabled_providers=(),
        development_only_presets=(),
        token_ceiling=None,
        persisted=False,
    )


def view_of(row: AIModelPolicy) -> PolicyView:
    return PolicyView(
        tenant_id=row.tenant_id,
        status=row.status,
        allowed_presets=tuple(row.allowed_presets or []),
        disabled_providers=tuple(row.disabled_providers or []),
        development_only_presets=tuple(row.development_only_presets or []),
        token_ceiling=row.token_ceiling,
        persisted=True,
    )


async def load_policy(session: AsyncSession, tenant_id: uuid.UUID) -> PolicyView:
    row = await session.scalar(sa_select(AIModelPolicy).where(AIModelPolicy.tenant_id == tenant_id))
    if row is None:
        return default_policy(tenant_id)
    return view_of(row)


async def save_policy(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    allowed_presets: list[str] | None = None,
    disabled_providers: list[str] | None = None,
    development_only_presets: list[str] | None = None,
    token_ceiling: int | None = None,
    status: str = "active",
    ceiling_supplied: bool = False,
) -> PolicyView:
    if status not in {"active", "disabled"}:
        raise ValidationFailed("Unknown policy status")
    if token_ceiling is not None and token_ceiling < 0:
        raise ValidationFailed("Token ceiling cannot be negative")
    row = await session.scalar(sa_select(AIModelPolicy).where(AIModelPolicy.tenant_id == tenant_id))
    if row is None:
        row = AIModelPolicy(tenant_id=tenant_id, status=status)
        session.add(row)
    row.status = status
    if allowed_presets is not None:
        row.allowed_presets = list(allowed_presets)
    if disabled_providers is not None:
        row.disabled_providers = list(disabled_providers)
    if development_only_presets is not None:
        row.development_only_presets = list(development_only_presets)
    if ceiling_supplied:
        row.token_ceiling = token_ceiling
    await session.flush()
    return view_of(row)


async def govern(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    text: str,
    channel: str = "text",
    environment_kind: str = "production",
    environment_id: uuid.UUID | None = None,
    claimed_tenant_id: uuid.UUID | None = None,
    executor=None,
    tokens_estimate: int = 1,
    record_usage: bool = False,
    request_id: str = "",
    blocked_output: tuple[str, ...] = (),
    requested_preset: str | None = None,
    allow_fallback: bool = True,
    budget_checked: bool = False,
    circuit_checked: bool = False,
) -> GatewayResult:
    bind_tenant(ctx, ctx.tenant_id, claimed_tenant_id)
    incoming = check_input(text, channel=channel)
    if not incoming.allowed:
        raise GovernanceError(
            f"Input rejected: {incoming.reason}", code="input_rejected", status_code=422
        )
    policy = await load_policy(session, ctx.tenant_id)
    if requested_preset:
        from app.ai.routing import select_for_runtime

        choice = select_for_runtime(
            ctx.tenant,
            policy,
            environment_kind=environment_kind,
            requested_preset=requested_preset,
        )
    else:
        choice = select(ctx.tenant, policy, environment_kind=environment_kind)
    if not circuit_checked and not await circuit_breaker.allow_shared(choice.provider):
        raise GovernanceError("Provider circuit is open", code="provider_unavailable")
    deadline = timeouts.deadline_for(channel)
    reserved_here = False
    if not budget_checked:
        budget = await admit(session, ctx.tenant, policy, tokens=tokens_estimate)
        if not budget.allowed:
            raise GovernanceError("AI budget denied the request", code="budget_denied")
        reserved_here = budget.reason == "reserved"
    started = time.perf_counter()
    attempts = 0
    fallback_used = False
    current = choice
    output_text = ""
    executed = False
    tokens = 0
    status = "planned"
    try:
        while attempts < timeouts.max_attempts(channel):
            timeouts.assert_open(deadline)
            attempts += 1
            timeout_ms = timeouts.provider_timeout_ms(deadline, 800 if channel == "voice" else 5000)
            if executor is None:
                status = "executor_not_attached"
                break
            try:
                produced = await executor(current, text, timeout_ms)
                output_text = str(produced.get("text") or "")
                tokens = int(produced.get("tokens") or 0)
                if tokens < 0:
                    raise ValidationFailed("Executor returned a negative token count")
                executed = True
                status = "completed"
                await circuit_breaker.record_success_shared(current.provider)
                break
            except fallback.NON_RETRYABLE:
                await circuit_breaker.record_failure_shared(current.provider)
                raise
            except ProviderError as exc:
                await circuit_breaker.record_failure_shared(current.provider)
                if (
                    not allow_fallback
                    or not fallback.is_retryable(exc)
                    or attempts >= timeouts.max_attempts(channel)
                ):
                    raise
                nxt = fallback.next_candidate(current, policy, environment_kind=environment_kind)
                if nxt is None:
                    raise
                current = nxt
                fallback_used = True
            except DeadlineExceeded:
                raise
    except BaseException:
        # The provider never produced a completion for this reservation. Give
        # the admitted tokens back so a failing provider cannot permanently
        # consume the tenant's ceiling.
        if reserved_here:
            await release(session, ctx.tenant_id, tokens_estimate)
        raise
    if reserved_here:
        if executed:
            await reconcile(
                session, ctx.tenant_id, reserved=tokens_estimate, actual=tokens
            )
        else:
            await release(session, ctx.tenant_id, tokens_estimate)
    checked = check_output(output_text, blocked_substrings=blocked_output)
    if not checked.allowed:
        output_text = checked.text
        status = checked.reason
    cost = costs.estimate(provider=current.provider, tokens=tokens if executed else None)
    if record_usage and executed and tokens > 0:
        await attribute_usage(
            session,
            tenant_id=ctx.tenant_id,
            tokens=tokens,
            idempotency_key=usage_idempotency_key(
                UsageMetric.LLM_TOKEN, request_id or ctx.tenant_id, discriminator=current.provider
            ),
            environment_id=environment_id,
            provider=current.provider,
            model=current.model,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    telemetry = emit(
        {
            "request_id": request_id,
            "tenant_id": str(ctx.tenant_id),
            "environment_id": str(environment_id) if environment_id else "",
            "provider": current.provider,
            "model": current.model,
            "latency_ms": latency_ms,
            "tokens": tokens if executed else 0,
            "status": status,
            "retry_count": max(0, attempts - 1),
            "fallback_used": fallback_used,
            "guardrail": "signal" if incoming.injection_signal else "none",
            "cost_known": cost["known"],
            "provider_cost_usd": cost["provider_cost_usd"],
            "channel": channel,
            "executed": executed,
            "prompt": text,
            "response": output_text,
            "api_key": "sk-should-not-log",
        }
    )
    return GatewayResult(
        provider=current.provider,
        model=current.model,
        text=output_text if executed else "",
        executed=executed,
        fallback_used=fallback_used,
        attempts=attempts,
        telemetry=telemetry,
    )


def providers_view() -> list[dict]:
    return [row.as_dict() for row in catalogue()]


def health_view() -> dict:
    return {
        "providers": [circuit_breaker.snapshot(name) for name in ("openai", "anthropic", "google")],
        "redis_configured": circuit_breaker.redis_configured(),
        "shared_across_workers": circuit_breaker.redis_configured(),
    }


def cases_from_payload(payload: list[dict]) -> list[Case]:
    if len(payload) > MAX_EVAL_CASES:
        raise ValidationFailed("Evaluation dataset exceeds the case limit")
    cases = []
    for item in payload:
        checks = []
        for check in item.get("checks") or []:
            kind = check.get("kind")
            if kind not in {"exact", "contains", "forbids", "token_f1", "similarity", "numbers"}:
                raise ValidationFailed("Unknown evaluation check")
            checks.append(
                Check(
                    name=str(check.get("name") or kind),
                    kind=kind,
                    reference=str(check.get("reference") or ""),
                    needles=tuple(check.get("needles") or ()),
                    minimum=float(check.get("minimum") or 0.5),
                )
            )
        cases.append(
            Case(
                name=str(item.get("name") or ""),
                prompt=str(item.get("prompt") or ""),
                reference=str(item.get("reference") or ""),
                checks=tuple(checks),
            )
        )
    if any(not case.name for case in cases):
        raise ValidationFailed("Evaluation case name is required")
    return cases


def score_cases(cases: list[Case], model_fn) -> dict:
    """Run the existing harness. The result stores pass/fail, not the prompt."""
    started = time.perf_counter()
    suite = run_suite(cases, model_fn)
    if (time.perf_counter() - started) > 5:
        raise DeadlineExceeded("Evaluation exceeded its local timeout")
    return {
        "passed": suite.passed,
        "cases": [{"name": result.case.name, "passed": result.passed} for result in suite.results],
        "prompt_stored_in_result": False,
        "score_invented": False,
    }


def selection_of(choice: LLMChoice) -> dict:
    return {"provider": choice.provider, "model": choice.model}


@dataclass
class LiveAuthorization:
    """What a live call may use. Not an API key and not a prompt body log."""

    choice: LLMChoice
    policy: PolicyView
    prompt: dict
    deadline_ms: int
    trace_id: str
    guardrail: str
    pii_kinds: tuple[str, ...]
    #: True when ``admit`` reserved tokens against a configured ceiling for
    #: this authorization. The caller that owns the invocation lifecycle
    #: (``app.ai.runtime``) uses it to release on failure or reconcile to the
    #: measured token count on success — without it a release could subtract
    #: another concurrent call's live reservation.
    budget_reserved: bool = False


async def authorize_live(
    session: AsyncSession,
    tenant,
    *,
    channel: str,
    environment_kind: str,
    text: str = "",
    check_input: bool = True,
    claimed_tenant_id: uuid.UUID | None = None,
    requested_preset: str | None = None,
    tokens_estimate: int = 1,
    tools: tuple[str, ...] = (),
    principal: str = "agent_runtime",
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext | None = None,
    request_id: str = "",
    call_id: uuid.UUID | None = None,
) -> LiveAuthorization:
    """Refuse a call before any provider. Order matches the runtime contract.

    Budget denial returns here and does not call an executor. Unpublished
    production prompts are refused. A client tenant id or model preset cannot
    replace the authenticated selection.
    """
    from app.ai.budget import admit_for_runtime, release as release_reservation
    from app.ai.context import RuntimeContext
    from app.ai.guardrails.input import enforce as enforce_input
    from app.ai.guardrails.pii import enforce as enforce_pii
    from app.ai.guardrails.tool_policy import enforce as enforce_tool
    from app.ai.policy import effective
    from app.ai.prompts.registry import resolve_for_runtime
    from app.ai.routing import assert_allowed
    from app.ai.trace import start
    from app.ai import circuit_breaker, timeouts

    if principal == "model":
        raise GovernanceError(
            "A model principal cannot authorize an invocation",
            code="policy_denied",
            status_code=403,
        )
    started = start(
        tenant_id=tenant.id,
        channel=channel,
        request_id=request_id,
        call_id=call_id,
    )
    context = RuntimeContext(
        tenant_id=tenant.id,
        environment_id=environment_id,
        environment_kind=environment_kind,
        channel=channel,
        principal=principal,
        request_id=started["request_id"],
        trace_id=started["trace_id"],
        call_id=call_id,
    )
    context.reject_claim(claimed_tenant_id)
    policy, choice = await effective(
        session,
        tenant,
        environment_kind=environment_kind,
        claimed_tenant_id=claimed_tenant_id,
        requested_preset=requested_preset,
        ctx=ctx,
    )
    guardrail = "none"
    pii_kinds: tuple[str, ...] = ()
    if check_input:
        incoming = enforce_input(text, channel="voice" if channel == "voice" else "text")
        if not incoming.allowed:
            raise GovernanceError(
                f"Input rejected: {incoming.reason}",
                code="input_rejected",
                status_code=422,
            )
        guardrail = "signal" if incoming.injection_signal else "none"
        pii_kinds = tuple(enforce_pii(text)["kinds"])
    from app.tenancy.isolation import Conflict, LifecycleDenied

    try:
        prompt = await resolve_for_runtime(
            session,
            tenant,
            environment_kind=environment_kind,
            environment_id=environment_id,
            provider=choice.provider,
        )
    except (LifecycleDenied, Conflict) as exc:
        raise GovernanceError(
            "Unpublished prompt cannot run in production",
            code="prompt_not_approved",
        ) from exc
    for name in tools:
        decision = enforce_tool(name, principal=principal)
        if not decision.allowed:
            raise GovernanceError("Tool is not authorized", code="tool_denied", status_code=403)
    admission = await admit_for_runtime(
        session,
        tenant,
        policy,
        tokens=tokens_estimate,
        call_id=call_id,
        agent_id=principal,
    )
    if not admission.provider_may_run:
        raise GovernanceError("AI budget denied the request", code="budget_denied")
    reserved = admission.reason == "reserved"
    try:
        assert_allowed(choice, policy, environment_kind=environment_kind)
        if not await circuit_breaker.consult(choice.provider):
            raise GovernanceError("Provider circuit is open", code="provider_unavailable")
        deadline = timeouts.deadline_for(channel)
        timeouts.assert_open(deadline)
    except BaseException:
        # Routing, circuit and deadline can all refuse *after* admission has
        # reserved tokens. The provider never ran, so the reservation goes
        # back instead of permanently shrinking the tenant's ceiling.
        if reserved:
            await release_reservation(session, tenant.id, tokens_estimate)
        raise
    budget = timeouts.invocation_budget(channel)
    return LiveAuthorization(
        choice=choice,
        policy=policy,
        prompt=prompt,
        deadline_ms=budget["overall_ms"],
        trace_id=started["trace_id"],
        guardrail=guardrail,
        pii_kinds=pii_kinds,
        budget_reserved=reserved,
    )
