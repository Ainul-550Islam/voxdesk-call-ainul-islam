"""Evidence-backed QA Scorecard & Version Quality Aggregation Service.

Combines deterministic/LLM-judge ``EvaluationResult`` evidence from ``TestRun``
executions into transparent QA scorecards and version-over-version quality
comparisons. Never fabricates scores when zero enabled rules exist
(returns ``NO_ASSERTIONS`` with ``overall_score=None``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.retell_models import (
    EvaluationResult as EvaluationResult,
    EvaluationResultStatusEnum as EvaluationResultStatusEnum,
    TestRun,
    TestRunStatusEnum,
)
from app.domain.evaluation_models import (
    EvaluationResultResponse,
    QAScorecardSummary,
    ScorecardStatus,
)
from app.qa import service as enterprise_qa_service
from app.services.evaluation_service import (
    EVALUATOR_ENGINE_VERSION,
    SCORECARD_FORMULA_VERSION,
    compute_scorecard_summary,
    list_evaluation_results_for_run,
)


def _ensure_uuid(value: uuid.UUID | str, field_name: str = "id") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {field_name}: {value}") from exc


async def get_run_qa_scorecard(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Return the complete evidence-backed QA scorecard for a persisted TestRun."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    r_id = _ensure_uuid(run_id, "run_id")

    run = (
        await session.execute(
            select(TestRun).where(
                TestRun.id == r_id,
                TestRun.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"TestRun {run_id} not found.")

    results = await list_evaluation_results_for_run(session, t_id, run.id)
    result_dicts = [
        {
            "id": str(r.id),
            "rule_name": r.rule_name,
            "rule_type": r.rule_type,
            "status": r.status,
            "score": r.score,
            "weight": r.weight,
            "evidence": r.evidence,
            "explanation": r.explanation,
            "evaluator_version": r.evaluator_version,
            "formula_version": r.formula_version,
        }
        for r in results
    ]

    persisted_summary = dict(run.scorecard_summary or {})
    if not persisted_summary or "status" not in persisted_summary:
        if run.status == TestRunStatusEnum.NOT_RUN.value:
            summary = QAScorecardSummary(
                status=ScorecardStatus.NOT_RUN,
                overall_score=None,
                explanation=run.error_message or "Test run did not execute.",
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            ).model_dump()
        else:
            summary = compute_scorecard_summary(result_dicts).model_dump()
    else:
        summary = persisted_summary

    return {
        "test_run_id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "suite_id": str(run.suite_id) if run.suite_id else None,
        "test_case_id": str(run.test_case_id) if run.test_case_id else None,
        "agent_id": run.agent_id,
        "agent_kind": run.agent_kind,
        "agent_version_id": str(run.agent_version_id) if run.agent_version_id else None,
        "agent_version_number": run.agent_version_number,
        "agent_config_hash": run.agent_config_hash,
        "mode": run.mode,
        "run_status": run.status,
        "is_mock_provider": bool(run.is_mock_provider),
        "provider": run.provider,
        "model": run.model,
        "scorecard": summary,
        "results": [EvaluationResultResponse.model_validate(r).model_dump(mode="json") for r in results],
        "transcript_turn_count": len(run.transcript_snapshot or []),
        "latency_metadata": dict(run.latency_metadata or {}),
        "usage_metadata": dict(run.usage_metadata or {}),
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


async def get_agent_version_qa_summary(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str,
    *,
    agent_version_number: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Aggregate QA scorecard metrics across persisted TestRuns for an agent/version."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(TestRun).where(
        TestRun.tenant_id == t_id,
        TestRun.agent_id == str(agent_id),
    )
    if agent_version_number is not None:
        stmt = stmt.where(TestRun.agent_version_number == int(agent_version_number))
    stmt = stmt.order_by(TestRun.created_at.desc()).limit(max(1, min(limit, 500)))
    runs = list((await session.execute(stmt)).scalars().all())

    passed_runs = 0
    failed_assertion_runs = 0
    error_runs = 0
    no_assertion_runs = 0
    not_run_count = 0
    scores: list[float] = []
    latencies: list[float] = []

    for r in runs:
        sc = r.scorecard_summary or {}
        sc_status = str(sc.get("status") or "").upper()
        if r.status == TestRunStatusEnum.NOT_RUN.value or sc_status == ScorecardStatus.NOT_RUN.value:
            not_run_count += 1
        elif sc_status == ScorecardStatus.NO_ASSERTIONS.value:
            no_assertion_runs += 1
        elif r.status == TestRunStatusEnum.ERROR.value or sc_status == ScorecardStatus.EVALUATION_ERROR.value:
            error_runs += 1
        elif r.status == TestRunStatusEnum.FAILED.value or sc_status == ScorecardStatus.FAILED_ASSERTION.value:
            failed_assertion_runs += 1
        elif r.status == TestRunStatusEnum.PASSED.value or sc_status == ScorecardStatus.PASSED.value:
            passed_runs += 1

        if sc.get("overall_score") is not None:
            scores.append(float(sc["overall_score"]))
        lat = (r.latency_metadata or {}).get("avg_turn_latency_ms")
        if lat is not None:
            latencies.append(float(lat))

    evaluated_runs = passed_runs + failed_assertion_runs + error_runs
    pass_rate = round((passed_runs / evaluated_runs) * 100.0, 2) if evaluated_runs > 0 else None
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else None

    return {
        "agent_id": str(agent_id),
        "agent_version_number": agent_version_number,
        "formula_version": SCORECARD_FORMULA_VERSION,
        "evaluator_version": EVALUATOR_ENGINE_VERSION,
        "total_runs": len(runs),
        "evaluated_runs_with_assertions": evaluated_runs,
        "passed_runs": passed_runs,
        "failed_assertion_runs": failed_assertion_runs,
        "error_runs": error_runs,
        "no_assertion_runs": no_assertion_runs,
        "not_run_count": not_run_count,
        "pass_rate_pct": pass_rate,
        "average_score": avg_score,
        "average_turn_latency_ms": avg_latency_ms,
    }


async def compare_agent_versions_qa(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str,
    version_a: int,
    version_b: int,
) -> dict[str, Any]:
    """Compare QA evidence and pass rates between two pinned versions of an agent."""
    summary_a = await get_agent_version_qa_summary(
        session, tenant_id, agent_id, agent_version_number=int(version_a)
    )
    summary_b = await get_agent_version_qa_summary(
        session, tenant_id, agent_id, agent_version_number=int(version_b)
    )
    score_a = summary_a.get("average_score")
    score_b = summary_b.get("average_score")
    score_delta = (
        round(float(score_b) - float(score_a), 2)
        if score_a is not None and score_b is not None
        else None
    )
    pass_a = summary_a.get("pass_rate_pct")
    pass_b = summary_b.get("pass_rate_pct")
    pass_rate_delta = (
        round(float(pass_b) - float(pass_a), 2)
        if pass_a is not None and pass_b is not None
        else None
    )
    return {
        "agent_id": str(agent_id),
        "version_a": summary_a,
        "version_b": summary_b,
        "score_delta": score_delta,
        "pass_rate_delta_pct": pass_rate_delta,
    }


__all__ = [
    "compare_agent_versions_qa",
    "enterprise_qa_service",
    "get_agent_version_qa_summary",
    "get_run_qa_scorecard",
]
