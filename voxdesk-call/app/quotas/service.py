"""Effective quota from the hierarchy and the existing billing entitlement.

A hierarchy value may tighten a billing cap. It may not raise one. A missing
value is unknown, not zero. A stored row that breaks its own shape is
malformed and fails closed rather than becoming unlimited.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import UNLIMITED
from app.db.models import QuotaLimit, Tenant
from app.quotas.metrics import record_decision
from app.quotas.models import (
    QuotaDecision,
    QuotaDecisionKind,
    QuotaKey,
    QuotaMode,
)

_BILLING_FEATURES = {
    QuotaKey.USERS.value: "team_members",
    QuotaKey.KNOWLEDGE_DOCUMENTS.value: "rag_documents",
    QuotaKey.ACTIVE_CALLS.value: "concurrent_calls",
    QuotaKey.ENVIRONMENTS.value: None,
    QuotaKey.STORAGE_BYTES.value: None,
    QuotaKey.WEBHOOK_EVENTS_PER_MINUTE.value: None,
}

_INBOUND_SAFE = frozenset({
    QuotaKey.MONTHLY_CALL_MINUTES.value,
    QuotaKey.ACTIVE_CALLS.value,
})


def parse_key(key: QuotaKey | str) -> str:
    if isinstance(key, QuotaKey):
        return key.value
    text = str(key).strip()
    try:
        return QuotaKey(text).value
    except ValueError as exc:
        raise ValueError("Unknown quota key") from exc


def _malformed(key: str, reason: str) -> QuotaDecision:
    decision = QuotaDecision(
        key=key,
        mode=QuotaMode.MALFORMED.value,
        limit=None,
        used=None,
        source="malformed",
        decision=QuotaDecisionKind.MALFORMED.value,
        reason=reason,
        allowed=False,
    )
    record_decision(key, decision.decision)
    return decision


def _unknown(key: str, used: int | None) -> QuotaDecision:
    decision = QuotaDecision(
        key=key,
        mode=QuotaMode.UNKNOWN.value,
        limit=None,
        used=used,
        source="none",
        decision=QuotaDecisionKind.UNKNOWN.value,
        reason="limit_unknown",
        allowed=True,
    )
    record_decision(key, decision.decision)
    return decision


def interpret_row(row: QuotaLimit | None, key: str) -> tuple[str, int | None, str] | QuotaDecision:
    """Return ``(mode, limit, source)`` or a malformed decision."""
    if row is None:
        return QuotaMode.UNKNOWN.value, None, "none"
    mode = row.mode
    if mode not in (
        QuotaMode.HARD.value, QuotaMode.SOFT.value,
        QuotaMode.UNLIMITED.value, QuotaMode.UNKNOWN.value,
    ):
        return _malformed(key, "configuration_error")
    if mode in (QuotaMode.UNLIMITED.value, QuotaMode.UNKNOWN.value):
        if row.limit_value is not None:
            return _malformed(key, "configuration_error")
        return mode, None, "hierarchy"
    if row.limit_value is None or row.limit_value < 0:
        return _malformed(key, "configuration_error")
    return mode, int(row.limit_value), "hierarchy"


def _billing_cap(plan, key: str) -> tuple[str, int | None]:
    """A billing number, unlimited, or unknown. Never a silent zero."""
    if plan is None:
        return QuotaMode.UNKNOWN.value, None
    if key == QuotaKey.MONTHLY_CALL_MINUTES.value:
        raw = getattr(plan, "included_voice_minutes", None)
        if raw is None:
            return QuotaMode.UNKNOWN.value, None
        return QuotaMode.HARD.value, int(raw)
    if key == QuotaKey.AI_TOKENS.value:
        raw = getattr(plan, "included_llm_tokens", None)
        if raw is None:
            return QuotaMode.UNKNOWN.value, None
        return QuotaMode.HARD.value, int(raw)
    feature = _BILLING_FEATURES.get(key)
    if not feature:
        return QuotaMode.UNKNOWN.value, None
    from app.billing.plans import feature_limit
    limit = feature_limit(plan, feature)
    if limit is None:
        return QuotaMode.UNKNOWN.value, None
    if isinstance(limit, bool):
        return (QuotaMode.UNLIMITED.value, None) if limit else (QuotaMode.HARD.value, 0)
    if int(limit) == UNLIMITED:
        return QuotaMode.UNLIMITED.value, None
    if int(limit) < 0:
        return QuotaMode.UNKNOWN.value, None
    return QuotaMode.HARD.value, int(limit)


def combine(
    key: str,
    hierarchy: tuple[str, int | None],
    billing: tuple[str, int | None],
    *,
    used: int | None,
) -> QuotaDecision:
    """Stricter numeric cap wins. Hierarchy cannot raise a billing cap."""
    h_mode, h_limit = hierarchy
    b_mode, b_limit = billing
    if h_mode == QuotaMode.MALFORMED.value or b_mode == QuotaMode.MALFORMED.value:
        return _malformed(key, "configuration_error")
    candidates: list[tuple[int, str]] = []
    if h_mode in (QuotaMode.HARD.value, QuotaMode.SOFT.value) and h_limit is not None:
        candidates.append((h_limit, "hierarchy"))
    if b_mode == QuotaMode.HARD.value and b_limit is not None:
        candidates.append((b_limit, "billing"))
    if not candidates:
        if h_mode == QuotaMode.UNLIMITED.value and b_mode == QuotaMode.UNLIMITED.value:
            mode, source = QuotaMode.UNLIMITED.value, "billing"
            limit = None
        elif h_mode == QuotaMode.UNLIMITED.value and b_mode == QuotaMode.UNKNOWN.value:
            mode, source, limit = QuotaMode.UNLIMITED.value, "hierarchy", None
        elif b_mode == QuotaMode.UNLIMITED.value and h_mode == QuotaMode.UNKNOWN.value:
            mode, source, limit = QuotaMode.UNLIMITED.value, "billing", None
        else:
            return _unknown(key, used)
    else:
        limit, source = min(candidates, key=lambda item: item[0])
        mode = QuotaMode.SOFT.value if h_mode == QuotaMode.SOFT.value and limit == h_limit else QuotaMode.HARD.value
        if b_limit is not None and h_limit is not None and h_limit > b_limit:
            source = "billing"
            limit = b_limit
            mode = QuotaMode.HARD.value
    if mode == QuotaMode.UNLIMITED.value:
        decision = QuotaDecisionKind.ALLOW.value
        reason = "unlimited"
        allowed = True
    elif used is None or limit is None:
        decision = QuotaDecisionKind.ALLOW.value
        reason = "no_usage_supplied"
        allowed = True
    elif used > limit and mode == QuotaMode.HARD.value:
        decision = QuotaDecisionKind.DENY.value
        reason = "hard_limit"
        allowed = False
    elif used > limit:
        decision = QuotaDecisionKind.WARN.value
        reason = "soft_limit"
        allowed = True
    elif limit and used / limit >= 0.8:
        decision = QuotaDecisionKind.WARN.value
        reason = "approaching_limit"
        allowed = True
    else:
        decision = QuotaDecisionKind.ALLOW.value
        reason = "within_limit"
        allowed = True
    result = QuotaDecision(
        key=key, mode=mode, limit=limit, used=used, source=source,
        decision=decision, reason=reason, allowed=allowed,
    )
    record_decision(key, result.decision)
    return result


async def _row(
    session: AsyncSession, kind: str, scope_id: uuid.UUID | None, key: str
) -> QuotaLimit | None:
    if scope_id is None:
        return None
    return (
        await session.execute(
            select(QuotaLimit).where(
                QuotaLimit.scope_kind == kind,
                QuotaLimit.scope_id == scope_id,
                QuotaLimit.quota_key == key,
            )
        )
    ).scalar_one_or_none()


def _tighter(rows: list[tuple[str, int | None, str]]) -> tuple[str, int | None]:
    numeric = [(mode, limit) for mode, limit, _source in rows if limit is not None]
    if not numeric:
        for mode, limit, _source in rows:
            if mode == QuotaMode.UNLIMITED.value:
                return QuotaMode.UNLIMITED.value, None
        return QuotaMode.UNKNOWN.value, None
    limit = min(item[1] for item in numeric)
    mode = QuotaMode.HARD.value
    for item_mode, item_limit in numeric:
        if item_limit == limit and item_mode == QuotaMode.SOFT.value:
            mode = QuotaMode.SOFT.value
    return mode, limit


async def resolve_quota(
    session: AsyncSession,
    key: QuotaKey | str,
    *,
    organization_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    environment_id: uuid.UUID | None = None,
    tenant: Tenant | None = None,
    used: int | None = None,
) -> QuotaDecision:
    parsed = parse_key(key)
    layers = []
    for kind, scope_id in (
        ("organization", organization_id),
        ("tenant", tenant_id),
        ("environment", environment_id),
    ):
        interpreted = interpret_row(await _row(session, kind, scope_id, parsed), parsed)
        if isinstance(interpreted, QuotaDecision):
            return interpreted
        layers.append(interpreted)
    hierarchy = _tighter(layers)
    billing_mode, billing_limit = QuotaMode.UNKNOWN.value, None
    if tenant is not None:
        from app.billing.entitlements import load_context
        context = await load_context(session, tenant)
        if context.unlimited:
            billing_mode, billing_limit = QuotaMode.UNLIMITED.value, None
        else:
            billing_mode, billing_limit = _billing_cap(context.plan, parsed)
    return combine(parsed, (hierarchy[0], hierarchy[1]), (billing_mode, billing_limit), used=used)


async def set_quota(
    session: AsyncSession,
    *,
    scope_kind: str,
    scope_id: uuid.UUID,
    key: QuotaKey | str,
    mode: str,
    limit_value: int | None,
) -> QuotaLimit:
    parsed = parse_key(key)
    if mode not in (
        QuotaMode.HARD.value, QuotaMode.SOFT.value,
        QuotaMode.UNLIMITED.value, QuotaMode.UNKNOWN.value,
    ):
        raise ValueError("Unknown quota mode")
    if mode in (QuotaMode.HARD.value, QuotaMode.SOFT.value):
        if limit_value is None or limit_value < 0:
            raise ValueError("A hard or soft quota needs a non-negative limit")
    elif limit_value is not None:
        raise ValueError("Unlimited and unknown quotas do not take a limit")
    row = await _row(session, scope_kind, scope_id, parsed)
    if row is None:
        row = QuotaLimit(
            scope_kind=scope_kind, scope_id=scope_id, quota_key=parsed,
            mode=mode, limit_value=limit_value,
        )
        session.add(row)
    else:
        row.mode = mode
        row.limit_value = limit_value
    await session.commit()
    await session.refresh(row)
    return row
