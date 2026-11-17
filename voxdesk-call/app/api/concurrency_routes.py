"""Concurrency API (PART 13, P13-05).

`GET /api/concurrency` answers the question a customer actually asks -- "how
many calls am I allowed, how many are up right now, and is the burst costing me
money?" -- in one response, because three endpoints that must agree are three
endpoints that drift.

The series endpoint returns what the sampler stored and nothing else. When the
sampler has not run yet the list is empty and `source` says so; a chart of
interpolated zeroes is a lie an operator cannot detect later.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.telephony import concurrency_policy as policy

router = APIRouter(tags=["telephony-concurrency"])

def _manager() -> policy.ConcurrencyManager:
    """The same process-wide manager used by API reads and production dialing."""
    return policy.get_concurrency_manager()


def configure_counter_store(store: policy.CounterStore | None) -> None:
    """Swap the counter store (called from startup when Redis is configured)."""
    policy.configure_counter_store(store)


class LimitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: int = Field(ge=1, le=10_000)
    reserved_inbound: int = Field(ge=0, le=10_000)
    burst_billing: bool = True
    provider_cps: dict[str, int] = Field(default_factory=dict)


@router.get("/api/concurrency")
async def current_concurrency(
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    limit, _row = await policy.load_limit(session, ctx.tenant_id)
    snapshot = await _manager().snapshot(ctx.tenant_id, limit)
    snapshot["series_source"] = "sampler"
    return snapshot


@router.put("/api/concurrency/limits")
async def update_limits(
    payload: LimitUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        limit = policy.Limit.from_payload(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await policy.save_limit(session, ctx.tenant_id, limit)
    await session.commit()
    snapshot = await _manager().snapshot(ctx.tenant_id, limit)
    return snapshot


@router.get("/api/concurrency/series")
async def concurrency_series(
    hours: int = Query(default=24, ge=1, le=168),
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    points = await policy.series(session, ctx.tenant_id, hours=hours)
    return {
        "hours": hours,
        "points": points,
        "source": "sampler" if points else "empty",
        "max_points": policy.MAX_SERIES_POINTS,
    }


@router.post("/api/concurrency/sample", status_code=status.HTTP_201_CREATED)
async def take_sample(
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Write one point now. The scheduler calls the same helper every minute."""
    limit, _row = await policy.load_limit(session, ctx.tenant_id)
    snapshot = await _manager().snapshot(ctx.tenant_id, limit)
    row = await policy.record_sample(session, ctx.tenant_id, snapshot)
    await session.commit()
    return {
        "bucket_start": row.bucket_start.isoformat(),
        "inbound": row.inbound,
        "outbound": row.outbound,
        "peak": row.peak,
    }
