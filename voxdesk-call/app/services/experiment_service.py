"""A/B Experiment assignment, outcome recording, and statistical evaluation (Part 1G / Gate G1).

Provides:
- Deterministic per-call variant assignment: ``hash_bucket_bp(call_sid, experiment_id) < cumulative_weight_bp``
- Persistence of ``Call.experiment_id`` / ``Call.variant_id`` and ``TelephonyCallSession.experiment_id`` / ``TelephonyCallSession.variant_id``
- Post-call outcome recording (``success``, ``duration``, ``csat``, ``cost``) in ``ExperimentCallOutcome`` and ``ExperimentVariant.metrics``
- Real two-proportion z-test and Welch's t-test over recorded outcomes, returning ``"insufficient_data"`` when ``n < 30`` per arm
"""
from __future__ import annotations

import hashlib
import math
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enterprise_models import (
    Experiment,
    ExperimentCallOutcome,
    ExperimentStatus,
    ExperimentVariant,
)
from app.db.models import Call
from app.db.telephony_models import TelephonyCallSession

MIN_SAMPLE_SIZE_PER_ARM = 30
SIGNIFICANCE_ALPHA = 0.05


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_bucket_bp(call_sid: str, experiment_id: uuid.UUID | str) -> int:
    """Return a deterministic basis-point bucket in ``[0, 9999]`` for ``(call_sid, experiment_id)``."""
    seed = f"{call_sid}:{experiment_id}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    value = int.from_bytes(digest[:8], "big")
    return value % 10000


def select_variant_by_hash(
    variants: Sequence[ExperimentVariant],
    call_sid: str,
    experiment_id: uuid.UUID | str,
) -> ExperimentVariant:
    """Deterministically select a variant where ``hash(call_sid, experiment_id) % 10000 < cumulative_weight_bp``."""
    if not variants:
        raise ValueError("Cannot select a variant from an empty list")

    ordered = sorted(
        variants,
        key=lambda v: (not bool(v.is_control), str(v.name or ""), str(v.id)),
    )
    bucket_bp = hash_bucket_bp(call_sid, experiment_id)
    cumulative_bp = 0
    for variant in ordered:
        weight_bp = max(0, int(variant.weight or 0)) * 100
        cumulative_bp += weight_bp
        if bucket_bp < cumulative_bp:
            return variant
    return ordered[-1]


