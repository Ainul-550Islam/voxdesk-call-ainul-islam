"""Admission checks in front of the existing entitlement path.

A missing ceiling is unknown, not zero. A configured ceiling is reserved with
one UPDATE so two requests cannot both pass a limit of N. The reservation is
not a tenant charge. Charges stay in ``app.billing.metering``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.entitlements import check_metric, load_context
from app.billing.metering import record_usage, usage_idempotency_key
from app.billing.periods import calendar_period
from app.db.models import Tenant, UsageEventType, UsageMetric
from app.ai.models import AIAdmissionCounter, BudgetDenied, PolicyView
from app.tenancy.isolation import ValidationFailed


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    ceiling: int | None
    billing_reason: str

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "ceiling": self.ceiling,
            "billing_reason": self.billing_reason,
            "pricing": "unknown" if self.ceiling is None else "configured",
            "tenant_charge_usd": None,
        }


async def admit(
    session: AsyncSession,
    tenant: Tenant,
    policy: PolicyView,
    *,
    tokens: int,
) -> BudgetDecision:
    if tokens < 0:
        raise ValidationFailed("Token estimate cannot be negative")
    context = await load_context(session, tenant)
    entitlement = await check_metric(session, context, UsageMetric.LLM_TOKEN, additional=tokens)
    billing_reason = entitlement.reason or entitlement.decision.value
    if entitlement.decision.value == "deny":
        return BudgetDecision(False, "billing_denied", policy.token_ceiling, billing_reason)
    if policy.token_ceiling is None:
        return BudgetDecision(True, "ceiling_unknown", None, billing_reason)
    if tokens == 0:
        return BudgetDecision(True, "no_tokens", policy.token_ceiling, billing_reason)
    reserved = await _reserve(session, tenant.id, tokens, policy.token_ceiling)
    if not reserved:
        return BudgetDecision(False, "ceiling_exceeded", policy.token_ceiling, billing_reason)
    return BudgetDecision(True, "reserved", policy.token_ceiling, billing_reason)


async def _reserve(session: AsyncSession, tenant_id: uuid.UUID, adding: int, limit: int) -> bool:
    if limit < 0 or adding < 0:
        raise BudgetDenied("Budget values cannot be negative")
    existing = await session.get(AIAdmissionCounter, tenant_id)
    if existing is None:
        try:
            async with session.begin_nested():
                session.add(AIAdmissionCounter(tenant_id=tenant_id, tokens_reserved=0))
                await session.flush()
        except IntegrityError:
            existing = await session.get(AIAdmissionCounter, tenant_id)
            if existing is None:
                raise BudgetDenied("Budget counter conflict") from None
    result = await session.execute(
        update(AIAdmissionCounter)
        .where(
            AIAdmissionCounter.tenant_id == tenant_id,
            AIAdmissionCounter.tokens_reserved + adding <= limit,
        )
        .values(tokens_reserved=AIAdmissionCounter.tokens_reserved + adding)
    )
    return result.rowcount == 1


@dataclass(frozen=True)
class RuntimeAdmission:
    """Admission plus the call it belongs to. Denial means the provider must not run."""

    allowed: bool
    reason: str
    ceiling: int | None
    billing_reason: str
    call_id: str | None = None
    agent_id: str | None = None

    @property
    def provider_may_run(self) -> bool:
        return self.allowed


async def admit_for_runtime(
    session: AsyncSession,
    tenant: Tenant,
    policy: PolicyView,
    *,
    tokens: int,
    call_id: uuid.UUID | None = None,
    agent_id: str | None = None,
) -> RuntimeAdmission:
    decision = await admit(session, tenant, policy, tokens=tokens)
    return RuntimeAdmission(
        allowed=decision.allowed,
        reason=decision.reason,
        ceiling=decision.ceiling,
        billing_reason=decision.billing_reason,
        call_id=None if call_id is None else str(call_id),
        agent_id=agent_id,
    )


async def attribute_usage(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tokens: int,
    idempotency_key: str,
    environment_id: uuid.UUID | None = None,
    provider: str = "",
    model: str = "",
    source_entity_id: uuid.UUID | None = None,
) -> None:
    """Record tokens on the existing meter. Does not commit and does not price."""
    if tokens <= 0:
        return
    period = calendar_period()
    await record_usage(
        session,
        tenant_id=tenant_id,
        metric=UsageMetric.LLM_TOKEN,
        quantity=tokens,
        idempotency_key=idempotency_key
        or usage_idempotency_key(UsageMetric.LLM_TOKEN, tenant_id, discriminator=provider or "ai"),
        period=period,
        event_type=UsageEventType.LLM_TOKEN_USED,
        environment_id=environment_id,
        source_entity_id=source_entity_id,
        metadata={"provider": provider, "model": model, "source": "ai.governance"},
    )
