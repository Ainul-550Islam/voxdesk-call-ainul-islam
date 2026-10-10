"""Candidate Configuration Validation Service for Conductor (Prompt 4).

Validates a proposed change set and materialized candidate Agent configuration
against:
1. Conductor allowlist rules (paths, operations, secret/code safety)
2. Agent builder snapshot schema (``validate_snapshot_dict``)
3. Provider & LLM model compatibility
4. Referenced tool IDs / tool names against built-in + tenant-registered tools
5. Referenced knowledge base IDs against tenant ``KnowledgeCollection`` rows
6. Referenced workflow IDs against tenant ``Workflow`` rows
7. Guardrail configuration bounds
8. Transfer / handoff E.164 destination phone numbers
"""

from __future__ import annotations

from app.core.value_types import dictionary_value

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base  # noqa: F401
from app.db.enterprise_models import AgentTool, KnowledgeCollection, WorkflowTrigger
from app.domain.conductor_diff import apply_operations_to_snapshot, canonical_config_hash
from app.security.policy import (
    validate_mutation_operation,
    validate_mutation_path,
    validate_value_safety,
)
from app.services.agent_service import validate_snapshot_dict

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

SUPPORTED_LLM_PROVIDERS: dict[str, set[str]] = {
    "openai": {
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4.1",
        "gpt-4.1-mini",
    },
    "anthropic": {
        "claude-3-5-sonnet",
        "claude-3-5-haiku",
        "claude-3-opus",
        "claude-3-7-sonnet",
    },
    "groq": {
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
    },
    "azure": {
        "gpt-4o",
        "gpt-4o-mini",
    },
    "gemini": {
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
    },
}

DEFAULT_SAFE_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "check_availability",
        "transfer_call",
        "end_call",
        "lookup_order",
        "create_ticket",
        "send_sms",
        "collect_payment",
        "capture_lead",
        "search_knowledge_base",
        "escalate_to_human",
        "check_business_hours",
    }
)