async def assign_call_to_experiment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str | uuid.UUID | None = None,
    call_sid: str,
    call_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Assign ``call_sid`` to the active experiment for ``agent_id`` (or ``experiment_id``) and persist on ``Call`` / ``TelephonyCallSession``."""
    experiment: Experiment | None = None
    if experiment_id is not None:
        experiment = await session.scalar(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.tenant_id == tenant_id,
                Experiment.status == ExperimentStatus.RUNNING.value,
            )
        )
    elif agent_id is not None:
        experiment = await session.scalar(
            select(Experiment)
            .where(
                Experiment.tenant_id == tenant_id,
                Experiment.agent_id == str(agent_id),
                Experiment.status == ExperimentStatus.RUNNING.value,
            )
            .order_by(Experiment.created_at.desc(), Experiment.id)
        )

    if experiment is None:
        return None

    variants = list(
        (
            await session.scalars(
                select(ExperimentVariant)
                .where(
                    ExperimentVariant.experiment_id == experiment.id,
                    ExperimentVariant.tenant_id == tenant_id,
                )
                .order_by(ExperimentVariant.created_at, ExperimentVariant.id)
            )
        ).all()
    )
    if len(variants) < 2:
        return None

    chosen = select_variant_by_hash(variants, call_sid, experiment.id)
    bucket_bp = hash_bucket_bp(call_sid, experiment.id)

    call_row: Call | None = None
    if call_id is not None:
        call_row = await session.scalar(
            select(Call).where(Call.id == call_id, Call.tenant_id == tenant_id)
        )
    if call_row is None and call_sid:
        call_row = await session.scalar(
            select(Call).where(
                Call.call_sid == call_sid, Call.tenant_id == tenant_id
            )
        )
    if call_row is not None:
        call_row.experiment_id = experiment.id
        call_row.variant_id = chosen.id

    session_row: TelephonyCallSession | None = None
    if call_id is not None:
        session_row = await session.scalar(
            select(TelephonyCallSession).where(
                TelephonyCallSession.id == call_id,
                TelephonyCallSession.tenant_id == tenant_id,
            )
        )
    if session_row is None and call_sid:
        session_row = await session.scalar(
            select(TelephonyCallSession).where(
                TelephonyCallSession.provider_call_id == call_sid,
                TelephonyCallSession.tenant_id == tenant_id,
            )
        )
    if session_row is not None:
        session_row.experiment_id = experiment.id
        session_row.variant_id = chosen.id
        session_row.metadata_json = {
            **(session_row.metadata_json or {}),
            "experiment_id": str(experiment.id),
            "variant_id": str(chosen.id),
            "variant_name": chosen.name,
        }

    await session.flush()
    return {
        "experiment_id": str(experiment.id),
        "variant_id": str(chosen.id),
        "variant_name": chosen.name,
        "is_control": bool(chosen.is_control),
        "weight": int(chosen.weight),
        "weight_bp": int(chosen.weight) * 100,
        "bucket_bp": bucket_bp,
        "config": dict(chosen.config or {}),
        "prompt": chosen.prompt or "",
    }


async def _refresh_variant_metrics_summary(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    experiment_id: uuid.UUID,
    variant_id: uuid.UUID,
) -> dict[str, Any]:
    outcomes = list(
        (
            await session.scalars(
                select(ExperimentCallOutcome).where(
                    ExperimentCallOutcome.tenant_id == tenant_id,
                    ExperimentCallOutcome.experiment_id == experiment_id,
                    ExperimentCallOutcome.variant_id == variant_id,
                )
            )
        ).all()
    )
    n = len(outcomes)
    if n == 0:
        summary = {
            "calls": 0,
            "successes": 0,
            "conversion_rate": 0.0,
            "avg_duration": 0.0,
            "avg_csat": None,
            "total_cost": 0.0,
            "avg_cost": 0.0,
        }
    else:
        successes = sum(1 for o in outcomes if o.success)
        durations = [float(o.duration_seconds or 0.0) for o in outcomes]
        csats = [float(o.csat) for o in outcomes if o.csat is not None]
        costs = [float(o.cost or 0.0) for o in outcomes]
        summary = {
            "calls": n,
            "successes": successes,
            "conversion_rate": round(successes / n, 6),
            "avg_duration": round(sum(durations) / n, 4),
            "avg_csat": round(sum(csats) / len(csats), 4) if csats else None,
            "total_cost": round(sum(costs), 6),
            "avg_cost": round(sum(costs) / n, 6),
        }

    variant = await session.get(ExperimentVariant, variant_id)
    if variant is not None and variant.tenant_id == tenant_id:
        variant.metrics = summary
    return summary


async def record_call_outcome(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    success: bool,
    duration: float,
    csat: float | None = None,
    cost: float = 0.0,
    experiment_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    call_sid: str = "",
) -> ExperimentCallOutcome | None:
    """Record a post-call outcome against the call's assigned experiment variant."""
    resolved_exp_id = experiment_id
    resolved_var_id = variant_id
    resolved_sid = call_sid

    if resolved_exp_id is None or resolved_var_id is None:
        call_row = await session.scalar(
            select(Call).where(Call.id == call_id, Call.tenant_id == tenant_id)
        )
        if call_row is not None:
            resolved_exp_id = resolved_exp_id or call_row.experiment_id
            resolved_var_id = resolved_var_id or call_row.variant_id
            resolved_sid = resolved_sid or (call_row.call_sid or "")

    if resolved_exp_id is None or resolved_var_id is None:
        sess_row = await session.scalar(
            select(TelephonyCallSession).where(
                TelephonyCallSession.id == call_id,
                TelephonyCallSession.tenant_id == tenant_id,
            )
        )
        if sess_row is not None:
            resolved_exp_id = resolved_exp_id or sess_row.experiment_id
            resolved_var_id = resolved_var_id or sess_row.variant_id
            resolved_sid = resolved_sid or (sess_row.provider_call_id or "")

    if resolved_exp_id is None or resolved_var_id is None:
        return None

    existing = await session.scalar(
        select(ExperimentCallOutcome).where(
            ExperimentCallOutcome.experiment_id == resolved_exp_id,
            ExperimentCallOutcome.call_id == call_id,
        )
    )
    if existing is None:
        outcome = ExperimentCallOutcome(
            tenant_id=tenant_id,
            experiment_id=resolved_exp_id,
            variant_id=resolved_var_id,
            call_id=call_id,
            call_sid=resolved_sid,
            success=bool(success),
            duration_seconds=max(0.0, float(duration)),
            csat=float(csat) if csat is not None else None,
            cost=max(0.0, float(cost)),
            recorded_at=_now(),
        )
        session.add(outcome)
    else:
        existing.variant_id = resolved_var_id
        existing.success = bool(success)
        existing.duration_seconds = max(0.0, float(duration))
        existing.csat = float(csat) if csat is not None else None
        existing.cost = max(0.0, float(cost))
        existing.recorded_at = _now()
        outcome = existing

    await session.flush()
    await _refresh_variant_metrics_summary(
        session,
        tenant_id=tenant_id,
        experiment_id=resolved_exp_id,
        variant_id=resolved_var_id,
    )
    await session.flush()
    return outcome


