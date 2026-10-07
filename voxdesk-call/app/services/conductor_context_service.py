"""Permission-Scoped Context Builder for Conductor (Prompt 4).

Assembles operational context strictly scoped to:
1. Authenticated ``tenant_id`` and optional ``environment_id``
2. Target ``agent_id`` and pinned ``AgentVersion`` (or ``ChatAgentVersion``)
3. Explicitly requested ``call_ids`` and ``test_run_ids`` (verifying tenant ownership;
   referencing a call or test run owned by another tenant raises ``NotFoundError``)
4. Registered tools, knowledge collections, and workflows in the same tenant
5. Recent failed/evaluated ``TestRun`` and QA findings for the target agent
All assembled context is scrubbed via ``app.auth.identity.events.scrub`` so raw
provider credentials can never enter Conductor context.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError, PermissionDeniedError
from app.db.models import (
    AgentVersion as AgentVersionRow,
    Call,
    ChatAgent,
    EvaluationResult,
    TestCase,
    TestRun,
    TestSuite,
)
from app.db.enterprise_models import AgentTool, KnowledgeCollection, WorkflowTrigger
from app.auth.identity.events import scrub
from app.domain.conductor_diff import canonical_config_hash
from app.services import agent_service


def _ensure_uuid(val: uuid.UUID | str, name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {name}: {val!r}") from exc


async def resolve_authoritative_agent_baseline(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str,
    *,
    agent_kind: str = "voice",
    base_version_number: int | None = None,
) -> dict[str, Any]:
    """Load the authoritative Agent + pinned AgentVersion snapshot for Conductor."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    kind = str(agent_kind or "voice").strip().lower()

    if kind == "voice":
        agent_row = await agent_service.get_agent_row(session, t_id, agent_id)
        if agent_row is None:
            raise NotFoundError(f"Voice Agent {agent_id!r} not found for tenant.")
        if base_version_number is not None:
            target_v = int(base_version_number)
        else:
            pub_v = int(getattr(agent_row, "published_version_number", None) or 0)
            if pub_v >= 1:
                target_v = pub_v
            else:
                max_v_stmt = select(
                    func.coalesce(func.max(AgentVersionRow.version_number), 0)
                ).where(
                    AgentVersionRow.tenant_id == t_id,
                    AgentVersionRow.agent_id == agent_row.id,
                )
                target_v = int((await session.execute(max_v_stmt)).scalar_one() or 0)
        if target_v >= 1:
            pinned = await agent_service.resolve_pinned_agent_version_async(
                session,
                t_id,
                str(agent_row.id),
                target_v,
                agent_kind="voice",
            )
            snapshot = dict(pinned["config_snapshot"] or {})
            return {
                "agent_row_id": agent_row.id,
                "agent_id": str(agent_row.id),
                "external_key": agent_row.external_key,
                "agent_kind": "voice",
                "name": agent_row.name,
                "status": agent_row.status,
                "environment_id": agent_row.environment_id,
                "base_agent_version_id": pinned["version_id"],
                "base_version_number": int(pinned["version_number"]),
                "base_config_hash": str(
                    pinned.get("config_hash") or canonical_config_hash(snapshot)
                ),
                "base_draft_etag": str(agent_row.draft_etag or ""),
                "config_snapshot": snapshot,
                "provider": str(pinned.get("provider") or snapshot.get("llm_provider") or "openai"),
                "model": str(pinned.get("model") or snapshot.get("llm_model") or "gpt-4o-mini"),
            }

        # Draft-only agent before first publish
        snapshot = dict(agent_row.current_draft_config or {})
        return {
            "agent_row_id": agent_row.id,
            "agent_id": str(agent_row.id),
            "external_key": agent_row.external_key,
            "agent_kind": "voice",
            "name": agent_row.name,
            "status": agent_row.status,
            "environment_id": agent_row.environment_id,
            "base_agent_version_id": None,
            "base_version_number": int(getattr(agent_row, "published_version_number", None) or 0),
            "base_config_hash": canonical_config_hash(snapshot),
            "base_draft_etag": str(agent_row.draft_etag or ""),
            "config_snapshot": snapshot,
            "provider": str(snapshot.get("llm_provider") or "openai"),
            "model": str(snapshot.get("llm_model") or "gpt-4o-mini"),
        }

    # Chat Agent branch
    try:
        c_uuid = uuid.UUID(str(agent_id))
        chat_row = (
            await session.execute(
                select(ChatAgent).where(
                    ChatAgent.id == c_uuid,
                    ChatAgent.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        chat_row = None

    if chat_row is None:
        raise NotFoundError(f"Chat Agent {agent_id!r} not found for tenant.")

    target_v = (
        int(base_version_number)
        if base_version_number is not None
        else int(chat_row.active_version_number or 1)
    )
    pinned = await agent_service.resolve_pinned_agent_version_async(
        session,
        t_id,
        str(chat_row.id),
        target_v,
        agent_kind="chat",
    )
    snapshot = dict(pinned["config_snapshot"] or {})
    return {
        "agent_row_id": chat_row.id,
        "agent_id": str(chat_row.id),
        "external_key": str(chat_row.id),
        "agent_kind": "chat",
        "name": chat_row.name,
        "status": chat_row.status,
        "environment_id": chat_row.environment_id,
        "base_agent_version_id": pinned["version_id"],
        "base_version_number": int(pinned["version_number"]),
        "base_config_hash": str(pinned.get("config_hash") or canonical_config_hash(snapshot)),
        "base_draft_etag": str(chat_row.draft_etag or ""),
        "config_snapshot": snapshot,
        "provider": str(pinned.get("provider") or "openai"),
        "model": str(pinned.get("model") or "gpt-4o-mini"),
    }


async def assemble_conductor_context(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    agent_id: str,
    agent_kind: str = "voice",
    base_version_number: int | None = None,
    environment_id: uuid.UUID | None = None,
    call_ids: list[str] | None = None,
    test_run_ids: list[str] | None = None,
    suite_ids: list[str] | None = None,
    include_calls: bool = True,
    include_test_runs: bool = True,
    include_qa_scorecards: bool = True,
    include_tools: bool = True,
    include_knowledge_bases: bool = True,
    include_workflows: bool = True,
) -> dict[str, Any]:
    """Assemble permission-scoped, credential-scrubbed operational context for Conductor."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    baseline = await resolve_authoritative_agent_baseline(
        session,
        t_id,
        agent_id,
        agent_kind=agent_kind,
        base_version_number=base_version_number,
    )

    # 1. Explicitly requested Calls (strictly verified against tenant_id)
    calls_context: list[dict[str, Any]] = []
    if call_ids:
        for cid in call_ids:
            c_uuid = _ensure_uuid(cid, "call_id")
            call_row = (
                await session.execute(select(Call).where(Call.id == c_uuid))
            ).scalar_one_or_none()
            if call_row is None:
                raise NotFoundError(f"Referenced Call {cid!r} not found.")
            if call_row.tenant_id != t_id:
                raise PermissionDeniedError(
                    f"Cross-tenant access denied: Call {cid!r} does not belong to caller tenant."
                )
            calls_context.append(
                {
                    "id": str(call_row.id),
                    "status": getattr(call_row, "status", "completed"),
                    "outcome": getattr(call_row, "outcome", None),
                    "summary": getattr(call_row, "summary", "") or "",
                    "transcript": getattr(call_row, "transcript", "") or "",
                    "sentiment": getattr(call_row, "sentiment", None),
                    "duration_sec": getattr(call_row, "duration_sec", 0),
                }
            )
    elif include_calls:
        recent_calls = (
            await session.execute(
                select(Call)
                .where(Call.tenant_id == t_id)
                .order_by(Call.started_at.desc())
                .limit(5)
            )
        ).scalars().all()
        for call_row in recent_calls:
            calls_context.append(
                {
                    "id": str(call_row.id),
                    "status": getattr(call_row, "status", "completed"),
                    "outcome": getattr(call_row, "outcome", None),
                    "summary": getattr(call_row, "summary", "") or "",
                    "transcript": (getattr(call_row, "transcript", "") or "")[:1000],
                    "sentiment": getattr(call_row, "sentiment", None),
                }
            )

    # 2. Explicitly requested or recent TestRuns + EvaluationResults + QA Scorecards
    runs_context: list[dict[str, Any]] = []
    qa_scorecards: list[dict[str, Any]] = []
    if test_run_ids:
        for rid in test_run_ids:
            r_uuid = _ensure_uuid(rid, "test_run_id")
            run_row = (
                await session.execute(select(TestRun).where(TestRun.id == r_uuid))
            ).scalar_one_or_none()
            if run_row is None:
                raise NotFoundError(f"Referenced TestRun {rid!r} not found.")
            if run_row.tenant_id != t_id:
                raise PermissionDeniedError(
                    f"Cross-tenant access denied: TestRun {rid!r} does not belong to caller tenant."
                )
            eval_rows = (
                await session.execute(
                    select(EvaluationResult).where(
                        EvaluationResult.tenant_id == t_id,
                        EvaluationResult.test_run_id == run_row.id,
                    )
                )
            ).scalars().all()
            runs_context.append(
                {
                    "id": str(run_row.id),
                    "agent_version_number": run_row.agent_version_number,
                    "mode": run_row.mode,
                    "status": run_row.status,
                    "transcript_snapshot": list(run_row.transcript_snapshot or []),
                    "scorecard_summary": dict(run_row.scorecard_summary or {}),
                    "failed_assertions": [
                        {
                            "rule_name": er.rule_name,
                            "rule_type": er.rule_type,
                            "status": er.status,
                            "explanation": er.explanation,
                            "evidence": er.evidence,
                        }
                        for er in eval_rows
                        if er.status != "PASSED"
                    ],
                }
            )
            if include_qa_scorecards and run_row.scorecard_summary:
                qa_scorecards.append(
                    {
                        "test_run_id": str(run_row.id),
                        "agent_version_number": run_row.agent_version_number,
                        "scorecard_summary": dict(run_row.scorecard_summary or {}),
                    }
                )
    elif include_test_runs:
        recent_runs = (
            await session.execute(
                select(TestRun)
                .where(
                    TestRun.tenant_id == t_id,
                    TestRun.agent_id == str(baseline["agent_id"]),
                )
                .order_by(TestRun.created_at.desc())
                .limit(5)
            )
        ).scalars().all()
        for run_row in recent_runs:
            eval_rows = (
                await session.execute(
                    select(EvaluationResult).where(
                        EvaluationResult.tenant_id == t_id,
                        EvaluationResult.test_run_id == run_row.id,
                    )
                )
            ).scalars().all()
            runs_context.append(
                {
                    "id": str(run_row.id),
                    "agent_version_number": run_row.agent_version_number,
                    "mode": run_row.mode,
                    "status": run_row.status,
                    "scorecard_summary": dict(run_row.scorecard_summary or {}),
                    "failed_assertions": [
                        {
                            "rule_name": er.rule_name,
                            "rule_type": er.rule_type,
                            "status": er.status,
                            "explanation": er.explanation,
                        }
                        for er in eval_rows
                        if er.status != "PASSED"
                    ],
                }
            )
            if include_qa_scorecards and run_row.scorecard_summary:
                qa_scorecards.append(
                    {
                        "test_run_id": str(run_row.id),
                        "agent_version_number": run_row.agent_version_number,
                        "scorecard_summary": dict(run_row.scorecard_summary or {}),
                    }
                )

    # 3. Suites & Cases for the agent
    suites_rows = (
        await session.execute(
            select(TestSuite)
            .where(
                TestSuite.tenant_id == t_id,
                TestSuite.archived_at.is_(None),
            )
            .limit(10)
        )
    ).scalars().all()
    cases_rows = (
        await session.execute(
            select(TestCase)
            .where(
                TestCase.tenant_id == t_id,
                TestCase.agent_id == str(baseline["agent_id"]),
                TestCase.archived_at.is_(None),
            )
            .limit(10)
        )
    ).scalars().all()

    # 4. Registered Tools, Knowledge Collections, and Workflows in Tenant
    registered_tools: list[dict[str, Any]] = []
    if include_tools:
        tool_rows = (
            await session.execute(
                select(AgentTool).where(AgentTool.tenant_id == t_id).limit(50)
            )
        ).scalars().all()
        for t in tool_rows:
            registered_tools.append(
                {
                    "id": str(t.id),
                    "name": t.name,
                    "tool_type": getattr(t, "tool_type", "webhook"),
                    "enabled": getattr(t, "enabled", True),
                }
            )

    knowledge_bases: list[dict[str, Any]] = []
    if include_knowledge_bases:
        kb_rows = (
            await session.execute(
                select(KnowledgeCollection)
                .where(KnowledgeCollection.tenant_id == t_id)
                .limit(25)
            )
        ).scalars().all()
        for kb in kb_rows:
            knowledge_bases.append(
                {
                    "id": str(kb.id),
                    "name": kb.name,
                    "status": getattr(kb, "status", "ready"),
                }
            )

    workflows: list[dict[str, Any]] = []
    if include_workflows:
        wf_rows = (
            await session.execute(
                select(WorkflowTrigger)
                .where(WorkflowTrigger.tenant_id == t_id)
                .limit(25)
            )
        ).scalars().all()
        for wf in wf_rows:
            workflows.append(
                {
                    "id": str(wf.id),
                    "name": getattr(wf, "workflow_id", str(wf.id)),
                    "status": "active" if getattr(wf, "is_enabled", True) else "disabled",
                }
            )

    raw_context = {
        "tenant_id": str(t_id),
        "environment_id": str(environment_id or baseline.get("environment_id") or ""),
        "agent": {
            "agent_id": baseline["agent_id"],
            "external_key": baseline["external_key"],
            "agent_kind": baseline["agent_kind"],
            "name": baseline["name"],
            "status": baseline["status"],
            "base_agent_version_id": (
                str(baseline["base_agent_version_id"])
                if baseline["base_agent_version_id"]
                else None
            ),
            "base_version_number": baseline["base_version_number"],
            "base_config_hash": baseline["base_config_hash"],
            "base_draft_etag": baseline["base_draft_etag"],
            "config_snapshot": baseline["config_snapshot"],
        },
        "calls": calls_context,
        "test_runs": runs_context,
        "qa_scorecards": qa_scorecards,
        "test_suites": [{"id": str(s.id), "name": s.name} for s in suites_rows],
        "test_cases": [
            {
                "id": str(c.id),
                "name": c.name,
                "agent_version_number": c.agent_version_number,
            }
            for c in cases_rows
        ],
        "registered_tools": registered_tools,
        "knowledge_bases": knowledge_bases,
        "workflows": workflows,
    }
    scrubbed = scrub(raw_context)
    return scrubbed if isinstance(scrubbed, dict) else raw_context
