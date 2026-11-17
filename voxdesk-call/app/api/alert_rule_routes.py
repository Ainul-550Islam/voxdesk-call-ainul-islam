# File: app/api/alert_rule_routes.py — alert rule CRUD, test-fire and history
"""Alert rule API (PART 15, P15-04).

The tenant in the token is the only tenant. A rule id from another tenant is a
404, never a 403 -- a 403 confirms the row exists.

``test-fire`` is deliberately *dry by default*: it evaluates the rule against a
value you supply and reports what would have happened, without writing an event
and without posting to Slack. A "test" button that pages the on-call engineer is
a test button nobody presses twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import evaluator, notifier
from app.alerts.models import AlertEvent, AlertRule
from app.auth.dependencies import TenantContext, get_context, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.context import reject_client_override

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

MAX_RULES_PER_TENANT = 100


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    metric: str
    comparator: str = Field(pattern="^(gt|lt|gte|lte)$")
    threshold: float
    window_minutes: int = Field(default=15, ge=1, le=1440)
    cooldown_minutes: int = Field(default=30, ge=0, le=1440)
    scope_kind: str = Field(default="tenant", pattern="^(tenant|agent|number)$")
    scope_id: str | None = Field(default=None, max_length=120)
    channels: list[Any] = Field(default_factory=list)
    enabled: bool = True
    tenant_id: uuid.UUID | None = None

    @field_validator("metric")
    @classmethod
    def _metric_known(cls, value: str) -> str:
        if value not in evaluator.METRICS:
            raise ValueError(f"unknown metric {value!r}; expected one of {list(evaluator.METRICS)}")
        return value

    @field_validator("scope_id")
    @classmethod
    def _scope_id_required(cls, value: str | None, info: Any) -> str | None:
        scope_kind = info.data.get("scope_kind", "tenant")
        if scope_kind != "tenant" and not (value or "").strip():
            raise ValueError("scope_id is required when scope_kind is agent or number")
        return value

    @field_validator("channels")
    @classmethod
    def _channels_shape(cls, value: list[Any]) -> list[Any]:
        if len(value) > 8:
            raise ValueError("at most 8 channels per rule")
        for entry in value:
            if isinstance(entry, str):
                if not entry.strip():
                    raise ValueError("channel name cannot be empty")
                continue
            if isinstance(entry, dict):
                if not str(entry.get("channel") or "").strip():
                    raise ValueError("channel entry needs a 'channel' key")
                continue
            raise ValueError("channels must be names or {channel, recipient} objects")
        return value


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    threshold: float | None = None
    window_minutes: int | None = Field(default=None, ge=1, le=1440)
    cooldown_minutes: int | None = Field(default=None, ge=0, le=1440)
    channels: list[Any] | None = None
    enabled: bool | None = None
    scope_id: str | None = Field(default=None, max_length=120)


class TestFireBody(BaseModel):
    observed: float | None = None
    deliver: bool = False


def _rule_dict(rule: AlertRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "metric": rule.metric,
        "comparator": rule.comparator,
        "threshold": rule.threshold,
        "window_minutes": rule.window_minutes,
        "cooldown_minutes": rule.cooldown_minutes,
        "scope_kind": rule.scope_kind,
        "scope_id": rule.scope_id,
        "channels": list(rule.channels or []),
        "enabled": bool(rule.enabled),
        "description": evaluator.describe(
            evaluator.AlertRuleView.from_row(rule)
        ),
        "created_by": rule.created_by,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def _event_dict(event: AlertEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "rule_id": str(event.rule_id) if event.rule_id else None,
        "metric": event.metric,
        "observed_value": event.observed_value,
        "threshold": event.threshold,
        "state": event.state,
        "deliveries": list(event.deliveries or []),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


async def _load_rule(session: AsyncSession, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> AlertRule:
    rule = await session.get(AlertRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "rule not found"})
    return rule


@router.get("/rules")
async def list_rules(
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(AlertRule)
            .where(AlertRule.tenant_id == ctx.tenant_id)
            .order_by(AlertRule.name)
        )
    ).all()
    return {
        "rules": [_rule_dict(row) for row in rows],
        "metrics": list(evaluator.METRICS),
        "comparators": list(evaluator.COMPARATORS),
    }


@router.post("/rules", status_code=201)
async def create_rule(
    payload: RuleBody,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    reject_client_override(ctx, payload.tenant_id)
    existing = await session.scalar(
        select(AlertRule).where(
            AlertRule.tenant_id == ctx.tenant_id, AlertRule.name == payload.name
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "name_taken", "message": f"a rule named {payload.name!r} exists"},
        )
    count = len(
        (
            await session.scalars(select(AlertRule).where(AlertRule.tenant_id == ctx.tenant_id))
        ).all()
    )
    if count >= MAX_RULES_PER_TENANT:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rule_limit",
                "message": f"at most {MAX_RULES_PER_TENANT} rules per tenant",
            },
        )
    rule = AlertRule(
        tenant_id=ctx.tenant_id,
        name=payload.name.strip(),
        metric=payload.metric,
        comparator=payload.comparator,
        threshold=float(payload.threshold),
        window_minutes=payload.window_minutes,
        cooldown_minutes=payload.cooldown_minutes,
        scope_kind=payload.scope_kind,
        scope_id=(payload.scope_id or None),
        channels=list(payload.channels),
        enabled=payload.enabled,
        created_by=str(ctx.user_id) if ctx.user_id else None,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _rule_dict(rule)


@router.patch("/rules/{rule_id}")
async def patch_rule(
    rule_id: uuid.UUID,
    payload: RulePatch,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rule = await _load_rule(session, ctx.tenant_id, rule_id)
    changes = payload.model_dump(exclude_unset=True)
    if "channels" in changes and changes["channels"] is not None:
        RuleBody._channels_shape(changes["channels"])
    for field, value in changes.items():
        if value is None and field not in ("scope_id",):
            continue
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return _rule_dict(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    rule = await _load_rule(session, ctx.tenant_id, rule_id)
    await session.delete(rule)
    await session.commit()
    return None


@router.post("/rules/{rule_id}/test-fire")
async def test_fire(
    rule_id: uuid.UUID,
    payload: TestFireBody,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Evaluate a rule without recording an event (or, on request, with delivery)."""
    rule = await _load_rule(session, ctx.tenant_id, rule_id)
    view = evaluator.AlertRuleView.from_row(rule)
    observed = payload.observed
    if observed is None:
        observed = await evaluator.sample_metric(
            session,
            tenant_id=ctx.tenant_id,
            metric=view.metric,
            window_minutes=view.window_minutes,
            scope_kind=view.scope_kind,
            scope_id=view.scope_id,
        )
    would_breach = observed is not None and evaluator.breaches(
        observed, view.comparator, view.threshold
    )
    body = {
        "rule_id": str(rule.id),
        "metric": view.metric,
        "observed": observed,
        "threshold": view.threshold,
        "comparator": view.comparator,
        "would_breach": would_breach,
        "sampled": payload.observed is None,
        "recorded": False,
        "delivered": [],
    }
    if not would_breach or not payload.deliver:
        return body

    # A real test delivery: the event is written, then delivered. This is the
    # branch an operator opts into when they want to prove the Slack hook works.
    evaluation = await evaluator.evaluate_rule(
        session, ctx.tenant_id, view, observed=observed
    )
    await session.commit()
    body["recorded"] = evaluation.event_id is not None
    body["state"] = evaluation.state
    if evaluation.event_id is not None:
        event = await session.get(AlertEvent, evaluation.event_id)
        if event is not None:
            body["delivered"] = await notifier.deliver(session, event, view)
            await session.commit()
    return body


