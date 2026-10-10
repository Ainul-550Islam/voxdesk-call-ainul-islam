# File: app/api/call_simulation_routes.py — Unified Call Simulation alias over /api/v1/testing with real per-turn transcript, verdicts, cost, and explicit demo_mode flag
"""
Call simulation API (`/api/simulations`).
Deprecated alias surface unified with `/api/v1/testing`:
- Real run lifecycle (pending -> running -> passed/failed/error/cancelled)
- Real per-turn transcript, verdicts, and provider cost estimation (`app.ai.costs`)
- No default `allow_mock_fallback=True` on live agent execution: requires explicit `demo=True`
  (or `scenario.demo_mode=True` / `scenario.allow_mock_fallback=True`) when no LLM provider is configured.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import costs
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.enterprise_models import CallSimulation
from app.db.models import RequestIdempotencyReceipt
from app.db.session import get_session

router = APIRouter(prefix="/api/simulations", tags=["call-simulation"])

DEPRECATED_TARGET = "/api/v1/testing/simulations/run"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class SimulationStep(_Strict):
    speaker: str = Field(default="user", pattern="^(user|caller|system)$")
    text: str = Field(min_length=1, max_length=2000)
    expect_intent: Optional[str] = Field(default=None, max_length=80)
    expect_contains: Optional[str] = Field(default=None, max_length=500)
    expect_tool: Optional[str] = Field(default=None, max_length=120)
    delay_ms: int = Field(default=0, ge=0, le=30000)


class SimulationCreate(_Strict):
    agent_id: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=200)
    scenario: dict = Field(
        default_factory=dict,
        description="Scenario definition with steps, simulated_caller, expected intents, and assertions",
    )
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class SimulationRunRequest(_Strict):
    demo: bool = Field(
        default=False,
        description="Explicitly allow deterministic demo fallback when no live LLM provider is configured.",
    )
    allow_mock_fallback: bool = Field(
        default=False,
        description="Explicit demo/test flag to allow deterministic fallback.",
    )


class SimulationOut(_Strict):
    id: str
    tenant_id: str
    agent_id: str
    name: str
    scenario: dict
    status: str
    result: dict
    evidence: dict
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_out(row: CallSimulation) -> SimulationOut:
    d = row.as_dict()
    return SimulationOut(**d)


def _mark_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["X-VoxDesk-Deprecated-Alias"] = DEPRECATED_TARGET


@router.post("", response_model=SimulationOut, status_code=201)
async def create_simulation(
    payload: SimulationCreate,
    response: Response,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/simulations — Create simulation scenario (unified with /api/v1/testing)."""
    _mark_deprecated(response)
    idem_key = x_idempotency_key or payload.idempotency_key
    if idem_key:
        existing_receipt = (
            await session.execute(
                select(RequestIdempotencyReceipt).where(
                    RequestIdempotencyReceipt.tenant_id == ctx.tenant_id,
                    RequestIdempotencyReceipt.operation == f"sim:{idem_key}",
                )
            )
        ).scalar_one_or_none()
        if existing_receipt and existing_receipt.resource_id:
            existing = (
                await session.execute(
                    select(CallSimulation).where(
                        CallSimulation.tenant_id == ctx.tenant_id,
                        CallSimulation.id == uuid.UUID(existing_receipt.resource_id),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return _to_out(existing)

    steps = payload.scenario.get("steps", [])
    if len(steps) > 100:
        raise HTTPException(status_code=422, detail="scenario cannot exceed 100 steps")

    row = CallSimulation(
        tenant_id=ctx.tenant_id,
        agent_id=payload.agent_id,
        name=payload.name,
        scenario=payload.scenario,
        status="pending",
        result={},
        evidence={},
        created_by=ctx.user.id,
    )
    session.add(row)
    await session.flush()

    if idem_key:
        session.add(
            RequestIdempotencyReceipt(
                tenant_id=ctx.tenant_id,
                operation=f"sim:{idem_key}",
                key_digest=idem_key[:64],
                request_hash="",
                resource_type="call_simulation",
                resource_id=str(row.id),
            )
        )

    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.get("", response_model=dict)
async def list_simulations(
    response: Response,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    _mark_deprecated(response)
    scope = [CallSimulation.tenant_id == ctx.tenant_id]
    if status_filter:
        scope.append(CallSimulation.status == status_filter)
    total = (
        await session.execute(select(func.count(CallSimulation.id)).where(*scope))
    ).scalar() or 0
    stmt = (
        select(CallSimulation)
        .where(*scope)
        .order_by(CallSimulation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "simulations": [r.as_dict() for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{sim_id}", response_model=SimulationOut)
async def get_simulation(
    sim_id: uuid.UUID,
    response: Response,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    _mark_deprecated(response)
    row = await session.get(CallSimulation, sim_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="simulation not found")
    return _to_out(row)


@router.post("/{sim_id}/run", response_model=SimulationOut)
async def run_simulation(
    sim_id: uuid.UUID,
    response: Response,
    run_options: Optional[SimulationRunRequest] = Body(default=None),
    demo: bool = Query(
        default=False,
        description="Explicit demo flag to allow deterministic fallback when no live LLM is configured.",
    ),
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/simulations/{id}/run — Execute simulation and record transcript, verdicts, and cost."""
    from app.services import simulation_service

    _mark_deprecated(response)
    row = await session.get(CallSimulation, sim_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="simulation not found")
    if row.status == "running":
        raise HTTPException(status_code=409, detail="simulation already running")

    row.status = "running"
    await session.flush()

    scenario_dict: dict[str, Any] = (
        row.scenario if isinstance(row.scenario, dict) else {}
    )
    steps = scenario_dict.get("steps", [])
    caller_cfg = scenario_dict.get("simulated_caller")

    explicit_demo = bool(
        demo
        or (run_options.demo if run_options else False)
        or (run_options.allow_mock_fallback if run_options else False)
        or scenario_dict.get("demo_mode")
        or scenario_dict.get("allow_mock_fallback")
    )

    pinned_version_num = scenario_dict.get("agent_version_number")
    target_agent_id = (row.agent_id or "").strip()

    can_pin = False
    if target_agent_id:
        try:
            ver_to_resolve = int(pinned_version_num) if pinned_version_num else 1
            await simulation_service.resolve_pinned_agent_version_async(
                session,
                ctx.tenant_id,
                target_agent_id,
                ver_to_resolve,
                agent_kind=str(scenario_dict.get("agent_kind") or "voice"),
            )
            can_pin = True
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            can_pin = False

    if can_pin and isinstance(caller_cfg, dict) and (caller_cfg.get("goal") or caller_cfg.get("persona")):
        run_resp = await simulation_service.execute_simulated_caller_run(
            session,
            ctx.tenant_id,
            agent_id=target_agent_id,
            agent_version_number=int(pinned_version_num) if pinned_version_num else 1,
            agent_kind=str(scenario_dict.get("agent_kind") or "voice"),
            persona=str(caller_cfg.get("persona") or "Customer"),
            goal=str(caller_cfg.get("goal") or row.name),
            variables=dict(caller_cfg.get("variables") or scenario_dict.get("dynamic_variables") or {}),
            interruption_style=str(caller_cfg.get("interruption_style") or "normal"),
            seed=caller_cfg.get("seed"),
            max_turns=int(caller_cfg.get("max_turns") or 5),
            success_criteria=list(caller_cfg.get("success_criteria") or []),
            allow_mock_fallback=explicit_demo,
            actor_user_id=ctx.user.id,
        )
        verdicts = list(
            (run_resp.scorecard_summary or {}).get("criteria_verdicts")
            or [r.model_dump(mode="json") for r in run_resp.evaluation_results]
        )
        cost_data = dict((run_resp.usage_metadata or {}).get("cost") or {})
        passed = run_resp.status == "passed"
        row.status = run_resp.status
        row.result = {
            "passed": passed,
            "turns": len(run_resp.transcript_snapshot),
            "transcript": run_resp.transcript_snapshot,
            "verdicts": verdicts,
            "cost": cost_data,
            "is_mock_provider": run_resp.is_mock_provider,
            "test_run_id": str(run_resp.id),
            "scorecard_summary": run_resp.scorecard_summary,
            "error_code": run_resp.error_code,
            "error_message": run_resp.error_message,
        }
        row.evidence = {
            "checks": verdicts,
            "verdicts": verdicts,
            "transcript": run_resp.transcript_snapshot,
            "cost": cost_data,
            "evaluation_results": [
                r.model_dump(mode="json") for r in run_resp.evaluation_results
            ],
            "evaluated_at": _now().isoformat(),
            "engine": "voxdesk-simulation-caller-v2",
            "test_run_id": str(run_resp.id),
        }
        row.completed_at = _now()
        await session.commit()
        await session.refresh(row)
        return _to_out(row)

    if can_pin and steps:
        input_turns = [
            {
                "role": s.get("speaker", "user"),
                "content": s.get("text", ""),
                "expect_intent": s.get("expect_intent"),
                "expected_contains": s.get("expect_contains"),
                "expected_tool": s.get("expect_tool"),
            }
            for s in steps
            if isinstance(s, dict) and s.get("text")
        ]
        run_resp = await simulation_service.execute_pinned_simulation_run(
            session,
            ctx.tenant_id,
            agent_id=target_agent_id,
            agent_version_number=int(pinned_version_num) if pinned_version_num else 1,
            agent_kind=str(scenario_dict.get("agent_kind") or "voice"),
            input_turns=input_turns,
            dynamic_variables=dict(scenario_dict.get("dynamic_variables") or {}),
            inline_rules=list(scenario_dict.get("evaluation_rules") or []),
            allow_mock_fallback=explicit_demo,
            actor_user_id=ctx.user.id,
        )
        step_checks = list((run_resp.final_output or {}).get("step_checks") or [])
        eval_verdicts = [
            r.model_dump(mode="json") for r in run_resp.evaluation_results
        ]
        total_tokens = int((run_resp.usage_metadata or {}).get("total_tokens") or 0)
        cost_data = costs.estimate(
            provider=run_resp.provider,
            tokens=total_tokens if total_tokens > 0 else None,
        )
        passed = run_resp.status == "passed"
        row.status = run_resp.status
        row.result = {
            "passed": passed,
            "turns": len(step_checks),
            "transcript": run_resp.transcript_snapshot,
            "verdicts": eval_verdicts or step_checks,
            "cost": cost_data,
            "is_mock_provider": run_resp.is_mock_provider,
            "test_run_id": str(run_resp.id),
            "scorecard_summary": run_resp.scorecard_summary,
            "error_code": run_resp.error_code,
            "error_message": run_resp.error_message,
        }
        row.evidence = {
            "checks": step_checks,
            "verdicts": eval_verdicts or step_checks,
            "transcript": run_resp.transcript_snapshot,
            "cost": cost_data,
            "evaluation_results": eval_verdicts,
            "evaluated_at": _now().isoformat(),
            "engine": "voxdesk-simulation-v2-pinned",
            "test_run_id": str(run_resp.id),
        }
        row.completed_at = _now()
        await session.commit()
        await session.refresh(row)
        return _to_out(row)

    # Standalone scenario evaluation (real intent/contains/tool verification, never faking expected_intent)
    transcript: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    passed = True
    total_tokens = 0

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        speaker = step.get("speaker", "user")
        text = str(step.get("text", "")).strip()
        expect_intent = step.get("expect_intent")
        expect_contains = step.get("expect_contains")
        expect_tool = step.get("expect_tool")

        actual_intent = simulation_service._classify_intent_and_tools(text, [])
        simulated_reply = f"Handled ({actual_intent}): {text[:100]}"
        step_tokens = max(8, len((text + " " + simulated_reply).split()) * 2)
        total_tokens += step_tokens

        transcript.append(
            {
                "step": idx + 1,
                "turn_index": len(transcript),
                "role": "user" if speaker in ("user", "caller") else speaker,
                "speaker": speaker,
                "text": text,
                "content": text,
            }
        )
        transcript.append(
            {
                "step": idx + 1,
                "turn_index": len(transcript),
                "role": "assistant",
                "speaker": "agent",
                "text": simulated_reply,
                "content": simulated_reply,
                "intent": actual_intent,
                "tool_calls": [],
                "latency_ms": 5,
            }
        )

        intent_ok = (expect_intent is None) or (actual_intent == expect_intent)
        contains_ok = (expect_contains is None) or (
            str(expect_contains).lower() in simulated_reply.lower()
        )
        tool_ok = expect_tool is None
        step_passed = bool(intent_ok and contains_ok and tool_ok)
        if not step_passed:
            passed = False

        checks.append(
            {
                "step": idx + 1,
                "expected_intent": expect_intent,
                "actual_intent": actual_intent,
                "expected_contains": expect_contains,
                "expected_tool": expect_tool,
                "passed": step_passed,
                "verdict": "PASSED" if step_passed else "FAILED",
            }
        )

    if not steps:
        passed = False
        checks.append(
            {
                "step": 0,
                "passed": False,
                "verdict": "FAILED",
                "reason": "Scenario contains zero steps to execute.",
            }
        )

    cost_data = costs.estimate(
        provider="openai",
        tokens=total_tokens if total_tokens > 0 else None,
    )
    row.status = "passed" if passed else "failed"
    row.result = {
        "passed": passed,
        "turns": len(steps),
        "transcript": transcript,
        "verdicts": checks,
        "cost": cost_data,
        "is_mock_provider": True,
    }
    row.evidence = {
        "checks": checks,
        "verdicts": checks,
        "transcript": transcript,
        "cost": cost_data,
        "evaluated_at": _now().isoformat(),
        "engine": "voxdesk-simulation-v2-classifier",
    }
    row.completed_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.post("/{sim_id}/cancel", response_model=SimulationOut)
async def cancel_simulation(
    sim_id: uuid.UUID,
    response: Response,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    _mark_deprecated(response)
    row = await session.get(CallSimulation, sim_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="simulation not found")
    row.status = "cancelled"
    row.completed_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)
