"""Step 7 — the read-only stuck-side-effect snapshot and bounded observability helpers."""

from __future__ import annotations

import pytest

from app.core import observability


@pytest.mark.asyncio
async def test_stuck_counts_on_empty_database(db):
    """An empty database reports zero stuck rows for both kinds — the sweep
    is a pure count, never a mutation."""
    counts = await observability.stuck_side_effect_counts(db)
    assert counts == {"crm_sync": 0, "knowledge_document": 0}


def test_stuck_side_effect_gauges_drop_unknown_kinds() -> None:
    """Only bounded STUCK_KINDS are published; arbitrary keys are safely ignored."""
    observability.set_stuck_side_effects(
        {
            "crm_sync": 2,
            "knowledge_document": 1,
            "reminder": 0,
            "unbounded_arbitrary_kind": 999,
        }
    )
    assert "crm_sync" in observability.STUCK_KINDS
    assert "unbounded_arbitrary_kind" not in observability.STUCK_KINDS


def test_observability_cardinality_discipline() -> None:
    """Verify bounded label sets for call outcomes, LLM providers, jobs, and cost resources."""
    observability.record_call_outcome("completed")
    observability.record_call_outcome("unexpected_custom_outcome")
    observability.record_llm_tokens("openai", 120)
    observability.record_llm_tokens("custom_finetuned_model", 50)
    observability.record_job_run("reminders", ok=True)
    observability.record_job_run("unknown_job", ok=False)
    observability.record_cost("voice_minute", 2.5, unit_price_millicents=1500)
    observability.record_cost("voice_minute", 1.0, unit_price_millicents=None)

    assert observability.CALL_OUTCOMES == frozenset({"completed", "failed", "no_answer", "unknown"})
    assert observability.LLM_PROVIDERS == frozenset({"openai", "anthropic", "google", "other"})