@router.get("/events")
async def list_events(
    rule_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, pattern="^(fired|suppressed_cooldown|suppressed_dedupe|delivery_failed)$"),
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    query = select(AlertEvent).where(
        AlertEvent.tenant_id == ctx.tenant_id, AlertEvent.created_at >= since
    )
    if rule_id is not None:
        query = query.where(AlertEvent.rule_id == rule_id)
    if state is not None:
        query = query.where(AlertEvent.state == state)
    rows = (
        await session.scalars(query.order_by(AlertEvent.created_at.desc()).limit(limit))
    ).all()
    return {"events": [_event_dict(row) for row in rows], "hours": hours}


@router.post("/evaluate")
async def evaluate_now(
    deliver: bool = Query(default=True),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Evaluate every enabled rule now.

    This is the endpoint the scheduler calls; it is also the manual "check now"
    button. ``deliver=false`` is for a dry run over the whole set.
    """
    rules = await evaluator.load_rules(session, ctx.tenant_id)
    results = await evaluator.evaluate_tenant(session, ctx.tenant_id, rules=rules)
    delivered: list[dict[str, Any]] = []
    if deliver:
        for result in results:
            if result.event_id is None or result.state not in (
                evaluator.STATE_FIRED,
                evaluator.STATE_DELIVERY_FAILED,
            ):
                continue
            event = await session.get(AlertEvent, result.event_id)
            rule = next((r for r in rules if r.id == result.rule_id), None)
            if event is None or rule is None:
                continue
            deliveries = await notifier.deliver(session, event, rule)
            delivered.append({"rule_id": str(rule.id), "deliveries": deliveries})
    await session.commit()
    return {
        "evaluated": len(results),
        "breached": sum(1 for r in results if r.breached),
        "results": [result.as_dict() for result in results],
        "deliveries": delivered,
        "deliver": deliver,
    }