def _normal_two_sided_p_value(z: float) -> float:
    return float(math.erfc(abs(z) / math.sqrt(2.0)))


def two_proportion_z_test(s1: int, n1: int, s2: int, n2: int) -> dict[str, float]:
    """Compute a two-proportion pooled z-test between treatment (s1/n1) and control (s2/n2)."""
    if n1 <= 0 or n2 <= 0:
        return {"z_stat": 0.0, "p_value": 1.0, "lift": 0.0}
    p1 = s1 / n1
    p2 = s2 / n2
    pooled = (s1 + s2) / (n1 + n2)
    denom_sq = pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2)
    if denom_sq <= 1e-15:
        return {"z_stat": 0.0, "p_value": 1.0, "lift": round(p1 - p2, 6)}
    z_stat = (p1 - p2) / math.sqrt(denom_sq)
    p_value = _normal_two_sided_p_value(z_stat)
    return {
        "z_stat": round(z_stat, 6),
        "p_value": round(p_value, 6),
        "lift": round(p1 - p2, 6),
    }


def welch_t_test(
    values1: Sequence[float], values2: Sequence[float]
) -> dict[str, float]:
    """Compute Welch's unequal-variances t-test between treatment ``values1`` and control ``values2``."""
    n1 = len(values1)
    n2 = len(values2)
    if n1 < 2 or n2 < 2:
        return {"t_stat": 0.0, "df": 0.0, "p_value": 1.0, "diff": 0.0}

    mean1 = statistics.fmean(values1)
    mean2 = statistics.fmean(values2)
    var1 = statistics.variance(values1)
    var2 = statistics.variance(values2)

    se_sq = (var1 / n1) + (var2 / n2)
    if se_sq <= 1e-15:
        return {
            "t_stat": 0.0,
            "df": float(n1 + n2 - 2),
            "p_value": 1.0,
            "diff": round(mean1 - mean2, 6),
        }

    t_stat = (mean1 - mean2) / math.sqrt(se_sq)
    num = se_sq ** 2
    den = ((var1 / n1) ** 2) / (n1 - 1) + ((var2 / n2) ** 2) / (n2 - 1)
    df = num / den if den > 1e-15 else float(n1 + n2 - 2)

    # Cornish-Fisher / normal approximation for df >= 30
    effective_z = t_stat * (1.0 - 1.0 / (4.0 * max(df, 2.0)))
    p_value = _normal_two_sided_p_value(effective_z)
    return {
        "t_stat": round(t_stat, 6),
        "df": round(df, 4),
        "p_value": round(p_value, 6),
        "diff": round(mean1 - mean2, 6),
    }


