"""Analytics aggregation service (Part 6 / Gate G7).

Computes tenant-scoped call & quality metrics:
- Success rate, average duration, p95 duration
- Cost per call (`app/ai/costs.py` + telephony voice minutes & token usage)
- Latency p50 / p95 (`CallLatencyStat` with `Call.avg_response_ms` fallback)
- Transfer rate & voicemail rate
- Disconnect reasons breakdown
- Caller sentiment breakdown (`SentimentResult` & `PostCallStepRun`)
- Group-by dimensions: `agent`, `version`, `number`, `day`, `status`, `disconnect_reason`, `sentiment`
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models as _db_models  # noqa: F401 — import before telephony_models
from app.ai import costs
from app.core.config import settings
from app.core.errors import BadRequestError
from app.db.enterprise_models import BatchRecipient, BatchRecipientStatus
from app.db.models import (
    Agent,
    AgentVersion,
    Appointment,
    Call,
    CallDirection,
    CallStatus,
    Lead,
    TransferState,
    UsageEvent,
    UsageMetric,
)
from app.db.telephony_models import CallLatencyStat, PostCallStepRun
from app.domain.analytics_models import (
    CallKpi,
    CostKpi,
    KpiGranularity,
    KpiKind,
    KpiPoint,
    KpiSnapshot,
)
from app.qa.models import SentimentResult

ALLOWED_GROUP_DIMENSIONS = frozenset(
    {
        "day",
        "agent",
        "version",
        "number",
        "status",
        "disconnect_reason",
        "sentiment",
        "direction",
        "none",
    }
)

ALLOWED_METRICS = frozenset(
    {
        "total_calls",
        "success_rate",
        "avg_duration_seconds",
        "p95_duration_seconds",
        "avg_cost_per_call_usd",
        "total_cost_usd",
        "latency_p50_ms",
        "latency_p95_ms",
        "transfer_rate",
        "voicemail_rate",
        "disconnect_reasons",
        "sentiment",
    }
)


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(float(sorted_vals[0]), 2)
    idx = (len(sorted_vals) - 1) * max(0.0, min(1.0, q))
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    val = sorted_vals[lower] * (1.0 - weight) + sorted_vals[upper] * weight
    return round(float(val), 2)


def _pct(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _extract_provider(call: Call) -> str:
    raw = str(call.llm_used or "").strip().lower()
    if "/" in raw:
        provider = raw.split("/", 1)[0].strip()
        if provider in {"openai", "anthropic", "google", "groq"}:
            return provider
    if raw in {"openai", "anthropic", "google", "groq"}:
        return raw
    return "openai"


def _estimate_call_cost_usd(call: Call) -> float:
    dur_sec = max(0.0, float(call.duration_seconds or 0.0))
    voice_minutes = dur_sec / 60.0
    provider = _extract_provider(call)
    est_tokens = max(0, int(dur_sec * 12)) if dur_sec > 0 else 0
    est = costs.estimate(
        provider=provider,
        tokens=est_tokens if est_tokens > 0 else None,
        voice_minutes=voice_minutes if voice_minutes > 0 else None,
    )
    if est.get("known") and est.get("provider_cost_usd") is not None:
        return round(float(est["provider_cost_usd"]), 6)
    fallback = costs.estimate(
        provider="openai",
        tokens=est_tokens if est_tokens > 0 else None,
        voice_minutes=voice_minutes if voice_minutes > 0 else None,
    )
    if fallback.get("known") and fallback.get("provider_cost_usd") is not None:
        return round(float(fallback["provider_cost_usd"]), 6)
    return round(voice_minutes * 0.015, 6)


def _classify_disconnect_reason(call: Call, is_voicemail: bool) -> str:
    if is_voicemail:
        return "voicemail"
    reason = str(call.failure_reason or "").strip().lower()
    if reason:
        if "voicemail" in reason or "machine" in reason:
            return "voicemail"
        if "busy" in reason:
            return "busy"
        if "no-answer" in reason or "no_answer" in reason:
            return "no_answer"
        if "caller" in reason or "user" in reason:
            return "caller_hangup"
        return reason[:40]
    status_val = (
        call.status.value if hasattr(call.status, "value") else str(call.status)
    ).lower()
    t_state = (
        call.transfer_state.value
        if hasattr(call.transfer_state, "value")
        else str(call.transfer_state)
    ).lower()
    if bool(call.escalated) or status_val == CallStatus.TRANSFERRED.value or t_state != TransferState.NONE.value:
        return "transferred"
    if status_val == CallStatus.NO_ANSWER.value:
        return "no_answer"
    if status_val == CallStatus.FAILED.value:
        return "failed"
    if status_val == CallStatus.COMPLETED.value:
        return "completed"
    return status_val or "unknown"


def _is_call_transferred(call: Call) -> bool:
    status_val = (
        call.status.value if hasattr(call.status, "value") else str(call.status)
    ).lower()
    t_state = (
        call.transfer_state.value
        if hasattr(call.transfer_state, "value")
        else str(call.transfer_state)
    ).lower()
    return bool(
        call.escalated
        or status_val == CallStatus.TRANSFERRED.value
        or t_state != TransferState.NONE.value
    )


def _is_call_voicemail(call: Call, vm_call_ids: set[uuid.UUID]) -> bool:
    if call.id in vm_call_ids:
        return True
    reason = str(call.failure_reason or "").strip().lower()
    intent = str(call.intent or "").strip().lower()
    return (
        "voicemail" in reason
        or "machine" in reason
        or intent == "voicemail"
    )


def _is_call_successful(call: Call, is_voicemail: bool) -> bool:
    if is_voicemail:
        return False
    status_val = (
        call.status.value if hasattr(call.status, "value") else str(call.status)
    ).lower()
    return bool(call.booked or status_val == CallStatus.COMPLETED.value)


def _aggregate_call_bucket(
    calls: list[Call],
    *,
    latency_by_call: dict[uuid.UUID, CallLatencyStat],
    sentiment_by_call: dict[uuid.UUID, str],
    vm_call_ids: set[uuid.UUID],
) -> dict[str, Any]:
    total = len(calls)
    if total == 0:
        return {
            "total_calls": 0,
            "completed_calls": 0,
            "successful_calls": 0,
            "success_rate": 0.0,
            "avg_duration_seconds": 0.0,
            "p95_duration_seconds": 0.0,
            "total_cost_usd": 0.0,
            "avg_cost_per_call_usd": 0.0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
            "latency": {
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "stt_p50_ms": 0.0,
                "stt_p95_ms": 0.0,
                "llm_ttfb_p50_ms": 0.0,
                "llm_ttfb_p95_ms": 0.0,
                "tts_ttfb_p50_ms": 0.0,
                "tts_ttfb_p95_ms": 0.0,
            },
            "transfer_count": 0,
            "transfer_rate": 0.0,
            "voicemail_count": 0,
            "voicemail_rate": 0.0,
            "disconnect_reasons": {},
            "sentiment": {
                "positive": 0,
                "neutral": 0,
                "negative": 0,
                "unknown": 0,
                "positive_rate": 0.0,
                "negative_rate": 0.0,
            },
        }

    durations = sorted(float(c.duration_seconds or 0.0) for c in calls)
    avg_dur = round(sum(durations) / total, 2)
    p95_dur = _percentile(durations, 0.95)

    completed_count = 0
    success_count = 0
    transfer_count = 0
    voicemail_count = 0
    total_cost = 0.0
    disconnect_reasons: dict[str, int] = {}
    sentiment_counts: dict[str, int] = {
        "positive": 0,
        "neutral": 0,
        "negative": 0,
        "unknown": 0,
    }

    e2e_samples: list[float] = []
    e2e_p95_samples: list[float] = []
    stt_p50_samples: list[float] = []
    stt_p95_samples: list[float] = []
    llm_p50_samples: list[float] = []
    llm_p95_samples: list[float] = []
    tts_p50_samples: list[float] = []
    tts_p95_samples: list[float] = []

    for c in calls:
        status_val = (
            c.status.value if hasattr(c.status, "value") else str(c.status)
        ).lower()
        if status_val in {CallStatus.COMPLETED.value, CallStatus.TRANSFERRED.value}:
            completed_count += 1

        is_vm = _is_call_voicemail(c, vm_call_ids)
        if is_vm:
            voicemail_count += 1
        if _is_call_transferred(c):
            transfer_count += 1
        if _is_call_successful(c, is_vm):
            success_count += 1

        total_cost += _estimate_call_cost_usd(c)

        d_reason = _classify_disconnect_reason(c, is_vm)
        disconnect_reasons[d_reason] = disconnect_reasons.get(d_reason, 0) + 1

        sent_label = sentiment_by_call.get(c.id, "unknown")
        if sent_label not in sentiment_counts:
            sent_label = "unknown"
        sentiment_counts[sent_label] += 1

        lat = latency_by_call.get(c.id)
        if lat is not None:
            e2e_50 = lat.e2e_p50_ms if lat.e2e_p50_ms is not None else lat.e2e_ms
            e2e_95 = lat.e2e_p95_ms if lat.e2e_p95_ms is not None else e2e_50
            if e2e_50 is not None:
                e2e_samples.append(float(e2e_50))
            if e2e_95 is not None:
                e2e_p95_samples.append(float(e2e_95))
            if (lat.stt_ttfb_p50_ms or lat.stt_ms) is not None:
                stt_p50_samples.append(float(lat.stt_ttfb_p50_ms if lat.stt_ttfb_p50_ms is not None else lat.stt_ms))
            if (lat.stt_ttfb_p95_ms or lat.stt_ms) is not None:
                stt_p95_samples.append(float(lat.stt_ttfb_p95_ms if lat.stt_ttfb_p95_ms is not None else lat.stt_ms))
            if lat.llm_ttfb_p50_ms is not None or lat.llm_ttfb_ms is not None:
                llm_p50_samples.append(float(lat.llm_ttfb_p50_ms if lat.llm_ttfb_p50_ms is not None else lat.llm_ttfb_ms))
            if lat.llm_ttfb_p95_ms is not None or lat.llm_ttfb_ms is not None:
                llm_p95_samples.append(float(lat.llm_ttfb_p95_ms if lat.llm_ttfb_p95_ms is not None else lat.llm_ttfb_ms))
            if lat.tts_ttfb_p50_ms is not None or lat.tts_ttfb_ms is not None:
                tts_p50_samples.append(float(lat.tts_ttfb_p50_ms if lat.tts_ttfb_p50_ms is not None else lat.tts_ttfb_ms))
            if lat.tts_ttfb_p95_ms is not None or lat.tts_ttfb_ms is not None:
                tts_p95_samples.append(float(lat.tts_ttfb_p95_ms if lat.tts_ttfb_p95_ms is not None else lat.tts_ttfb_ms))
        elif c.avg_response_ms is not None:
            e2e_samples.append(float(c.avg_response_ms))
            e2e_p95_samples.append(float(c.avg_response_ms))

    e2e_samples.sort()
    e2e_p95_samples.sort()
    stt_p50_samples.sort()
    stt_p95_samples.sort()
    llm_p50_samples.sort()
    llm_p95_samples.sort()
    tts_p50_samples.sort()
    tts_p95_samples.sort()

    lat_p50 = _percentile(e2e_samples, 0.50)
    lat_p95 = _percentile(e2e_p95_samples, 0.95) if e2e_p95_samples else _percentile(e2e_samples, 0.95)
    total_cost_rounded = round(total_cost, 6)
    avg_cost = round(total_cost / total, 6)
    classified_sentiment = (
        sentiment_counts["positive"]
        + sentiment_counts["neutral"]
        + sentiment_counts["negative"]
    )
    sent_denom = classified_sentiment if classified_sentiment > 0 else total

    return {
        "total_calls": total,
        "completed_calls": completed_count,
        "successful_calls": success_count,
        "success_rate": _pct(success_count, total),
        "avg_duration_seconds": avg_dur,
        "p95_duration_seconds": p95_dur,
        "total_cost_usd": total_cost_rounded,
        "avg_cost_per_call_usd": avg_cost,
        "latency_p50_ms": lat_p50,
        "latency_p95_ms": lat_p95,
        "latency": {
            "p50_ms": lat_p50,
            "p95_ms": lat_p95,
            "stt_p50_ms": _percentile(stt_p50_samples, 0.50),
            "stt_p95_ms": _percentile(stt_p95_samples, 0.95),
            "llm_ttfb_p50_ms": _percentile(llm_p50_samples, 0.50),
            "llm_ttfb_p95_ms": _percentile(llm_p95_samples, 0.95),
            "tts_ttfb_p50_ms": _percentile(tts_p50_samples, 0.50),
            "tts_ttfb_p95_ms": _percentile(tts_p95_samples, 0.95),
        },
        "transfer_count": transfer_count,
        "transfer_rate": _pct(transfer_count, total),
        "voicemail_count": voicemail_count,
        "voicemail_rate": _pct(voicemail_count, total),
        "disconnect_reasons": disconnect_reasons,
        "sentiment": {
            **sentiment_counts,
            "positive_rate": _pct(sentiment_counts["positive"], sent_denom),
            "negative_rate": _pct(sentiment_counts["negative"], sent_denom),
        },
    }


async def compute_metrics_and_breakdown(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    group_by: str = "day",
    filters: dict[str, Any] | None = None,
    max_groups: int = 100,
) -> dict[str, Any]:
    """Compute tenant-scoped rich analytics summary and grouped breakdown."""
    dim = (group_by or "day").strip().lower()
    if dim not in ALLOWED_GROUP_DIMENSIONS:
        raise ValueError(
            f"Unsupported group_by dimension {group_by!r}; expected one of {sorted(ALLOWED_GROUP_DIMENSIONS)}"
        )

    flt = dict(filters or {})
    stmt = select(Call).where(Call.tenant_id == tenant_id)
    if start is not None:
        stmt = stmt.where(Call.started_at >= start)
    if end is not None:
        stmt = stmt.where(Call.started_at < end)

    if flt.get("agent_id"):
        try:
            stmt = stmt.where(Call.agent_id == uuid.UUID(str(flt["agent_id"])))
        except ValueError:
            return {
                "summary": _aggregate_call_bucket(
                    [], latency_by_call={}, sentiment_by_call={}, vm_call_ids=set()
                ),
                "group_by": dim,
                "groups": [],
            }
    if flt.get("agent_version_id"):
        try:
            stmt = stmt.where(
                Call.agent_version_id == uuid.UUID(str(flt["agent_version_id"]))
            )
        except ValueError:
            pass
    if flt.get("number"):
        num_str = str(flt["number"]).strip()
        stmt = stmt.where(
            (Call.to_number == num_str) | (Call.from_number == num_str)
        )
    if flt.get("direction"):
        dir_str = str(flt["direction"]).strip().lower()
        for d_enum in CallDirection:
            if d_enum.value == dir_str:
                stmt = stmt.where(Call.direction == d_enum)
                break
    if flt.get("status"):
        st_str = str(flt["status"]).strip().lower()
        for s_enum in CallStatus:
            if s_enum.value == st_str:
                stmt = stmt.where(Call.status == s_enum)
                break
    if "booked" in flt and flt["booked"] is not None:
        stmt = stmt.where(Call.booked.is_(bool(flt["booked"])))
    if "escalated" in flt and flt["escalated"] is not None:
        stmt = stmt.where(Call.escalated.is_(bool(flt["escalated"])))

    stmt = stmt.order_by(Call.started_at.asc())
    calls = list((await db.execute(stmt)).scalars().all())
    call_ids = [c.id for c in calls]

    latency_by_call: dict[uuid.UUID, CallLatencyStat] = {}
    sentiment_by_call: dict[uuid.UUID, str] = {}
    vm_call_ids: set[uuid.UUID] = set()

    if call_ids:
        lat_rows = list(
            (
                await db.execute(
                    select(CallLatencyStat).where(
                        CallLatencyStat.tenant_id == tenant_id,
                        CallLatencyStat.call_id.in_(call_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for lr in lat_rows:
            latency_by_call[lr.call_id] = lr

        sent_rows = list(
            (
                await db.execute(
                    select(SentimentResult)
                    .where(
                        SentimentResult.tenant_id == tenant_id,
                        SentimentResult.call_id.in_(call_ids),
                    )
                    .order_by(SentimentResult.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for sr in sent_rows:
            sentiment_by_call[sr.call_id] = str(sr.label or "unknown").lower()

        step_rows = list(
            (
                await db.execute(
                    select(PostCallStepRun).where(
                        PostCallStepRun.tenant_id == tenant_id,
                        PostCallStepRun.call_id.in_(call_ids),
                        PostCallStepRun.step == "sentiment",
                    )
                )
            )
            .scalars()
            .all()
        )
        for st_row in step_rows:
            if st_row.call_id not in sentiment_by_call and isinstance(st_row.output, dict):
                lbl = str(
                    st_row.output.get("label") or st_row.output.get("sentiment") or ""
                ).lower()
                if lbl in {"positive", "neutral", "negative"}:
                    sentiment_by_call[st_row.call_id] = lbl

        vm_rows = list(
            (
                await db.execute(
                    select(BatchRecipient.call_id).where(
                        BatchRecipient.tenant_id == tenant_id,
                        BatchRecipient.call_id.in_(call_ids),
                        BatchRecipient.status == BatchRecipientStatus.VOICEMAIL.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        vm_call_ids = {cid for cid in vm_rows if cid is not None}

    if flt.get("sentiment"):
        wanted_sent = str(flt["sentiment"]).strip().lower()
        calls = [
            c
            for c in calls
            if sentiment_by_call.get(c.id, "unknown") == wanted_sent
        ]

    if flt.get("disconnect_reason"):
        wanted_dr = str(flt["disconnect_reason"]).strip().lower()
        calls = [
            c
            for c in calls
            if _classify_disconnect_reason(c, _is_call_voicemail(c, vm_call_ids))
            == wanted_dr
        ]

    summary = _aggregate_call_bucket(
        calls,
        latency_by_call=latency_by_call,
        sentiment_by_call=sentiment_by_call,
        vm_call_ids=vm_call_ids,
    )

    if dim == "none":
        return {"summary": summary, "group_by": dim, "groups": []}

    # Resolve agent names and version numbers for readable group labels
    agent_labels: dict[str, str] = {}
    version_labels: dict[str, str] = {}
    if dim == "agent":
        agent_ids = {c.agent_id for c in calls if c.agent_id is not None}
        if agent_ids:
            ag_rows = list(
                (
                    await db.execute(
                        select(Agent).where(
                            Agent.tenant_id == tenant_id,
                            Agent.id.in_(agent_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for ag in ag_rows:
                agent_labels[str(ag.id)] = ag.name
    elif dim == "version":
        ver_ids = {c.agent_version_id for c in calls if c.agent_version_id is not None}
        if ver_ids:
            v_rows = list(
                (
                    await db.execute(
                        select(AgentVersion).where(
                            AgentVersion.tenant_id == tenant_id,
                            AgentVersion.id.in_(ver_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for vr in v_rows:
                version_labels[str(vr.id)] = f"v{vr.version_number}"

    buckets: dict[str, list[Call]] = {}
    labels: dict[str, str] = {}

    for c in calls:
        if dim == "day":
            key = (
                c.started_at.strftime("%Y-%m-%d")
                if c.started_at
                else "unknown"
            )
            label = key
        elif dim == "agent":
            key = str(c.agent_id) if c.agent_id else "unassigned"
            label = agent_labels.get(key, key)
        elif dim == "version":
            key = str(c.agent_version_id) if c.agent_version_id else "unpinned"
            label = version_labels.get(key, key)
        elif dim == "number":
            dir_val = (
                c.direction.value
                if hasattr(c.direction, "value")
                else str(c.direction)
            ).lower()
            key = (
                (c.from_number if dir_val == CallDirection.OUTBOUND.value else c.to_number)
                or c.to_number
                or "unknown"
            )
            label = key
        elif dim == "status":
            key = (
                c.status.value if hasattr(c.status, "value") else str(c.status)
            ).lower()
            label = key
        elif dim == "disconnect_reason":
            key = _classify_disconnect_reason(c, _is_call_voicemail(c, vm_call_ids))
            label = key
        elif dim == "sentiment":
            key = sentiment_by_call.get(c.id, "unknown")
            label = key
        elif dim == "direction":
            key = (
                c.direction.value
                if hasattr(c.direction, "value")
                else str(c.direction)
            ).lower()
            label = key
        else:
            key = "all"
            label = "All"

        buckets.setdefault(key, []).append(c)
        labels[key] = label

    bounded_limit = max(1, min(int(max_groups or 100), 366))
    groups: list[dict[str, Any]] = []
    for key in sorted(buckets.keys())[:bounded_limit]:
        agg = _aggregate_call_bucket(
            buckets[key],
            latency_by_call=latency_by_call,
            sentiment_by_call=sentiment_by_call,
            vm_call_ids=vm_call_ids,
        )
        groups.append(
            {
                "dimension": dim,
                "key": key,
                "label": labels.get(key, key),
                **agg,
            }
        )

    return {
        "summary": summary,
        "group_by": dim,
        "groups": groups,
    }


async def overview(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Overview metrics enriched with latency p50/p95, cost per call, sentiment, and disconnect reasons."""
    rich = await compute_metrics_and_breakdown(
        db, tenant_id, group_by="none"
    )
    summary = rich["summary"]

    leads_count = (
        await db.execute(
            select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)
        )
    ).scalar_one() or 0
    appt_count = (
        await db.execute(
            select(func.count(Appointment.id)).where(
                Appointment.tenant_id == tenant_id
            )
        )
    ).scalar_one() or 0

    booked_calls = (
        await db.execute(
            select(func.count(Call.id)).where(
                Call.tenant_id == tenant_id,
                Call.booked.is_(True),
            )
        )
    ).scalar_one() or 0

    total = int(summary["total_calls"])
    return {
        "total_calls": total,
        "completed_calls": int(summary["completed_calls"]),
        "successful_calls": int(summary["successful_calls"]),
        "success_rate": float(summary["success_rate"]),
        "booked_calls": int(booked_calls),
        "escalated_calls": int(summary["transfer_count"]),
        "booking_rate": round(booked_calls / total, 4) if total else 0.0,
        "escalation_rate": round(summary["transfer_count"] / total, 4) if total else 0.0,
        "transfer_rate": float(summary["transfer_rate"]),
        "voicemail_rate": float(summary["voicemail_rate"]),
        "avg_duration_seconds": float(summary["avg_duration_seconds"]),
        "p95_duration_seconds": float(summary["p95_duration_seconds"]),
        "avg_latency_ms": float(summary["latency_p50_ms"]),
        "latency_p50_ms": float(summary["latency_p50_ms"]),
        "latency_p95_ms": float(summary["latency_p95_ms"]),
        "total_cost_usd": float(summary["total_cost_usd"]),
        "avg_cost_per_call_usd": float(summary["avg_cost_per_call_usd"]),
        "disconnect_reasons": dict(summary["disconnect_reasons"]),
        "sentiment": dict(summary["sentiment"]),
        "total_leads": int(leads_count),
        "total_appointments": int(appt_count),
    }


