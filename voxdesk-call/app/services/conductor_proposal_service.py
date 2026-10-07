"""Durable Proposal & Change-Set Service for Conductor (Prompt 4).

Synthesizes typed, allowlisted ``ProposedChangeOperationInput`` operations from:
1. Explicit caller-provided structured operations (validated against policy)
2. Natural-language build/review/improve instructions
3. Linked failed-call transcripts, QA scorecards, and evaluation failures
4. Permitted tools, knowledge bases, and workflows in the caller's context

Never executes arbitrary code or mutates production state.
"""

from __future__ import annotations

import os as os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.models import (
    ConductorApproval,
    ConductorChange,
    ConductorEvidence,
    ConductorEvidenceSourceEnum as ConductorEvidenceSourceEnum,
    ConductorProposal,
    ConductorProposalStatusEnum,
    ConductorRiskLevelEnum as ConductorRiskLevelEnum,
    ConductorSession as ConductorSession,
)
from app.domain.conductor_diff import (
    apply_operations_to_snapshot as apply_operations_to_snapshot,
    canonical_config_hash as canonical_config_hash,
    get_value_at_path,
)
from app.domain.conductor_models import (
    ConductorApprovalEntryResponse,
    ConductorChangeResponse,
    ConductorEvidenceResponse,
    ConductorOperationType,
    ConductorProposalResponse,
    ConductorProposalStatus as ConductorProposalStatus,
    ConductorRiskLevel,
    ProposedChangeOperationInput,
    enforce_proposal_transition as enforce_proposal_transition,
)
from app.security.policy import (
    MAX_PROPOSAL_CHANGES,
    classify_change_risk as classify_change_risk,
    classify_path_section as classify_path_section,
)
from app.services.conductor_evidence_service import record_proposal_evidence as record_proposal_evidence


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(val: uuid.UUID | str, name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {name}: {val!r}") from exc


def synthesize_operations_from_request(
    *,
    request_text: str,
    base_snapshot: dict[str, Any],
    context_snapshot: dict[str, Any],
    explicit_operations: list[ProposedChangeOperationInput] | None = None,
) -> tuple[str, str, list[ProposedChangeOperationInput]]:
    """Synthesize a deterministic, schema-validated list of change operations."""
    if explicit_operations:
        summary = f"Proposed {len(explicit_operations)} structured configuration change(s) from request."
        rationale = f"Generated from explicit structured operations for request: {request_text}"
        return summary, rationale, list(explicit_operations[:MAX_PROPOSAL_CHANGES])

    text = str(request_text or "").strip()
    lower = text.lower()
    ops: list[ProposedChangeOperationInput] = []
    reasons: list[str] = []

    # 1. Greeting changes
    greeting_match = re.search(
        r"(?:change|set|update)\s+(?:the\s+)?greeting\s+(?:to\s+)?[\"']([^\"']+)[\"']",
        text,
        re.IGNORECASE,
    )
    if greeting_match:
        new_greeting = greeting_match.group(1).strip()
        ops.append(
            ProposedChangeOperationInput(
                path="greeting",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "greeting"),
                new_value=new_greeting,
                reason="Updated agent greeting as requested.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append("Updated greeting message")
    elif "greeting" in lower and ("warm" in lower or "friendly" in lower or "change" in lower or "improve" in lower):
        agent_name = str(base_snapshot.get("name") or "our team")
        new_greeting = f"Hello! Thank you for calling {agent_name}. How may I assist you today?"
        ops.append(
            ProposedChangeOperationInput(
                path="greeting",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "greeting"),
                new_value=new_greeting,
                reason="Refined greeting for clearer, warmer caller onboarding.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append("Refined greeting")

    # 2. Voice speed / voice ID / temperature changes
    speed_match = re.search(r"speed\s*(?:to|=)?\s*(0\.\d+|1\.\d+|2\.0)", lower)
    if speed_match or "slower" in lower or "faster" in lower:
        if speed_match:
            target_speed = round(float(speed_match.group(1)), 2)
        elif "slower" in lower:
            target_speed = 0.92
        else:
            target_speed = 1.1
        speed_path = (
            "voice_speed"
            if "voice_speed" in base_snapshot
            else "voice_config.speed"
            if isinstance(base_snapshot.get("voice_config"), dict)
            else "voice_speed"
        )
        ops.append(
            ProposedChangeOperationInput(
                path=speed_path,
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, speed_path)
                or base_snapshot.get("voice_speed", 1.0),
                new_value=target_speed,
                reason=f"Adjusted voice delivery speed to {target_speed}.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append(f"Adjusted voice speed to {target_speed}")

    temp_match = re.search(
        r"temperature\s*(?:to|=)?\s*(0\.\d+|1\.\d+|0|1|2\.0)", lower
    )
    if temp_match:
        target_temp = round(float(temp_match.group(1)), 2)
        ops.append(
            ProposedChangeOperationInput(
                path="temperature",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "temperature"),
                new_value=target_temp,
                reason=f"Adjusted LLM sampling temperature to {target_temp}.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append(f"Adjusted temperature to {target_temp}")

    voice_match = re.search(
        r"(?:change|set|switch)\s+(?:the\s+)?voice\s+(?:to\s+)?([a-zA-Z0-9_-]{2,40})",
        text,
        re.IGNORECASE,
    )
    if voice_match and voice_match.group(1).lower() not in {"speed", "slower", "faster", "config"}:
        new_voice = voice_match.group(1).strip()
        ops.append(
            ProposedChangeOperationInput(
                path="voice",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "voice"),
                new_value=new_voice,
                reason=f"Switched voice profile to '{new_voice}'.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append(f"Switched voice to {new_voice}")

    # 3. LLM / Model changes
    model_match = re.search(
        r"\b(gpt-4o-mini|gpt-4o|gpt-4\.1-mini|gpt-4\.1|claude-3-5-sonnet|claude-3-5-haiku|claude-3-7-sonnet|llama-3\.3-70b-versatile|gemini-2\.0-flash)\b",
        text,
        re.IGNORECASE,
    )
    if model_match:
        new_model = model_match.group(1)
        inferred_provider = (
            "anthropic"
            if new_model.lower().startswith("claude")
            else "groq"
            if new_model.lower().startswith("llama")
            else "gemini"
            if new_model.lower().startswith("gemini")
            else "openai"
        )
        cur_provider = str(base_snapshot.get("llm_provider") or "openai").lower()
        if cur_provider != inferred_provider:
            ops.append(
                ProposedChangeOperationInput(
                    path="llm_provider",
                    operation=ConductorOperationType.SET,
                    old_value=cur_provider,
                    new_value=inferred_provider,
                    reason=f"Updated LLM provider to '{inferred_provider}' to match model '{new_model}'.",
                    risk_level=ConductorRiskLevel.MEDIUM,
                )
            )
        ops.append(
            ProposedChangeOperationInput(
                path="llm_model",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "llm_model"),
                new_value=new_model,
                reason=f"Switched LLM model to '{new_model}'.",
                risk_level=ConductorRiskLevel.MEDIUM,
            )
        )
        reasons.append(f"Updated model to {new_model}")

    # 4. Language changes
    lang_match = re.search(
        r"\b(en-US|en-GB|es-ES|es-MX|fr-FR|de-DE|bn-BD|hi-IN|pt-BR|ja-JP)\b",
        text,
    )
    if lang_match or "spanish" in lower or "french" in lower or "german" in lower or "bengali" in lower:
        new_lang = (
            lang_match.group(1)
            if lang_match
            else "es-ES"
            if "spanish" in lower
            else "fr-FR"
            if "french" in lower
            else "de-DE"
            if "german" in lower
            else "bn-BD"
        )
        ops.append(
            ProposedChangeOperationInput(
                path="language",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "language") or "en-US",
                new_value=new_lang,
                reason=f"Updated agent primary language to '{new_lang}'.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append(f"Updated language to {new_lang}")

    # 5. Interruption sensitivity / turn-taking behavior
    if "interrupt" in lower or "barge" in lower or "turn-taking" in lower or "patience" in lower:
        new_sens = 0.35 if ("less" in lower or "lower" in lower or "patient" in lower) else 0.75
        ops.append(
            ProposedChangeOperationInput(
                path="interruption_sensitivity",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "interruption_sensitivity") or 0.5,
                new_value=new_sens,
                reason=f"Adjusted interruption sensitivity to {new_sens} for smoother turn-taking.",
                risk_level=ConductorRiskLevel.LOW,
            )
        )
        reasons.append(f"Set interruption_sensitivity={new_sens}")

    # 6. Convert prompt configuration into structured flow configuration
    if "flow" in lower and ("convert" in lower or "node" in lower or "structured" in lower):
        flow_nodes = [
            {
                "id": "node_greeting",
                "type": "greeting",
                "prompt": str(base_snapshot.get("greeting") or "Welcome! How can I help you?"),
                "next": "node_intent_router",
            },
            {
                "id": "node_intent_router",
                "type": "conversation",
                "prompt": str(base_snapshot.get("system_prompt") or "Assist the caller."),
                "transitions": [
                    {"condition": "book_appointment", "target": "node_booking"},
                    {"condition": "transfer_to_human", "target": "node_transfer"},
                ],
            },
            {
                "id": "node_booking",
                "type": "tool_execution",
                "tool": "book_appointment",
                "next": "node_closing",
            },
            {
                "id": "node_transfer",
                "type": "transfer",
                "destination": str(base_snapshot.get("handoff_number") or "+18005550199"),
            },
            {
                "id": "node_closing",
                "type": "end_call",
                "prompt": "Thank you for calling. Goodbye!",
            },
        ]
        ops.append(
            ProposedChangeOperationInput(
                path="flow.nodes",
                operation=ConductorOperationType.SET,
                old_value=get_value_at_path(base_snapshot, "flow.nodes"),
                new_value=flow_nodes,
                reason="Converted single-prompt instructions into a multi-node conversation flow.",
                risk_level=ConductorRiskLevel.MEDIUM,
            )
        )
        reasons.append("Converted prompt into structured flow nodes")

    # 7. Tool / KB / Workflow additions from registered resources
    for known_tool in (
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "check_availability",
        "transfer_call",
        "lookup_order",
        "create_ticket",
        "send_sms",
    ):
        if known_tool in lower or known_tool.replace("_", " ") in lower:
            current_tools = list(base_snapshot.get("tools") or [])
            if known_tool not in current_tools:
                ops.append(
                    ProposedChangeOperationInput(
                        path="tools",
                        operation=ConductorOperationType.APPEND,
                        old_value=current_tools,
                        new_value=known_tool,
                        reason=f"Enabled tool '{known_tool}' to support requested workflow.",
                        risk_level=ConductorRiskLevel.MEDIUM,
                    )
                )
                reasons.append(f"Added tool {known_tool}")

    # 8. Failed-call / QA findings / general prompt improvement
    calls = list(context_snapshot.get("calls") or [])
    test_runs = list(context_snapshot.get("test_runs") or [])
    failed_clues: list[str] = []
    for c in calls:
        if c.get("outcome") in {"failed", "escalated", "abandoned"} or c.get("summary"):
            failed_clues.append(
                f"Call {c.get('id')}: {c.get('summary') or c.get('transcript', '')[:160]}"
            )
    for tr in test_runs:
        for fa in tr.get("failed_assertions") or []:
            failed_clues.append(
                f"Failed rule '{fa.get('rule_name')}' ({fa.get('rule_type')}): {fa.get('explanation')}"
            )

    if failed_clues or not ops or "prompt" in lower or "improve" in lower or "fix" in lower:
        old_prompt = str(base_snapshot.get("system_prompt") or "").strip()
        addition_lines = [old_prompt] if old_prompt else ["You are a helpful AI assistant."]
        if failed_clues:
            addition_lines.append(
                f"Operational QA Guidance: Address caller intent directly, confirm reference codes clearly, and follow: {text}"
            )
        else:
            addition_lines.append(f"Additional instruction: {text}")
        new_prompt = "\n\n".join(line for line in addition_lines if line)
        if new_prompt != old_prompt:
            ops.append(
                ProposedChangeOperationInput(
                    path="system_prompt",
                    operation=ConductorOperationType.REPLACE if old_prompt else ConductorOperationType.SET,
                    old_value=old_prompt,
                    new_value=new_prompt,
                    reason=(
                        "Enhanced system_prompt to address failed call/QA evidence: "
                        + "; ".join(failed_clues[:2])
                        if failed_clues
                        else f"Updated system_prompt based on request: {text[:140]}"
                    ),
                    risk_level=ConductorRiskLevel.MEDIUM,
                )
            )
            reasons.append("Updated system_prompt with operational guidance")

    summary = "; ".join(reasons) if reasons else f"Proposed {len(ops)} change(s) for agent."
    rationale = (
        f"Analyzed base version v{context_snapshot.get('agent', {}).get('base_version_number', 1)} "
        f"with {len(calls)} call(s) and {len(test_runs)} test run(s) in scoped context."
    )
    return summary, rationale, ops[:MAX_PROPOSAL_CHANGES]


def compute_risk_summary(changes: list[ConductorChange]) -> dict[str, Any]:
    counts = {"low": 0, "medium": 0, "high": 0}
    for chg in changes:
        lvl = str(chg.risk_level or "low").lower()
        counts[lvl] = counts.get(lvl, 0) + 1
    highest = (
        "high"
        if counts["high"] > 0
        else "medium"
        if counts["medium"] > 0
        else "low"
    )
    return {
        "overall_risk": highest,
        "total_changes": len(changes),
        "by_risk_level": counts,
    }


async def check_and_mark_stale_if_needed(
    session: AsyncSession,
    proposal: ConductorProposal,
    current_baseline: dict[str, Any],
) -> bool:
    """Detect whether the agent's authoritative version/hash has advanced past ``proposal``."""
    if proposal.status in {
        ConductorProposalStatusEnum.APPLIED.value,
        ConductorProposalStatusEnum.STALE.value,
    }:
        return proposal.status == ConductorProposalStatusEnum.STALE.value

    current_v = int(current_baseline.get("base_version_number") or 0)
    current_hash = str(current_baseline.get("base_config_hash") or "")
    if (
        current_v != int(proposal.base_version_number)
        or (proposal.base_config_hash and current_hash and current_hash != proposal.base_config_hash)
    ):
        proposal.status = ConductorProposalStatusEnum.STALE.value
        proposal.error_code = "STALE_BASE_VERSION"
        proposal.error_message = (
            f"Proposal was created against AgentVersion v{proposal.base_version_number} "
            f"(hash {proposal.base_config_hash[:12]}), but current authoritative version is "
            f"v{current_v} (hash {current_hash[:12]}). Reload or rebase before applying."
        )
        proposal.updated_at = _now()
        await session.flush()
        return True
    return False


async def build_proposal_response(
    session: AsyncSession,
    proposal: ConductorProposal,
) -> ConductorProposalResponse:
    """Hydrate a ``ConductorProposal`` with its ordered changes, evidence, and approval log."""
    changes = list(
        (
            await session.execute(
                select(ConductorChange)
                .where(
                    ConductorChange.proposal_id == proposal.id,
                    ConductorChange.tenant_id == proposal.tenant_id,
                )
                .order_by(ConductorChange.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    evidence = list(
        (
            await session.execute(
                select(ConductorEvidence)
                .where(
                    ConductorEvidence.proposal_id == proposal.id,
                    ConductorEvidence.tenant_id == proposal.tenant_id,
                )
                .order_by(ConductorEvidence.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    approvals = list(
        (
            await session.execute(
                select(ConductorApproval)
                .where(
                    ConductorApproval.proposal_id == proposal.id,
                    ConductorApproval.tenant_id == proposal.tenant_id,
                )
                .order_by(ConductorApproval.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    ready_to_publish = (
        proposal.status == ConductorProposalStatusEnum.APPLIED.value
        and proposal.resulting_version_number is not None
    )

    return ConductorProposalResponse(
        id=proposal.id,
        session_id=proposal.session_id,
        tenant_id=proposal.tenant_id,
        environment_id=proposal.environment_id,
        agent_id=proposal.agent_id,
        agent_kind=proposal.agent_kind,
        base_agent_version_id=proposal.base_agent_version_id,
        base_version_number=proposal.base_version_number,
        base_config_hash=proposal.base_config_hash,
        base_draft_etag=proposal.base_draft_etag,
        base_config_snapshot=dict(proposal.base_config_snapshot or {}),
        request_text=proposal.request_text,
        summary=proposal.summary,
        rationale=proposal.rationale,
        status=proposal.status,
        validation_status=proposal.validation_status,
        validation_report=dict(proposal.validation_report or {}),
        simulation_status=proposal.simulation_status,
        simulation_summary=dict(proposal.simulation_summary or {}),
        risk_summary=dict(proposal.risk_summary or {}),
        candidate_config_snapshot=dict(proposal.candidate_config_snapshot or {}),
        final_candidate_hash=proposal.final_candidate_hash,
        resulting_agent_version_id=proposal.resulting_agent_version_id,
        resulting_version_number=proposal.resulting_version_number,
        apply_idempotency_key=proposal.apply_idempotency_key,
        approved_change_hash=proposal.approved_change_hash,
        is_mock_provider=bool(proposal.is_mock_provider),
        provider=proposal.provider,
        model=proposal.model,
        correlation_id=proposal.correlation_id,
        error_code=proposal.error_code,
        error_message=proposal.error_message,
        production_published=False,
        ready_to_publish=ready_to_publish,
        changes=[ConductorChangeResponse.model_validate(c) for c in changes],
        evidence=[ConductorEvidenceResponse.model_validate(e) for e in evidence],
        approvals=[ConductorApprovalEntryResponse.model_validate(a) for a in approvals],
        created_by=proposal.created_by,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        applied_at=proposal.applied_at,
    )


async def get_proposal_row(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> ConductorProposal:
    """Load a ``ConductorProposal`` scoped to ``tenant_id`` or raise ``NotFoundError``."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    row = (
        await session.execute(
            select(ConductorProposal).where(
                ConductorProposal.id == p_id,
                ConductorProposal.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"ConductorProposal {proposal_id} not found.")
    return row