async def compute_experiment_results(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    experiment_id: uuid.UUID,
    min_samples_per_arm: int = MIN_SAMPLE_SIZE_PER_ARM,
) -> dict[str, Any]:
    """Compute statistical evaluation across variants for ``experiment_id``."""
    experiment = await session.scalar(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.tenant_id == tenant_id,
        )
    )
    if experiment is None:
        raise ValueError("experiment not found")

    variants = list(
        (
            await session.scalars(
                select(ExperimentVariant)
                .where(
                    ExperimentVariant.experiment_id == experiment_id,
                    ExperimentVariant.tenant_id == tenant_id,
                )
                .order_by(ExperimentVariant.created_at, ExperimentVariant.id)
            )
        ).all()
    )
    outcomes = list(
        (
            await session.scalars(
                select(ExperimentCallOutcome).where(
                    ExperimentCallOutcome.experiment_id == experiment_id,
                    ExperimentCallOutcome.tenant_id == tenant_id,
                )
            )
        ).all()
    )

    by_variant: dict[uuid.UUID, list[ExperimentCallOutcome]] = {
        v.id: [] for v in variants
    }
    for o in outcomes:
        by_variant.setdefault(o.variant_id, []).append(o)

    arms: list[dict[str, Any]] = []
    for v in variants:
        rows = by_variant.get(v.id, [])
        n = len(rows)
        successes = sum(1 for r in rows if r.success)
        durations = [float(r.duration_seconds or 0.0) for r in rows]
        csats = [float(r.csat) for r in rows if r.csat is not None]
        costs = [float(r.cost or 0.0) for r in rows]
        arms.append(
            {
                "variant_id": str(v.id),
                "name": v.name,
                "is_control": bool(v.is_control),
                "weight": int(v.weight),
                "n": n,
                "successes": successes,
                "conversion_rate": round(successes / n, 6) if n else 0.0,
                "avg_duration": round(sum(durations) / n, 4) if n else 0.0,
                "avg_csat": round(sum(csats) / len(csats), 4) if csats else None,
                "avg_cost": round(sum(costs) / n, 6) if n else 0.0,
                "total_cost": round(sum(costs), 6),
            }
        )

    insufficient = any(arm["n"] < min_samples_per_arm for arm in arms) or len(arms) < 2
    if insufficient:
        return {
            "experiment_id": str(experiment.id),
            "experiment_name": experiment.name,
            "experiment_status": experiment.status,
            "status": "insufficient_data",
            "significance": "insufficient_data",
            "min_samples_per_arm": min_samples_per_arm,
            "total_calls": len(outcomes),
            "variants": arms,
            "comparisons": [],
            "recommended_winner_variant_id": None,
        }

    control_variant = next((v for v in variants if v.is_control), variants[0])
    control_rows = by_variant.get(control_variant.id, [])
    c_n = len(control_rows)
    c_succ = sum(1 for r in control_rows if r.success)
    c_durations = [float(r.duration_seconds or 0.0) for r in control_rows]
    c_csats = [float(r.csat) for r in control_rows if r.csat is not None]
    c_costs = [float(r.cost or 0.0) for r in control_rows]

    comparisons: list[dict[str, Any]] = []
    best_winner_id: str | None = None
    best_lift = 0.0
    any_significant = False

    for v in variants:
        if v.id == control_variant.id:
            continue
        t_rows = by_variant.get(v.id, [])
        t_n = len(t_rows)
        t_succ = sum(1 for r in t_rows if r.success)
        t_durations = [float(r.duration_seconds or 0.0) for r in t_rows]
        t_csats = [float(r.csat) for r in t_rows if r.csat is not None]
        t_costs = [float(r.cost or 0.0) for r in t_rows]

        z_res = two_proportion_z_test(t_succ, t_n, c_succ, c_n)
        dur_t = welch_t_test(t_durations, c_durations)
        cost_t = welch_t_test(t_costs, c_costs)
        csat_t = (
            welch_t_test(t_csats, c_csats)
            if len(t_csats) >= 2 and len(c_csats) >= 2
            else None
        )

        is_sig = z_res["p_value"] < SIGNIFICANCE_ALPHA
        if is_sig:
            any_significant = True
            if z_res["lift"] > best_lift:
                best_lift = z_res["lift"]
                best_winner_id = str(v.id)

        comparisons.append(
            {
                "treatment_variant_id": str(v.id),
                "treatment_name": v.name,
                "control_variant_id": str(control_variant.id),
                "control_name": control_variant.name,
                "success_z_test": z_res,
                "duration_welch_t_test": dur_t,
                "cost_welch_t_test": cost_t,
                "csat_welch_t_test": csat_t,
                "statistically_significant": is_sig,
            }
        )

    overall_status = "significant" if any_significant else "not_significant"
    return {
        "experiment_id": str(experiment.id),
        "experiment_name": experiment.name,
        "experiment_status": experiment.status,
        "status": overall_status,
        "significance": overall_status,
        "min_samples_per_arm": min_samples_per_arm,
        "total_calls": len(outcomes),
        "variants": arms,
        "comparisons": comparisons,
        "recommended_winner_variant_id": best_winner_id,
    }
