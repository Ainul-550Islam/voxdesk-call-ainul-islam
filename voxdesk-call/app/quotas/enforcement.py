"""Central quota guard.

Unknown limits are not treated as zero, so they do not block. Malformed
configuration fails closed. Inbound call actions are not blocked by this
layer; billing already owns that product decision and refuses to cut off the
phone line.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import AuditAction, Tenant
from app.organization.service import Actor
from app.quotas.metrics import record_decision
from app.quotas.models import QuotaDecision, QuotaError, QuotaKey
from app.quotas.service import _INBOUND_SAFE, resolve_quota

_INBOUND_ACTIONS = frozenset({"inbound_call", "inbound_voice"})


async def enforce(
    session: AsyncSession,
    *,
    key: QuotaKey | str,
    tenant: Tenant,
    used: int,
    adding: int = 1,
    action: str = "admin",
    environment_id: uuid.UUID | None = None,
    actor: Actor | None = None,
) -> QuotaDecision:
    projected = max(0, int(used)) + max(0, int(adding))
    decision = await resolve_quota(
        session,
        key,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        environment_id=environment_id,
        tenant=tenant,
        used=projected,
    )
    key_text = decision.key
    if action in _INBOUND_ACTIONS and key_text in _INBOUND_SAFE and not decision.allowed:
        # Do not block the phone line. The decision is recorded as a warning.
        softened = QuotaDecision(
            key=decision.key,
            mode=decision.mode,
            limit=decision.limit,
            used=decision.used,
            source=decision.source,
            decision="warn",
            reason="inbound_not_blocked",
            allowed=True,
        )
        record_decision(key_text, "warn")
        return softened
    if decision.decision == "malformed" or not decision.allowed:
        who = actor or Actor(tenant_id=tenant.id)
        await emit(
            session,
            AuditAction.QUOTA_DENIED,
            tenant_id=tenant.id,
            actor_user_id=who.user_id,
            actor_email=who.email,
            ip_address=who.ip_address,
            user_agent=who.user_agent,
            detail={
                "quota_key": key_text,
                "reason": decision.reason,
                "action": action,
            },
            commit=False,
        )
        await session.commit()
        if decision.decision == "malformed":
            from app.quotas.models import QuotaConfigurationError
            raise QuotaConfigurationError(
                "Quota configuration is invalid.", decision=decision
            )
        raise QuotaError("Quota exceeded.", decision=decision)
    return decision
