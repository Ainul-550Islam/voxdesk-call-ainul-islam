"""Centralized concurrency, rate, budget, calling-window, and retry policy enforcement (Part 1B / Gate G1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enterprise_models import BatchCall, CallPolicy
from app.db.models import Call, CallStatus, Campaign
from app.db.telephony_models import TelephonyCallSession, TelephonyUsageLedger


@dataclass(frozen=True)
class CallingWindowVerdict:
    allowed: bool
    reason: str | None
    timezone: str
    local_time: str
    next_window_start: datetime | None = None


@dataclass(frozen=True)
class ConcurrencyVerdict:
    allowed: bool
    reason: str | None
    active_calls: int
    max_concurrent_calls: int
    calls_last_minute: int = 0
    max_calls_per_minute: int | None = None


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    next_attempt_at: datetime | None
    delay_seconds: int
    max_attempts: int
    reason: str


def _aware_utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _naive_utc(now: datetime | None = None) -> datetime:
    return _aware_utc(now).replace(tzinfo=None)


def _parse_hhmm(value: str) -> int:
    parts = (value or "00:00").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return hour * 60 + minute


async def resolve_call_policy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: str | None,
    policy_type: str,
) -> CallPolicy | None:
    """Resolve enabled ``CallPolicy`` for ``(tenant_id, agent_id, policy_type)``, falling back to ``agent_id=""``."""
    aid = (agent_id or "").strip()
    if aid:
        row = (
            await db.execute(
                select(CallPolicy).where(
                    CallPolicy.tenant_id == tenant_id,
                    CallPolicy.agent_id == aid,
                    CallPolicy.policy_type == policy_type,
                    CallPolicy.is_enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row

    return (
        await db.execute(
            select(CallPolicy).where(
                CallPolicy.tenant_id == tenant_id,
                CallPolicy.agent_id == "",
                CallPolicy.policy_type == policy_type,
                CallPolicy.is_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()


def _evaluate_window_list(
    local_dt: datetime,
    tz: ZoneInfo,
    windows: list[dict[str, Any]],
) -> CallingWindowVerdict:
    current_day = local_dt.weekday()  # 0=Monday .. 6=Sunday
    current_minutes = local_dt.hour * 60 + local_dt.minute
    local_str = local_dt.strftime("%Y-%m-%d %H:%M %Z")

    by_day: dict[int, dict[str, Any]] = {}
    for w in windows:
        if isinstance(w, dict) and "day" in w:
            by_day[int(w["day"])] = w

    today_win = by_day.get(current_day)
    if today_win is not None and bool(today_win.get("enabled", True)):
        start_raw = str(today_win.get("start", "09:00")).strip()
        end_raw = str(today_win.get("end", "20:00")).strip()
        start_min = _parse_hhmm(start_raw)
        end_min = 1440 if end_raw in ("23:59", "24:00") else _parse_hhmm(end_raw)
        if start_min <= current_minutes < end_min:
            return CallingWindowVerdict(
                allowed=True,
                reason=None,
                timezone=str(tz),
                local_time=local_str,
                next_window_start=None,
            )

    # Compute next enabled window start in UTC
    for day_offset in range(0, 8):
        cand_date = (local_dt + timedelta(days=day_offset)).date()
        cand_day = cand_date.weekday()
        win = by_day.get(cand_day)
        if not win or not bool(win.get("enabled", True)):
            continue
        start_min = _parse_hhmm(str(win.get("start", "09:00")))
        cand_local = datetime(
            cand_date.year,
            cand_date.month,
            cand_date.day,
            start_min // 60,
            start_min % 60,
            tzinfo=tz,
        )
        if cand_local > local_dt:
            return CallingWindowVerdict(
                allowed=False,
                reason=f"outside_calling_window ({local_str})",
                timezone=str(tz),
                local_time=local_str,
                next_window_start=cand_local.astimezone(timezone.utc),
            )

    return CallingWindowVerdict(
        allowed=False,
        reason=f"outside_calling_window ({local_str})",
        timezone=str(tz),
        local_time=local_str,
        next_window_start=None,
    )


async def check_calling_window(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str | None = None,
    batch: BatchCall | None = None,
    now: datetime | None = None,
) -> CallingWindowVerdict:
    """Check whether dialing is permitted at ``now`` under the agent/tenant/batch calling window."""
    now_utc = _aware_utc(now)
    policy = await resolve_call_policy(db, tenant_id, agent_id, "calling_window")

    if policy is not None and isinstance(policy.config, dict):
        tz_name = str(policy.config.get("timezone") or "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        local_dt = now_utc.astimezone(tz)
        windows = list(policy.config.get("windows") or [])
        if windows:
            verdict = _evaluate_window_list(local_dt, tz, windows)
            if not verdict.allowed:
                return verdict

    if batch is not None and batch.calling_window_start and batch.calling_window_end:
        win_start = batch.calling_window_start
        win_end = batch.calling_window_end
        if win_start == "09:00" and win_end == "20:00":
            from app.db.models import Tenant

            tenant = await db.get(Tenant, tenant_id)
            open_val = getattr(tenant, "outbound_window_open", None) if tenant is not None else None
            close_val = getattr(tenant, "outbound_window_close", None) if tenant is not None else None
            if (
                open_val is not None
                and close_val is not None
                and (
                    (open_val == "00:00" and close_val in ("23:59", "24:00"))
                    or (
                        getattr(open_val, "hour", -1) == 0
                        and getattr(open_val, "minute", -1) == 0
                        and getattr(close_val, "hour", -1) == 23
                        and getattr(close_val, "minute", -1) == 59
                    )
                )
            ):
                win_start = "00:00"
                win_end = "23:59"
        tz_name = str(batch.timezone or "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        local_dt = now_utc.astimezone(tz)
        windows = [
            {
                "day": d,
                "start": win_start,
                "end": win_end,
                "enabled": True,
            }
            for d in range(7)
        ]
        return _evaluate_window_list(local_dt, tz, windows)

    if policy is not None and isinstance(policy.config, dict):
        tz_name = str(policy.config.get("timezone") or "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        local_dt = now_utc.astimezone(tz)
        return CallingWindowVerdict(
            allowed=True,
            reason=None,
            timezone=str(tz),
            local_time=local_dt.strftime("%Y-%m-%d %H:%M %Z"),
        )

    return CallingWindowVerdict(
        allowed=True,
        reason=None,
        timezone="UTC",
        local_time=now_utc.strftime("%Y-%m-%d %H:%M UTC"),
    )


async def _agent_call_count(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: str | None,
    *,
    active_only: bool = False,
    since_naive: datetime | None = None,
) -> int:
    """Count tenant calls matching ``agent_id`` (or unscoped legacy calls in the same tenant)."""
    aid = (agent_id or "").strip()
    filters = [Call.tenant_id == tenant_id]
    if active_only:
        filters.append(Call.status.in_([CallStatus.RINGING, CallStatus.IN_PROGRESS]))
    if since_naive is not None:
        filters.append(Call.started_at >= since_naive)

    if not aid:
        return int((await db.scalar(select(func.count(Call.id)).where(*filters))) or 0)

    # Exclude calls explicitly bound to a DIFFERENT agent in TelephonyCallSession
    other_sessions = (
        await db.execute(
            select(TelephonyCallSession.id, TelephonyCallSession.provider_call_id).where(
                TelephonyCallSession.tenant_id == tenant_id,
                TelephonyCallSession.agent_id != "",
                TelephonyCallSession.agent_id != aid,
            )
        )
    ).all()
    other_ids = {r[0] for r in other_sessions if r[0]}
    other_sids = {r[1] for r in other_sessions if r[1]}

    rows = (
        await db.execute(select(Call.id, Call.call_sid).where(*filters))
    ).all()
    matched = [
        r[0]
        for r in rows
        if r[0] not in other_ids and (not r[1] or r[1] not in other_sids)
    ]
    return len(matched)


async def check_concurrency_and_rate(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str | None = None,
    campaign: Campaign | None = None,
    batch: BatchCall | None = None,
    now: datetime | None = None,
) -> ConcurrencyVerdict:
    """Check concurrency, per-minute/hour/day rate, and daily budget limits."""
    now_naive = _naive_utc(now)
    policy = await resolve_call_policy(db, tenant_id, agent_id, "concurrency")
    cfg = policy.config if (policy is not None and isinstance(policy.config, dict)) else {}

    max_concurrent = int(cfg.get("max_concurrent_calls") or 0)
    if batch is not None and batch.concurrency:
        max_concurrent = min(max_concurrent, int(batch.concurrency)) if max_concurrent > 0 else int(batch.concurrency)
    if max_concurrent <= 0:
        max_concurrent = 10

    max_per_min = int(cfg.get("max_calls_per_minute") or 0) or None
    if campaign is not None and campaign.calls_per_minute:
        c_rpm = max(1, int(campaign.calls_per_minute))
        max_per_min = min(max_per_min, c_rpm) if max_per_min is not None else c_rpm

    max_per_hour = int(cfg.get("max_calls_per_hour") or 0) or None
    max_per_day = int(cfg.get("max_calls_per_day") or 0) or None
    budget_cents = cfg.get("budget_cents_per_day")

    active_calls = await _agent_call_count(
        db, tenant_id, agent_id, active_only=True
    )
    if active_calls >= max_concurrent:
        return ConcurrencyVerdict(
            allowed=False,
            reason=f"max_concurrent_calls_exceeded ({active_calls}/{max_concurrent})",
            active_calls=active_calls,
            max_concurrent_calls=max_concurrent,
        )

    calls_last_min = await _agent_call_count(
        db, tenant_id, agent_id, since_naive=now_naive - timedelta(minutes=1)
    )
    if max_per_min is not None and calls_last_min >= max_per_min:
        return ConcurrencyVerdict(
            allowed=False,
            reason=f"max_calls_per_minute_exceeded ({calls_last_min}/{max_per_min})",
            active_calls=active_calls,
            max_concurrent_calls=max_concurrent,
            calls_last_minute=calls_last_min,
            max_calls_per_minute=max_per_min,
        )

    if max_per_hour is not None:
        calls_last_hour = await _agent_call_count(
            db, tenant_id, agent_id, since_naive=now_naive - timedelta(hours=1)
        )
        if calls_last_hour >= max_per_hour:
            return ConcurrencyVerdict(
                allowed=False,
                reason=f"max_calls_per_hour_exceeded ({calls_last_hour}/{max_per_hour})",
                active_calls=active_calls,
                max_concurrent_calls=max_concurrent,
                calls_last_minute=calls_last_min,
                max_calls_per_minute=max_per_min,
            )

    if max_per_day is not None:
        calls_last_day = await _agent_call_count(
            db, tenant_id, agent_id, since_naive=now_naive - timedelta(days=1)
        )
        if calls_last_day >= max_per_day:
            return ConcurrencyVerdict(
                allowed=False,
                reason=f"max_calls_per_day_exceeded ({calls_last_day}/{max_per_day})",
                active_calls=active_calls,
                max_concurrent_calls=max_concurrent,
                calls_last_minute=calls_last_min,
                max_calls_per_minute=max_per_min,
            )

    if budget_cents is not None:
        day_start = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = int(
            (
                await db.scalar(
                    select(func.coalesce(func.sum(TelephonyUsageLedger.BillableUnits), 0)).where(
                        TelephonyUsageLedger.tenant_id == tenant_id,
                        TelephonyUsageLedger.created_at >= day_start,
                    )
                )
            )
            or 0
        ) if hasattr(TelephonyUsageLedger, "BillableUnits") else 0
        if spent > int(budget_cents):
            return ConcurrencyVerdict(
                allowed=False,
                reason=f"budget_cents_per_day_exceeded ({spent}/{budget_cents})",
                active_calls=active_calls,
                max_concurrent_calls=max_concurrent,
                calls_last_minute=calls_last_min,
                max_calls_per_minute=max_per_min,
            )

    return ConcurrencyVerdict(
        allowed=True,
        reason=None,
        active_calls=active_calls,
        max_concurrent_calls=max_concurrent,
        calls_last_minute=calls_last_min,
        max_calls_per_minute=max_per_min,
    )


async def compute_retry_schedule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str | None = None,
    attempt: int,
    outcome: str = "failed",
    batch: BatchCall | None = None,
    now: datetime | None = None,
) -> RetryDecision:
    """Compute exponential-backoff retry schedule from ``CallPolicy(policy_type='retry')`` or ``BatchCall``."""
    now_utc = _aware_utc(now)
    policy = await resolve_call_policy(db, tenant_id, agent_id, "retry")
    cfg = policy.config if (policy is not None and isinstance(policy.config, dict)) else {}

    max_attempts = int(
        cfg.get("max_attempts")
        or (batch.max_attempts if batch is not None else 3)
        or 3
    )
    base_delay = int(
        cfg.get("retry_delay_seconds")
        or (batch.retry_delay_seconds if batch is not None else 3600)
        or 3600
    )
    multiplier = float(cfg.get("backoff_multiplier") or 2.0)
    max_delay = int(cfg.get("max_delay_seconds") or 86400)
    retry_on = [str(x).lower() for x in (cfg.get("retry_on") or ["no_answer", "busy", "failed"])]

    norm_outcome = (outcome or "failed").strip().lower()
    if norm_outcome not in retry_on:
        return RetryDecision(
            should_retry=False,
            next_attempt_at=None,
            delay_seconds=0,
            max_attempts=max_attempts,
            reason=f"outcome_not_retryable:{norm_outcome}",
        )

    if attempt >= max_attempts:
        return RetryDecision(
            should_retry=False,
            next_attempt_at=None,
            delay_seconds=0,
            max_attempts=max_attempts,
            reason=f"max_attempts_reached ({attempt}/{max_attempts})",
        )

    exponent = max(0, int(attempt) - 1)
    delay = min(int(round(base_delay * (multiplier ** exponent))), max_delay)
    return RetryDecision(
        should_retry=True,
        next_attempt_at=now_utc + timedelta(seconds=delay),
        delay_seconds=delay,
        max_attempts=max_attempts,
        reason="retry_scheduled",
    )
