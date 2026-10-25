"""Erlang-bounded predictive pacing with conservative low-sample throttling."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from statistics import median

from app.contact_center_v2.dialer.agent_availability import AgentAvailabilityEstimate
from app.contact_center_v2.pacing import AbandonmentMeasurement, PacingDecision
from app.contact_center_v2.settings import settings

D = Decimal


def erlang_c(offered_load: float, agents: int) -> float:
    """Probability that an arrival waits in an M/M/N queue."""
    if agents <= 0:
        return 1.0 if offered_load > 0 else 0.0
    if offered_load <= 0:
        return 0.0
    if offered_load >= agents:
        return 1.0
    # Erlang-B recursion avoids large factorials and remains stable at the
    # small/medium agent counts used by a contact-center campaign.
    loss = 1.0
    for serving in range(1, agents + 1):
        loss = offered_load * loss / (serving + offered_load * loss)
    rho = offered_load / agents
    denominator = 1.0 - rho + rho * loss
    return min(1.0, max(0.0, loss / denominator if denominator else 1.0))


def probability_wait_exceeds(
    *, offered_load: float, agents: int, mean_service_seconds: float, target_wait_seconds: float = 2.0
) -> float:
    if mean_service_seconds <= 0 or target_wait_seconds < 0:
        raise ValueError("service time must be positive and target wait non-negative")
    queued = erlang_c(offered_load, agents)
    if offered_load >= agents:
        return queued
    exponent = -(agents - offered_load) * target_wait_seconds / mean_service_seconds
    return min(1.0, max(0.0, queued * math.exp(exponent)))


def binomial_abandonment_rate(
    *, attempts: int, available_agents: int, answer_rate: Decimal
) -> Decimal:
    """Expected positive answer overflow for one discrete pacing tick.

    ``max(answers - agents, 0)`` is convex, so evaluating overflow only at the
    mean answer count understates abandonment. The exact binomial tail includes
    low-volume variance and is therefore suitable for a fail-closed dial cap.
    """
    n = int(attempts)
    agents = int(available_agents)
    if n < 0 or agents < 0:
        raise ValueError("attempts and agents must be non-negative")
    probability = float(answer_rate)
    if probability < 0 or probability > 1:
        raise ValueError("answer rate must be between zero and one")
    if n == 0 or probability == 0 or n <= agents:
        return D("0")
    if probability == 1:
        return D(str((n - agents) / n)).quantize(D("0.000001"))

    expected_overflow = 0.0
    log_p = math.log(probability)
    log_miss = math.log1p(-probability)
    for answered in range(agents + 1, n + 1):
        log_pmf = (
            math.lgamma(n + 1)
            - math.lgamma(answered + 1)
            - math.lgamma(n - answered + 1)
            + answered * log_p
            + (n - answered) * log_miss
        )
        expected_overflow += (answered - agents) * math.exp(log_pmf)
    rate = expected_overflow / (n * probability)
    return D(str(min(1.0, max(0.0, rate)))).quantize(D("0.000001"))


def projected_abandon_rate(
    *,
    dial_ratio: Decimal,
    available_agents: Decimal,
    answer_rate: Decimal,
    mean_service_seconds: Decimal,
    tick_seconds: Decimal,
) -> Decimal:
    """Conservative answered-call overflow probability for a candidate ratio."""
    if dial_ratio <= 0 or answer_rate < 0 or answer_rate > 1:
        raise ValueError("invalid ratio or answer rate")
    if mean_service_seconds <= 0 or tick_seconds <= 0:
        raise ValueError("service and tick durations must be positive")
    if available_agents <= 0 or answer_rate == 0:
        return D("0")
    attempts = int((available_agents * dial_ratio).to_integral_value(rounding=ROUND_FLOOR))
    capacity = int(available_agents.to_integral_value(rounding=ROUND_FLOOR))
    return binomial_abandonment_rate(
        attempts=attempts, available_agents=capacity, answer_rate=answer_rate
    )


def clamp_ratio_to_abandon_cap(
    *,
    requested_ratio: Decimal,
    available_agents: Decimal,
    answer_rate: Decimal,
    mean_service_seconds: Decimal,
    tick_seconds: Decimal,
    cap: Decimal,
) -> tuple[Decimal, Decimal]:
    """Binary-search the highest bounded ratio whose projection is within cap."""
    if cap <= 0 or cap > D("0.10"):
        raise ValueError("cap outside the enforced safety domain")
    upper = min(D(requested_ratio), D(settings.max_dial_ratio))
    if upper < D("1"):
        upper = D("1")
    lower = D("1")

    def projection(value: Decimal) -> Decimal:
        return projected_abandon_rate(
            dial_ratio=value,
            available_agents=available_agents,
            answer_rate=answer_rate,
            mean_service_seconds=mean_service_seconds,
            tick_seconds=tick_seconds,
        )

    upper_projection = projection(upper)
    if upper_projection <= cap:
        return upper, upper_projection
    best = D("0")
    best_projection = D("1")
    for _ in range(48):
        candidate = (lower + upper) / D("2")
        projected = projection(candidate)
        if projected <= cap:
            best, best_projection = candidate, projected
            lower = candidate
        else:
            upper = candidate
    if best == 0:
        # A ratio below one is not predictive dialing. Returning zero forces the
        # caller to throttle to progressive instead of disguising the refusal.
        return D("0"), D("1")
    # Keep the exact searched ratio. Rounding it upward could select a larger
    # integer attempt count than the one whose binomial tail was validated.
    return best, best_projection


@dataclass(frozen=True)
class PacingPlan:
    mode: str
    requested_ratio: Decimal
    dial_ratio: Decimal
    projected_rate: Decimal
    answer_rate: Decimal
    occupancy: Decimal
    available_agents: int
    soon_available_agents: Decimal
    sample_size: int
    allowed: int
    target_inflight: int
    throttled_to_progressive: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "requested_ratio": str(self.requested_ratio),
            "dial_ratio": str(self.dial_ratio),
            "projected_abandon_rate": str(self.projected_rate),
            "answer_rate": str(self.answer_rate),
            "occupancy": str(self.occupancy),
            "available_agents": self.available_agents,
            "soon_available_agents": str(self.soon_available_agents),
            "sample_size": self.sample_size,
            "allowed": self.allowed,
            "target_inflight": self.target_inflight,
            "throttled_to_progressive": self.throttled_to_progressive,
            "reason": self.reason,
        }


def answer_rate_from_history(answered: int, completed_attempts: int) -> Decimal:
    if completed_attempts <= 0:
        return D("0")
    return min(D("1"), D(answered) / D(completed_attempts))


def talk_time_distribution(durations_seconds: list[float]) -> dict[str, Decimal]:
    if not durations_seconds:
        return {"p50": D("180"), "p90": D("300"), "samples": D("0")}
    ordered = sorted(max(0.0, value) for value in durations_seconds)
    p90_index = min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)
    return {
        "p50": D(str(median(ordered))).quantize(D("0.1")),
        "p90": D(str(ordered[p90_index])).quantize(D("0.1")),
        "samples": D(len(ordered)),
    }


def compute_pacing_plan(
    *,
    mode: str,
    requested_ratio: Decimal,
    measurement: AbandonmentMeasurement,
    availability: AgentAvailabilityEstimate,
    inflight: int,
    max_inflight: int,
    answer_rate: Decimal,
    now: datetime,
) -> PacingPlan:
    del now  # All history windows are measured by the tracker before planning.
    available = D(availability.available_now) + availability.soon_available
    occupancy = availability.occupancy
    room = max(0, int(max_inflight) - max(0, int(inflight)))
    insufficient = not measurement.sample_sufficient
    near_cap = measurement.sample_sufficient and measurement.rate >= measurement.cap * D("0.8")
    throttle = mode == "predictive" and (insufficient or near_cap or availability.available_now == 0)
    effective_mode = "progressive" if throttle else mode
    if measurement.breached:
        return PacingPlan(
            mode, requested_ratio, D("0"), D("1"), answer_rate, occupancy,
            availability.available_now, availability.soon_available,
            measurement.human_answered, 0, inflight, False, "abandonment_cap",
        )
    if effective_mode in {"progressive", "preview"}:
        allowed = min(room, availability.available_now) if effective_mode == "progressive" else 0
        return PacingPlan(
            mode, requested_ratio, D("1"), D("0"), answer_rate, occupancy,
            availability.available_now, availability.soon_available,
            measurement.human_answered, allowed, inflight + allowed, throttle,
            "agent_click_required" if effective_mode == "preview" else
            ("low_sample_progressive" if insufficient else "near_cap_progressive" if near_cap else "progressive_agent_reserved"),
        )
    if effective_mode == "ai_agent_blast":
        # AI concurrency is bounded by max_inflight and the canonical AI
        # runtime, not by human agent availability. One pacing unit authorizes
        # one new attempt per tick; fractional units never round upward.
        allowed = min(room, max(0, math.floor(requested_ratio)))
        return PacingPlan(
            mode, requested_ratio, requested_ratio, D("0"), answer_rate, occupancy,
            availability.available_now, availability.soon_available,
            measurement.human_answered, allowed, inflight + allowed, False,
            "max_inflight" if room == 0 else "ai_agent_capacity",
        )
    if available <= 0 or room == 0:
        return PacingPlan(
            mode, requested_ratio, D("0"), D("0"), answer_rate, occupancy,
            availability.available_now, availability.soon_available,
            measurement.human_answered, 0, inflight, False,
            "max_inflight" if room == 0 else "no_available_agent",
        )
    mean_service = max(D("1"), availability.median_talk_seconds + D("30"))
    budget = measurement.cap
    if measurement.sample_sufficient:
        budget = max(D("0"), budget - measurement.rate)
    ratio, projected = clamp_ratio_to_abandon_cap(
        requested_ratio=requested_ratio,
        available_agents=available,
        answer_rate=answer_rate,
        mean_service_seconds=mean_service,
        tick_seconds=D(str(settings.dialer_tick_seconds)),
        cap=budget,
    )
    if ratio <= 0:
        allowed = min(room, availability.available_now)
        return PacingPlan(
            mode, requested_ratio, D("1"), projected, answer_rate, occupancy,
            availability.available_now, availability.soon_available,
            measurement.human_answered, allowed, inflight + allowed, True,
            "erlang_bound_progressive",
        )
    target = min(max_inflight, int((available * ratio).to_integral_value(rounding=ROUND_FLOOR)))
    allowed = min(room, max(0, target - inflight))
    return PacingPlan(
        mode, requested_ratio, ratio, projected, answer_rate, occupancy,
        availability.available_now, availability.soon_available,
        measurement.human_answered, allowed, target, False, "erlang_abandon_bound",
    )


__all__ = [
    "PacingDecision",
    "PacingPlan",
    "answer_rate_from_history",
    "binomial_abandonment_rate",
    "clamp_ratio_to_abandon_cap",
    "compute_pacing_plan",
    "erlang_c",
    "probability_wait_exceeds",
    "projected_abandon_rate",
    "talk_time_distribution",
]
