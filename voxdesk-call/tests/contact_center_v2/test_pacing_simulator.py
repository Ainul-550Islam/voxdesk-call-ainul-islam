"""Tests-only stochastic pacing simulation; never production evidence.

The carrier is not contacted and no database rows are created. Each run draws
recipient answers independently, then charges an answered call as abandoned
when all human agents are already occupied. The assertion is on the simulated
30-day-style aggregate, not on a fabricated provider result.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from random import Random

from app.contact_center_v2.dialer.agent_availability import AgentAvailabilityEstimate
from app.contact_center_v2.dialer.pacing_engine import compute_pacing_plan
from app.contact_center_v2.pacing import AbandonmentMeasurement


SIMULATION_RUNS = 1_000
SIMULATION_SEED = 20_261_024


def test_predictive_pacing_simulation_1000_runs_respects_three_percent_cap():
    rng = Random(SIMULATION_SEED)
    moment = datetime(2026, 1, 15, tzinfo=timezone.utc)
    total_answered = 0
    total_abandoned = 0
    for _ in range(SIMULATION_RUNS):
        agents = rng.randint(1, 30)
        answer_rate = Decimal(str(rng.uniform(0.05, 0.95)))
        requested_ratio = Decimal(str(rng.uniform(1, 4)))
        measurement = AbandonmentMeasurement(
            window_start=moment,
            human_answered=100,
            abandoned=0,
            cap=Decimal("0.03"),
            minimum_sample=30,
        )
        availability = AgentAvailabilityEstimate(
            available_now=agents,
            soon_available=Decimal("0"),
            busy_agents=0,
            occupancy=Decimal("0.5"),
            median_talk_seconds=Decimal("180"),
            agent_ids=(),
        )
        plan = compute_pacing_plan(
            mode="predictive",
            requested_ratio=requested_ratio,
            measurement=measurement,
            availability=availability,
            inflight=0,
            max_inflight=100,
            answer_rate=answer_rate,
            now=moment,
        )
        assert plan.projected_rate <= Decimal("0.03")
        answered = sum(rng.random() < float(answer_rate) for _ in range(plan.allowed))
        abandoned = max(0, answered - agents)
        total_answered += answered
        total_abandoned += abandoned

    assert total_answered > 0
    observed_rate = Decimal(total_abandoned) / Decimal(total_answered)
    assert observed_rate <= Decimal("0.03"), (
        f"SIMULATION ONLY: {SIMULATION_RUNS} runs produced {observed_rate:.6%} abandonment"
    )
