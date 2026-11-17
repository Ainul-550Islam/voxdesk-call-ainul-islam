"""Country policy API (PART 13, P13-06).

CRUD plus a dry-run. The dry-run is the important endpoint: an operator who is
about to publish a deny list can ask "what would this number do?" and get the
matching rule back before a customer's call is refused.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.db.telephony_models import TelephonyCountryPolicy
from app.telephony.country_policy import (
    STAGES,
    CountryPolicyError,
    country_of,
    parse_rules,
    policy_from_rows,
)

router = APIRouter(tags=["telephony-countries"])

DEFAULT_ACTIONS = ("allow", "deny")


class RulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = "tenant"
    scope_id: str | None = None
    country: str = Field(min_length=1, max_length=2)
    action: str = "allow"
    reason: str | None = None


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_action: str = "allow"
    rules: list[RulePayload] = Field(default_factory=list)


class DryRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str
    stage: str = "dry_run"
    number_id: str | None = None
    agent_id: str | None = None


def _serialize(row: TelephonyCountryPolicy) -> dict:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "scope_id": row.scope_id,
        "country": row.country,
        "action": row.action,
        "reason": row.reason,
    }


async def _rows(session: AsyncSession, tenant_id: uuid.UUID) -> list[TelephonyCountryPolicy]:
    return list(
        (
            await session.execute(
                select(TelephonyCountryPolicy)
                .where(TelephonyCountryPolicy.tenant_id == tenant_id)
                .order_by(TelephonyCountryPolicy.scope, TelephonyCountryPolicy.country)
            )
        )
        .scalars()
        .all()
    )


async def _policy(session: AsyncSession, tenant_id: uuid.UUID):
    """The tenant's effective policy, with the default inferred from its rules."""
    return policy_from_rows(await _rows(session, tenant_id))


@router.get("/api/telephony/countries")
async def list_rules(
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await _rows(session, ctx.tenant_id)
    return {"rules": [_serialize(row) for row in rows], "stages": list(STAGES)}


@router.put("/api/telephony/countries")
async def replace_rules(
    payload: RuleSet,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Replace the whole rule set, atomically.

    A partial update would leave a window where a country is neither allowed
    nor denied; replacing is the only edit that cannot be half-applied.
    """
    try:
        rules = parse_rules([rule.model_dump() for rule in payload.rules])
    except CountryPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if payload.default_action not in DEFAULT_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"default_action must be one of {', '.join(DEFAULT_ACTIONS)}",
        )

    seen: set[tuple[str, str | None, str]] = set()
    for rule in rules:
        key = (rule.scope, rule.scope_id, rule.country)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"duplicate rule for {rule.country} in scope {rule.scope}",
            )
        seen.add(key)

    for row in await _rows(session, ctx.tenant_id):
        await session.delete(row)
    for rule in rules:
        session.add(
            TelephonyCountryPolicy(
                tenant_id=ctx.tenant_id,
                scope=rule.scope,
                scope_id=rule.scope_id,
                country=rule.country,
                action=rule.action,
                reason=rule.reason,
            )
        )
    await session.commit()
    rows = await _rows(session, ctx.tenant_id)
    return {
        "rules": [_serialize(row) for row in rows],
        "default_action": payload.default_action,
    }


@router.post("/api/telephony/countries/dry-run")
async def dry_run(
    payload: DryRun,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.stage not in STAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"stage must be one of {', '.join(STAGES)}",
        )
    policy = await _policy(session, ctx.tenant_id)
    decision = policy.decide(
        payload.number,
        stage=payload.stage,
        number_id=payload.number_id,
        agent_id=payload.agent_id,
    )
    return {"decision": decision.as_dict(), "resolved_country": country_of(payload.number)}