async def validate_candidate_configuration(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_name: str,
    base_snapshot: dict[str, Any],
    changes: list[dict[str, Any]],
    only_approved: bool = False,
) -> dict[str, Any]:
    """Validate individual changes and the resulting candidate configuration snapshot."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    per_change_results: dict[str, dict[str, Any]] = {}

    active_ops: list[dict[str, Any]] = []
    for chg in sorted(changes, key=lambda c: int(c.get("sequence", 0))):
        cid = str(chg.get("id") or chg.get("sequence") or "0")
        approval = str(chg.get("approval_state") or "pending").lower()
        if only_approved and approval != "approved":
            continue
        if not only_approved and approval == "rejected":
            per_change_results[cid] = {
                "validation_state": "skipped",
                "messages": ["Change is rejected and excluded from candidate validation."],
            }
            continue

        c_msgs: list[str] = []
        path = str(chg.get("path") or "")
        op = str(chg.get("operation") or "set")
        new_val = chg.get("new_value")
        try:
            validate_mutation_path(path)
            validate_mutation_operation(op)
            if op not in {"remove", "delete"}:
                validate_value_safety(new_val, path=path)
        except Exception as exc:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            msg = str(exc)
            c_msgs.append(msg)
            errors.append({"field": path or "operation", "message": msg})

        if c_msgs:
            per_change_results[cid] = {
                "validation_state": "invalid",
                "messages": c_msgs,
            }
        else:
            per_change_results[cid] = {
                "validation_state": "valid",
                "messages": [],
            }
            active_ops.append(
                {
                    "sequence": int(chg.get("sequence", 0)),
                    "path": path,
                    "operation": op,
                    "new_value": new_val,
                }
            )

    if errors:
        return {
            "valid": False,
            "status": "invalid",
            "errors": errors,
            "warnings": warnings,
            "per_change": per_change_results,
            "candidate_snapshot": base_snapshot,
            "candidate_hash": canonical_config_hash(base_snapshot),
        }

    # Materialize candidate snapshot
    try:
        candidate = apply_operations_to_snapshot(base_snapshot, active_ops)
    except Exception as exc:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        errors.append({"field": "candidate", "message": str(exc)})
        return {
            "valid": False,
            "status": "invalid",
            "errors": errors,
            "warnings": warnings,
            "per_change": per_change_results,
            "candidate_snapshot": base_snapshot,
            "candidate_hash": canonical_config_hash(base_snapshot),
        }

    # 1. Core Agent Builder Snapshot Schema Validation
    snap_errors, snap_warnings = validate_snapshot_dict(
        candidate,
        tenant_id=str(tenant_id),
        agent_name=str(candidate.get("name") or agent_name or "Agent"),
    )
    errors.extend(snap_errors)
    warnings.extend(snap_warnings)

    # 2. Provider / Model Compatibility Check
    llm_cfg = dictionary_value(candidate.get("llm_config"))
    provider = str(
        candidate.get("llm_provider")
        or llm_cfg.get("provider")
        or "openai"
    ).strip().lower()
    model = str(
        candidate.get("llm_model")
        or llm_cfg.get("model")
        or "gpt-4o-mini"
    ).strip()

    if provider not in SUPPORTED_LLM_PROVIDERS:
        errors.append(
            {
                "field": "llm_provider",
                "message": f"Unsupported LLM provider {provider!r}. Supported: {sorted(SUPPORTED_LLM_PROVIDERS)}.",
            }
        )
    else:
        allowed_models = SUPPORTED_LLM_PROVIDERS[provider]
        if model and model not in allowed_models:
            # Check if user accidentally paired e.g. claude model with openai provider
            if (provider == "openai" and model.startswith("claude")) or (
                provider == "anthropic" and model.startswith("gpt-")
            ):
                errors.append(
                    {
                        "field": "llm_model",
                        "message": f"Model {model!r} is incompatible with provider {provider!r}.",
                    }
                )
            else:
                warnings.append(
                    {
                        "field": "llm_model",
                        "message": f"Model {model!r} is not in the standard catalog for {provider!r}.",
                    }
                )

    # 3. Voice Speed / Temperature Bounds
    voice_cfg = (
        dictionary_value(candidate.get("voice_config"))
    )
    speed_val = candidate.get("voice_speed", voice_cfg.get("speed"))
    if speed_val is not None:
        try:
            spd = float(speed_val)
            if spd < 0.5 or spd > 2.0:
                errors.append(
                    {
                        "field": "voice_config.speed",
                        "message": f"Voice speed {spd} must be between 0.5 and 2.0.",
                    }
                )
        except (TypeError, ValueError):
            errors.append(
                {
                    "field": "voice_config.speed",
                    "message": "Voice speed must be a numeric value.",
                }
            )

    # 4. Referenced Tool IDs / Names
    tools_val = candidate.get("tools")
    if isinstance(tools_val, list) and tools_val:
        tenant_tools = (
            await session.execute(
                select(AgentTool).where(AgentTool.tenant_id == tenant_id)
            )
        ).scalars().all()
        valid_tool_names = set(DEFAULT_SAFE_BUILTIN_TOOLS) | {
            t.name for t in tenant_tools
        } | {str(t.id) for t in tenant_tools}
        for item in tools_val:
            t_name = (
                str(item.get("name") or item.get("id") or "")
                if isinstance(item, dict)
                else str(item)
            ).strip()
            if not t_name:
                errors.append({"field": "tools", "message": "Tool entry cannot be empty."})
            elif t_name not in valid_tool_names:
                errors.append(
                    {
                        "field": "tools",
                        "message": f"Referenced tool {t_name!r} is not registered for this tenant or in the built-in catalog.",
                    }
                )

    # 5. Referenced Knowledge Base IDs
    kb_ids = candidate.get("knowledge_base_ids")
    if isinstance(kb_ids, list) and kb_ids:
        tenant_kbs = (
            await session.execute(
                select(KnowledgeCollection).where(
                    KnowledgeCollection.tenant_id == tenant_id
                )
            )
        ).scalars().all()
        valid_kbs = {str(kb.id) for kb in tenant_kbs} | {kb.name for kb in tenant_kbs}
        for kid in kb_ids:
            if str(kid) not in valid_kbs:
                errors.append(
                    {
                        "field": "knowledge_base_ids",
                        "message": f"Referenced knowledge base {kid!r} does not exist in caller tenant.",
                    }
                )

    # 6. Referenced Workflow IDs
    wf_ids = candidate.get("workflow_ids")
    if isinstance(wf_ids, list) and wf_ids:
        tenant_wfs = (
            await session.execute(
                select(WorkflowTrigger).where(
                    WorkflowTrigger.tenant_id == tenant_id
                )
            )
        ).scalars().all()
        valid_wfs = {str(wf.id) for wf in tenant_wfs} | {
            wf.workflow_id for wf in tenant_wfs
        }
        for wid in wf_ids:
            if str(wid) not in valid_wfs:
                errors.append(
                    {
                        "field": "workflow_ids",
                        "message": f"Referenced workflow {wid!r} does not exist in caller tenant.",
                    }
                )

    # 7. Guardrails Validation
    guardrails = candidate.get("guardrails")
    if isinstance(guardrails, dict):
        max_turns = guardrails.get("max_turns")
        if max_turns is not None:
            try:
                mt = int(max_turns)
                if mt < 1 or mt > 200:
                    errors.append(
                        {
                            "field": "guardrails.max_turns",
                            "message": "guardrails.max_turns must be between 1 and 200.",
                        }
                    )
            except (TypeError, ValueError):
                errors.append(
                    {
                        "field": "guardrails.max_turns",
                        "message": "guardrails.max_turns must be an integer.",
                    }
                )

    # 8. Transfer / Handoff Phone Validation
    handoff_num = candidate.get("handoff_number") or (
        candidate.get("handoff", {}).get("destination")
        if isinstance(candidate.get("handoff"), dict)
        else None
    ) or (
        candidate.get("transfer", {}).get("destination")
        if isinstance(candidate.get("transfer"), dict)
        else None
    )
    if handoff_num:
        h_str = str(handoff_num).strip()
        if not _E164_RE.match(h_str) and not h_str.startswith("sip:"):
            errors.append(
                {
                    "field": "transfer.destination",
                    "message": f"Transfer/handoff destination {h_str!r} must be a valid E.164 phone number or SIP URI.",
                }
            )

    is_valid = len(errors) == 0
    if not is_valid:
        for chg in changes:
            cid = str(chg.get("id") or chg.get("sequence") or "0")
            c_path = str(chg.get("path") or "")
            matching = [
                e["message"]
                for e in errors
                if e["field"] == c_path or c_path.startswith(e["field"]) or e["field"].startswith(c_path)
            ]
            if matching:
                per_change_results[cid] = {
                    "validation_state": "invalid",
                    "messages": matching,
                }

    return {
        "valid": is_valid,
        "status": "valid" if is_valid else "invalid",
        "errors": errors,
        "warnings": warnings,
        "per_change": per_change_results,
        "candidate_snapshot": candidate,
        "candidate_hash": canonical_config_hash(candidate),
    }
