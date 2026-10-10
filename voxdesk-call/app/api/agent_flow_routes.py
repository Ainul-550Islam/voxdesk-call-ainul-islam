# File: app/api/agent_flow_routes.py — Draft flow GET/PUT, graph validation, text-only FlowRunner simulation, and AgentVersion publish routes (Part 5 / Gate G6)
"""Conversation-Flow Builder API routes (`/api/agents`).

Endpoints:
  - `GET  /api/agents/flow/schema`            — Canonical JSON Schema for FlowGraph
  - `GET  /api/agents/{agent_id}/flow`        — Load draft flow, ETag, validation & W-12 bindings
  - `PUT  /api/agents/{agent_id}/flow`        — Save draft flow with ETag optimistic concurrency
  - `POST /api/agents/{agent_id}/flow/validate` — Validate draft or inline FlowGraph
  - `POST /api/agents/{agent_id}/flow/simulate` — Text-only scripted-turn simulation via FlowRunner
  - `POST /api/agents/{agent_id}/flow/publish`  — Gate on flow_validation and publish via AgentVersion
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.builder.flow_runner import FlowRunner, load_agent_flow_bindings
from app.builder.flow_validation import validate_flow
from app.builder.node import FlowGraph, export_flow_json_schema
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.models import Agent, AgentValidationStatus
from app.db.session import get_session as get_db
from app.domain.agent_models import compute_config_etag, find_secret_like_keys
from app.services import agent_service

router = APIRouter(prefix="/api/agents", tags=["agent-flow"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def default_starter_flow() -> dict[str, Any]:
    """Return a valid default starter flow graph (`start -> greet -> end`)."""
    return {
        "version": 1,
        "nodes": [
            {
                "id": "node_start",
                "type": "start",
                "label": "Start Call",
                "params": {"greeting": "Hello! Thanks for calling. How can I help you today?"},
                "position": {"x": 80.0, "y": 180.0},
                "is_global": False,
            },
            {
                "id": "node_conversation",
                "type": "conversation",
                "label": "Main Conversation",
                "params": {
                    "prompt": "Help the caller with their request concisely and politely.",
                    "tools": ["book_appointment", "transfer_to_human"],
                },
                "position": {"x": 360.0, "y": 180.0},
                "is_global": False,
            },
            {
                "id": "node_end",
                "type": "end",
                "label": "End Call",
                "params": {
                    "reason": "completed",
                    "speak_text": "Thank you for calling. Have a great day!",
                },
                "position": {"x": 660.0, "y": 180.0},
                "is_global": False,
            },
        ],
        "edges": [
            {
                "id": "e_node_start_node_conversation",
                "source": "node_start",
                "target": "node_conversation",
                "source_id": "node_start",
                "target_id": "node_conversation",
                "condition_label": "always",
                "expression": None,
                "priority": 0,
                "condition": {
                    "kind": "always",
                    "equations": [],
                    "match_mode": "all",
                    "prompt": None,
                    "expression": None,
                    "label": "always",
                },
            },
            {
                "id": "e_node_conversation_node_end",
                "source": "node_conversation",
                "target": "node_end",
                "source_id": "node_conversation",
                "target_id": "node_end",
                "condition_label": "Caller is done",
                "expression": None,
                "priority": 0,
                "condition": {
                    "kind": "always",
                    "equations": [],
                    "match_mode": "all",
                    "prompt": None,
                    "expression": None,
                    "label": "Caller is done",
                },
            },
        ],
        "initial_variables": {},
        "variables": [],
        "metadata": {},
    }


class FlowSavePayload(_StrictModel):
    mode: Literal["single_prompt", "flow"] = "flow"
    flow: dict[str, Any]
    expected_etag: Optional[str] = None


class FlowValidatePayload(_StrictModel):
    flow: Optional[dict[str, Any]] = None


class FlowSimulatePayload(_StrictModel):
    turns: list[str] = Field(default_factory=list, max_length=50)
    initial_variables: dict[str, Any] = Field(default_factory=dict)
    flow: Optional[dict[str, Any]] = None
    judge_decisions: dict[str, bool] = Field(default_factory=dict)
    tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)


class FlowPublishPayload(_StrictModel):
    changelog: str = Field(default="Published conversation flow", max_length=500)
    environment: Literal["development", "staging", "production"] = "production"


async def _resolve_agent_row(
    session: AsyncSession,
    ctx: TenantContext,
    agent_id: str,
    *,
    for_update: bool = False,
) -> Agent:
    row = await agent_service.get_agent_row(
        session, ctx.tenant, agent_id, for_update=for_update
    )
    if row is not None:
        return row
    default_cfg = agent_service.from_tenant(ctx.tenant)
    if default_cfg.id == agent_id:
        row = await agent_service.create_draft_async(
            session, ctx.tenant, default_cfg, actor=ctx.user_id
        )
        return row
    raise NotFoundError("Agent not found")


@router.get("/flow/schema")
async def get_flow_json_schema(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> dict[str, Any]:
    """Return the canonical JSON Schema for VoxDesk Conversation Flows."""
    del ctx
    return export_flow_json_schema()


@router.get("/{agent_id}/flow")
async def get_agent_draft_flow(
    agent_id: str,
    response: Response,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """GET /api/agents/{agent_id}/flow — Load draft conversation flow and W-12 bindings."""
    row = await _resolve_agent_row(session, ctx, agent_id)
    snapshot = (
        copy.deepcopy(row.current_draft_config)
        if isinstance(row.current_draft_config, dict) and row.current_draft_config
        else agent_service.default_builder_config(name=row.name, description=row.description)
    )
    raw_flow = snapshot.get("flow")
    has_saved_flow = isinstance(raw_flow, dict) and bool(raw_flow.get("nodes"))
    flow_dict = FlowGraph.from_dict(raw_flow).to_dict() if has_saved_flow else default_starter_flow()
    mode = str(snapshot.get("mode") or ("flow" if has_saved_flow else "single_prompt"))
    validation = validate_flow(flow_dict)
    bindings = await load_agent_flow_bindings(
        session, tenant_id=ctx.tenant_id, agent_id=row.id
    )

    response.headers["ETag"] = row.draft_etag or ""
    return {
        "agent_id": str(row.id),
        "external_key": row.external_key,
        "name": row.name,
        "mode": mode,
        "flow": flow_dict,
        "draft_etag": row.draft_etag or "",
        "published_version_number": row.published_version_number,
        "validation": validation.to_dict(),
        "bindings": bindings,
        "updated_at": row.updated_at.isoformat() if row.updated_at else datetime.now(timezone.utc).isoformat(),
    }


@router.put("/{agent_id}/flow")
async def put_agent_draft_flow(
    agent_id: str,
    payload: FlowSavePayload,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """PUT /api/agents/{agent_id}/flow — Save draft flow with ETag optimistic concurrency."""
    secret_offenders = find_secret_like_keys(payload.flow, "flow")
    if secret_offenders:
        raise BadRequestError(
            f"Flow configuration must not contain raw secret or credential fields: {sorted(secret_offenders)}"
        )

    row = await _resolve_agent_row(session, ctx, agent_id, for_update=True)
    expected_etag = payload.expected_etag or if_match
    if expected_etag and expected_etag.strip():
        if row.draft_etag and expected_etag.strip() != row.draft_etag:
            raise ConflictError(
                f"Draft ETag mismatch: expected {expected_etag!r}, current is {row.draft_etag!r}"
            )

    normalized_flow = FlowGraph.from_dict(payload.flow).to_dict()
    validation = validate_flow(normalized_flow)

    snapshot = (
        copy.deepcopy(row.current_draft_config)
        if isinstance(row.current_draft_config, dict) and row.current_draft_config
        else agent_service.default_builder_config(name=row.name, description=row.description)
    )
    snapshot["mode"] = payload.mode
    snapshot["flow"] = normalized_flow

    now = datetime.now(timezone.utc)
    new_etag = compute_config_etag(snapshot)
    row.current_draft_config = snapshot
    row.draft_etag = new_etag
    row.validation_status = (
        AgentValidationStatus.VALID.value
        if validation.valid
        else AgentValidationStatus.INVALID.value
    )
    row.validation_errors = [e.to_dict() for e in validation.errors]
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = ctx.user_id
    row.updated_at = now
    await session.flush()

    await agent_service._emit_audit(
        session,
        tenant_id=ctx.tenant_id,
        action="agent.flow.updated",
        agent_id=str(row.id),
        actor=ctx.user_id,
        environment_id=row.environment_id,
        details={
            "mode": payload.mode,
            "node_count": len(normalized_flow.get("nodes", [])),
            "edge_count": len(normalized_flow.get("edges", [])),
            "valid": validation.valid,
            "draft_etag": new_etag,
        },
    )
    await session.commit()

    response.headers["ETag"] = new_etag
    return {
        "agent_id": str(row.id),
        "external_key": row.external_key,
        "mode": payload.mode,
        "flow": normalized_flow,
        "draft_etag": new_etag,
        "lock_version": row.lock_version,
        "validation": validation.to_dict(),
        "updated_at": now.isoformat(),
    }


@router.post("/{agent_id}/flow/validate")
async def validate_agent_flow(
    agent_id: str,
    payload: Optional[FlowValidatePayload] = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """POST /api/agents/{agent_id}/flow/validate — Validate inline or persisted draft flow."""
    row = await _resolve_agent_row(session, ctx, agent_id)
    if payload is not None and payload.flow is not None:
        target_flow = payload.flow
    else:
        snapshot = row.current_draft_config if isinstance(row.current_draft_config, dict) else {}
        target_flow = snapshot.get("flow") or default_starter_flow()

    result = validate_flow(target_flow)
    return result.to_dict()


@router.post("/{agent_id}/flow/simulate")
async def simulate_agent_flow(
    agent_id: str,
    payload: FlowSimulatePayload,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """POST /api/agents/{agent_id}/flow/simulate — Run scripted user turns through FlowRunner."""
    row = await _resolve_agent_row(session, ctx, agent_id)
    snapshot = row.current_draft_config if isinstance(row.current_draft_config, dict) else {}
    flow_data = payload.flow or snapshot.get("flow") or default_starter_flow()

    validation = validate_flow(flow_data)
    if not validation.valid:
        raise BadRequestError(
            "Cannot simulate invalid flow: "
            + "; ".join(f"{e.code} ({e.node_id}): {e.message}" for e in validation.errors)
        )

    bindings = await load_agent_flow_bindings(
        session, tenant_id=ctx.tenant_id, agent_id=row.id
    )

    async def _sim_judge(condition_prompt: str, utterance: str, variables: dict[str, Any]) -> bool:
        if condition_prompt in payload.judge_decisions:
            return bool(payload.judge_decisions[condition_prompt])
        u_low = (utterance or "").lower()
        c_low = (condition_prompt or "").lower()
        if c_low and c_low in u_low:
            return True
        words = [w for w in c_low.split() if len(w) >= 4]
        return any(w in u_low for w in words)

    async def _sim_fn_handler(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name in payload.tool_results:
            return {"ok": True, **payload.tool_results[tool_name]}
        return {"ok": True, "tool_name": tool_name, "arguments": args, "result": "simulated_ok"}

    runner = FlowRunner(
        flow_data,
        base_system_prompt=str(snapshot.get("system_prompt") or ""),
        available_tools=bindings["tools"],
        knowledge_collections=bindings["knowledge_collections"],
        workflow_triggers=bindings["workflow_triggers"],
        initial_variables=payload.initial_variables,
        judge=_sim_judge,
        function_handler=_sim_fn_handler,
    )

    start_res = await runner.start()
    step_results: list[dict[str, Any]] = [
        {"turn": 0, "utterance": None, **start_res.to_dict()}
    ]

    for idx, utterance in enumerate(payload.turns, start=1):
        turn_res = await runner.step(utterance)
        step_results.append(
            {"turn": idx, "utterance": utterance, **turn_res.to_dict()}
        )
        if turn_res.ended:
            break

    return {
        "agent_id": str(row.id),
        "start_node_id": start_res.current_node_id,
        "final_node_id": runner.current_node_id,
        "ended": runner.ended,
        "transferred": runner.transferred,
        "variables": dict(runner.variables),
        "transitions": [t.to_dict() for t in runner.history],
        "actions": [a.to_dict() for a in runner.executed_actions],
        "step_results": step_results,
    }


@router.post("/{agent_id}/flow/publish")
async def publish_agent_flow(
    agent_id: str,
    payload: FlowPublishPayload,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """POST /api/agents/{agent_id}/flow/publish — Validate draft flow and publish immutable AgentVersion."""
    row = await _resolve_agent_row(session, ctx, agent_id, for_update=True)
    snapshot = (
        copy.deepcopy(row.current_draft_config)
        if isinstance(row.current_draft_config, dict) and row.current_draft_config
        else agent_service.default_builder_config(name=row.name, description=row.description)
    )
    flow_data = snapshot.get("flow")
    if not isinstance(flow_data, dict) or not flow_data.get("nodes"):
        raise BadRequestError("Agent has no draft conversation flow to publish")

    validation = validate_flow(flow_data)
    if not validation.valid:
        row.validation_status = AgentValidationStatus.INVALID.value
        row.validation_errors = [e.to_dict() for e in validation.errors]
        await session.commit()
        raise BadRequestError(
            "Cannot publish invalid conversation flow: "
            + "; ".join(f"{e.code} ({e.node_id}): {e.message}" for e in validation.errors)
        )

    snapshot["mode"] = "flow"
    snapshot["flow"] = FlowGraph.from_dict(flow_data).to_dict()
    row.current_draft_config = snapshot
    row.draft_etag = compute_config_etag(snapshot)
    row.validation_status = AgentValidationStatus.VALID.value
    row.validation_errors = []
    await session.flush()

    try:
        version = await agent_service.publish_async(
            session,
            ctx.tenant,
            agent_identifier=row.id,
            actor=ctx.user_id,
            changelog=payload.changelog,
            environment=payload.environment,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise BadRequestError(str(exc)) from exc

    return {
        "id": str(version.id),
        "agent_id": str(row.id),
        "external_key": row.external_key,
        "version": int(version.version),
        "version_number": int(version.version),
        "status": str(version.status),
        "mode": "flow",
        "config_hash": version.config_hash,
        "changelog": version.changelog,
        "published_environment": version.published_environment,
        "published_at": version.published_at.isoformat(),
        "flow": snapshot["flow"],
    }