async def trends(
    db: AsyncSession, tenant_id: uuid.UUID, days: int = 14
) -> list[dict]:
    """Daily call volume, bookings, duration, latency, and cost for the last N days."""
    bounded_days = max(1, min(int(days or 14), 366))
    since = datetime.now(timezone.utc) - timedelta(days=bounded_days)
    rich = await compute_metrics_and_breakdown(
        db,
        tenant_id,
        start=since,
        group_by="day",
        max_groups=bounded_days + 2,
    )
    by_day = {g["key"]: g for g in rich["groups"]}

    # Also count booked calls per day
    stmt = select(Call.started_at, Call.booked).where(
        Call.tenant_id == tenant_id,
        Call.started_at >= since,
    )
    rows = (await db.execute(stmt)).all()
    booked_by_day: dict[str, int] = {}
    for started_at, booked in rows:
        if started_at and booked:
            day_str = started_at.strftime("%Y-%m-%d")
            booked_by_day[day_str] = booked_by_day.get(day_str, 0) + 1

    out: list[dict[str, Any]] = []
    for day_key in sorted(by_day.keys()):
        g = by_day[day_key]
        out.append(
            {
                "date": day_key,
                "calls": g["total_calls"],
                "booked": booked_by_day.get(day_key, 0),
                "success_rate": g["success_rate"],
                "avg_duration_seconds": g["avg_duration_seconds"],
                "p95_duration_seconds": g["p95_duration_seconds"],
                "latency_p50_ms": g["latency_p50_ms"],
                "latency_p95_ms": g["latency_p95_ms"],
                "total_cost_usd": g["total_cost_usd"],
                "avg_cost_per_call_usd": g["avg_cost_per_call_usd"],
                "transfer_rate": g["transfer_rate"],
                "voicemail_rate": g["voicemail_rate"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Domain KPI helpers (app.domain.analytics_models)
# ---------------------------------------------------------------------------


def _validate_range(start: datetime | None, end: datetime | None) -> tuple[datetime | None, datetime | None]:
    def _norm(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    s = _norm(start)
    e = _norm(end)
    if s is not None and e is not None and s > e:
        raise BadRequestError("start must be on or before end")
    return s, e


async def call_kpis(
    db: AsyncSession,
    tenant_id: str | uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CallKpi:
    tid = uuid.UUID(str(tenant_id))
    s, e = _validate_range(start, end)
    stmt = select(Call).where(Call.tenant_id == tid)
    if s is not None:
        stmt = stmt.where(Call.started_at >= s)
    if e is not None:
        stmt = stmt.where(Call.started_at <= e)
    rows = list((await db.scalars(stmt)).all())
    total = len(rows)
    completed = sum(1 for r in rows if r.status == CallStatus.COMPLETED)
    transferred = sum(
        1
        for r in rows
        if r.status == CallStatus.TRANSFERRED
        or (r.transfer_state is not None and str(r.transfer_state).lower() not in {"none", "transferstate.none", ""})
    )
    failed = sum(1 for r in rows if r.status == CallStatus.FAILED)
    no_answer = sum(
        1 for r in rows if r.status == CallStatus.NO_ANSWER
    )
    answered = sum(
        1
        for r in rows
        if r.status
        in (CallStatus.COMPLETED, CallStatus.TRANSFERRED, CallStatus.IN_PROGRESS)
    )
    escalated = sum(1 for r in rows if bool(r.escalated))
    durations = [float(r.duration_seconds) for r in rows if r.duration_seconds is not None]
    avg_dur = round(sum(durations) / len(durations), 2) if durations else 0.0
    return CallKpi(
        total=total,
        answered=answered,
        completed=completed,
        failed=failed,
        no_answer=no_answer,
        transferred=transferred,
        escalated=escalated,
        avg_duration_seconds=avg_dur,
    )


async def cost_kpis(
    db: AsyncSession,
    tenant_id: str | uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CostKpi:
    tid = uuid.UUID(str(tenant_id))
    s, e = _validate_range(start, end)
    stmt = select(UsageEvent).where(UsageEvent.tenant_id == tid)
    if s is not None:
        stmt = stmt.where(UsageEvent.created_at >= s)
    if e is not None:
        stmt = stmt.where(UsageEvent.created_at <= e)
    events = list((await db.scalars(stmt)).all())

    seconds_total = 0.0
    sms_segments = 0
    llm_tokens = 0
    for ev in events:
        qty = float(ev.quantity or 0)
        if ev.metric == UsageMetric.VOICE_MINUTE:
            if (ev.unit or "").lower() == "seconds":
                seconds_total += qty
            else:
                seconds_total += qty * 60.0
        elif ev.metric == UsageMetric.SMS_SEGMENT:
            sms_segments += int(qty)
        elif ev.metric == UsageMetric.LLM_TOKEN:
            llm_tokens += int(qty)

    minutes = round(seconds_total / 60.0, 4)
    unit_prices = getattr(settings, "cost_unit_prices", {}) or {}
    voice_price = int(unit_prices.get("voice_minute", 1300))
    sms_price = int(unit_prices.get("sms_segment", 790))
    token_price = int(unit_prices.get("llm_1k_tokens", 0))
    estimated_millicents = int(
        round(
            minutes * voice_price
            + sms_segments * sms_price
            + (llm_tokens / 1000.0) * token_price
        )
    )
    return CostKpi(
        minutes=minutes,
        sms_segments=sms_segments,
        llm_tokens=llm_tokens,
        estimated_cost_millicents=estimated_millicents,
        currency="USD",
    )


async def snapshot(
    db: AsyncSession,
    tenant_id: str | uuid.UUID,
    *,
    kind: KpiKind = KpiKind.CALL,
    start: datetime | None = None,
    end: datetime | None = None,
    granularity: KpiGranularity = KpiGranularity.DAILY,
) -> KpiSnapshot:
    s, e = _validate_range(start, end)
    p_start = s.isoformat() if s is not None else ""
    p_end = e.isoformat() if e is not None else ""
    if kind == KpiKind.COST:
        ck = await cost_kpis(db, tenant_id, start=start, end=end)
        metrics: dict[str, Any] = {
            "minutes": ck.minutes,
            "sms_segments": ck.sms_segments,
            "llm_tokens": ck.llm_tokens,
            "estimated_cost_millicents": ck.estimated_cost_millicents,
            "currency": ck.currency,
        }
    else:
        kpi = await call_kpis(db, tenant_id, start=start, end=end)
        metrics = {
            "total": kpi.total,
            "answered": kpi.answered,
            "completed": kpi.completed,
            "failed": kpi.failed,
            "no_answer": kpi.no_answer,
            "transferred": kpi.transferred,
            "escalated": kpi.escalated,
            "avg_duration_seconds": kpi.avg_duration_seconds,
            "rates": kpi.rates(),
        }
    point = KpiPoint(
        kind=kind,
        period_start=p_start,
        period_end=p_end,
        metrics=metrics,
    )
    return KpiSnapshot.build(
        str(tenant_id),
        range_start=p_start,
        range_end=p_end,
        granularity=granularity,
        points=(point,),
    )

