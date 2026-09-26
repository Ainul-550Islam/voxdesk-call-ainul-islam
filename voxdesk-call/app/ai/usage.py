"""Record governed usage on the existing meter.

Unknown tokens are not written as zero. A metering failure propagates so a
completed provider call cannot look free.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.budget import attribute_usage
from app.ai.costs import for_runtime
from app.billing.metering import usage_idempotency_key
from app.db.models import UsageMetric


async def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tokens: int | None,
    request_id: str,
    provider: str,
    model: str,
    environment_id: uuid.UUID | None = None,
    call_id: uuid.UUID | None = None,
) -> dict:
    if tokens is not None and tokens < 0:
        raise ValueError("token count cannot be negative")
    priced = for_runtime(provider=provider, tokens=tokens if tokens else None)
    if not tokens:
        return {
            "recorded": False,
            "cost_known": False,
            "reason": "tokens_unknown",
            "provider_cost_usd": None,
        }
    await attribute_usage(
        session,
        tenant_id=tenant_id,
        tokens=tokens,
        idempotency_key=usage_idempotency_key(
            UsageMetric.LLM_TOKEN, request_id or tenant_id, discriminator=provider or "ai"
        ),
        environment_id=environment_id,
        provider=provider,
        model=model,
        source_entity_id=call_id,
    )
    return {
        "recorded": True,
        "cost_known": bool(priced.get("known")),
        "reason": priced.get("reason") or "recorded",
        "provider_cost_usd": priced.get("provider_cost_usd"),
    }
