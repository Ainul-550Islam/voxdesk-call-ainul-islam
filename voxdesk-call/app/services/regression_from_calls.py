"""Convert production Calls into PII-redacted regression TestCases (Part 6 / Gate G7).

Extracts caller turns as a multi-turn script, redacts PII per policy via
``app.gdpr.redact.redact``, drafts assertions (via ``app.ai.gateway.govern`` when
an LLM executor is available, plus grounded call-outcome/tool/transcript rules)
marked ``needs_review=True``, and persists a version-pinned ``TestCase``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gateway
from app.core.errors import BadRequestError, NotFoundError
from app.db.models import (
    Agent,
    AgentVersion,
    AuditAction,
    AuditLog,
    Call,
    CallStatus,
    EvaluationRule,
    Speaker,
    Tenant,
    TestCase,
    TestRunModeEnum,
    TransferState,
    Turn,
)
from app.domain.evaluation_models import EvaluationRuleType, validate_rule_config
from app.gdpr.redact import RedactionResult, redact as gdpr_redact
from app.services.simulation_caller import _SyntheticGatewayContext
from app.services.simulation_service import resolve_pinned_agent_version_async


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(val: uuid.UUID | str, field_name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {field_name}: {val!r}") from exc


def _apply_redaction(
    text: str,
    *,
    enabled: bool = True,
    policy: dict[str, bool] | None = None,
) -> RedactionResult:
    if not enabled or not text:
        return RedactionResult(text=text or "")
    pol = policy or {}
    return gdpr_redact(
        text,
        emails=bool(pol.get("emails", True)),
        phones=bool(pol.get("phones", True)),
        cards=bool(pol.get("cards", True)),
        ssns=bool(pol.get("ssns", True)),
    )


def _redact_structure(
    value: Any,
    *,
    enabled: bool = True,
    policy: dict[str, bool] | None = None,
    kinds_accum: set[str],
    count_accum: list[int],
) -> Any:
    if isinstance(value, str):
        res = _apply_redaction(value, enabled=enabled, policy=policy)
        kinds_accum.update(res.kinds)
        count_accum[0] += res.total
        return res.text
    if isinstance(value, list):
        return [
            _redact_structure(
                item,
                enabled=enabled,
                policy=policy,
                kinds_accum=kinds_accum,
                count_accum=count_accum,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(k): _redact_structure(
                v,
                enabled=enabled,
                policy=policy,
                kinds_accum=kinds_accum,
                count_accum=count_accum,
            )
            for k, v in value.items()
        }
    return value


async def _resolve_agent_for_call(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    call: Call,
    *,
    explicit_agent_id: str | None = None,
    explicit_version_number: int | None = None,
) -> dict[str, Any]:
    target_agent_id = explicit_agent_id or (str(call.agent_id) if call.agent_id else None)
    target_version = explicit_version_number

    if target_version is None and call.agent_version_id is not None:
        ver_row = await session.get(AgentVersion, call.agent_version_id)
        if ver_row is not None and ver_row.tenant_id == tenant_id:
            target_version = int(ver_row.version_number)
            if not target_agent_id:
                target_agent_id = str(ver_row.agent_id)

    if not target_agent_id:
        first_agent = (
            await session.execute(
                select(Agent)
                .where(Agent.tenant_id == tenant_id)
                .order_by(Agent.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if first_agent is not None:
            target_agent_id = str(first_agent.id)

    if not target_agent_id:
        raise BadRequestError(
            "Call does not reference an agent_id and no Agent exists for this tenant; "
            "supply agent_id explicitly."
        )

    if target_version is None:
        latest_ver = (
            await session.execute(
                select(AgentVersion)
                .where(
                    AgentVersion.tenant_id == tenant_id,
                    AgentVersion.agent_id == _ensure_uuid(target_agent_id, "agent_id"),
                )
                .order_by(AgentVersion.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        target_version = int(latest_ver.version_number) if latest_ver is not None else 1

    return await resolve_pinned_agent_version_async(
        session,
        tenant_id,
        target_agent_id,
        target_version,
        agent_kind="voice",
    )


async def _draft_assertions(
    session: AsyncSession,
    tenant: Tenant | None,
    *,
    call: Call,
    caller_turns: list[dict[str, Any]],
    assistant_turns: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    redacted_summary: str,
    executor: Any | None = None,
) -> list[dict[str, Any]]:
    drafted: list[dict[str, Any]] = []

    if tenant is not None and executor is not None:
        ctx = _SyntheticGatewayContext(tenant=tenant, tenant_id=tenant.id)
        prompt = json.dumps(
            {
                "task": "draft_regression_assertions",
                "call_id": str(call.id),
                "intent": call.intent,
                "booked": bool(call.booked),
                "escalated": bool(call.escalated),
                "summary": redacted_summary,
                "caller_turns": [t["content"] for t in caller_turns],
                "assistant_turns": [t["content"] for t in assistant_turns],
                "tool_calls": tool_calls,
            },
            sort_keys=True,
            default=str,
        )
        try:
            gw_res = await gateway.govern(
                session,
                ctx,
                text=prompt,
                channel="text",
                executor=executor,
                tokens_estimate=96,
                record_usage=False,
            )
            parsed = json.loads(gw_res.text or "{}")
            raw_rules = parsed.get("assertions") or parsed.get("rules") or []
            if isinstance(raw_rules, list):
                for idx, item in enumerate(raw_rules):
                    if not isinstance(item, dict):
                        continue
                    r_type = str(item.get("rule_type") or "llm_judge").strip().lower()
                    try:
                        cfg = validate_rule_config(r_type, dict(item.get("config") or {}))
                    except ValueError:
                        continue
                    cfg["needs_review"] = True
                    cfg["drafted_by"] = f"{gw_res.provider}/{gw_res.model}"
                    drafted.append(
                        {
                            "name": str(item.get("name") or f"LLM Drafted Assertion #{idx + 1}"),
                            "rule_type": r_type,
                            "config": cfg,
                            "enabled": True,
                            "weight": float(item.get("weight", 1.0)),
                            "evaluator_version": "v1.0",
                            "needs_review": True,
                        }
                    )
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            pass

    # Deterministic grounded assertions from real call facts (always marked needs_review)
    transferred = bool(
        call.escalated
        or call.status == CallStatus.TRANSFERRED
        or (call.transfer_state is not None and call.transfer_state != TransferState.NONE)
    )
    if transferred:
        cfg = validate_rule_config(
            EvaluationRuleType.TRANSFER_OCCURRED,
            {
                "expected": True,
                **(
                    {"destination": call.transfer_destination}
                    if call.transfer_destination
                    else {}
                ),
            },
        )
        cfg["needs_review"] = True
        drafted.append(
            {
                "name": "Verify call handoff / transfer behavior",
                "rule_type": EvaluationRuleType.TRANSFER_OCCURRED.value,
                "config": cfg,
                "enabled": True,
                "weight": 1.0,
                "evaluator_version": "v1.0",
                "needs_review": True,
            }
        )

    for tc in tool_calls:
        tool_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
        if not tool_name:
            continue
        cfg = validate_rule_config(
            EvaluationRuleType.TOOL_CALLED, {"tool_name": tool_name}
        )
        cfg["needs_review"] = True
        drafted.append(
            {
                "name": f"Verify tool '{tool_name}' is invoked",
                "rule_type": EvaluationRuleType.TOOL_CALLED.value,
                "config": cfg,
                "enabled": True,
                "weight": 1.0,
                "evaluator_version": "v1.0",
                "needs_review": True,
            }
        )

    if call.booked and not any(
        r["rule_type"] == EvaluationRuleType.TOOL_CALLED.value for r in drafted
    ):
        cfg = validate_rule_config(
            EvaluationRuleType.TOOL_CALLED, {"tool_name": "book_appointment"}
        )
        cfg["needs_review"] = True
        drafted.append(
            {
                "name": "Verify appointment booking tool invocation",
                "rule_type": EvaluationRuleType.TOOL_CALLED.value,
                "config": cfg,
                "enabled": True,
                "weight": 1.0,
                "evaluator_version": "v1.0",
                "needs_review": True,
            }
        )

    rubric_basis = (
        redacted_summary
        or (assistant_turns[-1]["content"] if assistant_turns else "")
        or f"Handle caller intent '{call.intent or 'general_inquiry'}' accurately."
    )
    judge_cfg = validate_rule_config(
        EvaluationRuleType.LLM_JUDGE,
        {
            "rubric": (
                f"Agent should handle the caller scenario consistently with outcome: "
                f"{rubric_basis[:300]}"
            ),
            "pass_threshold": 0.7,
        },
    )
    judge_cfg["needs_review"] = True
    drafted.append(
        {
            "name": "Verify overall response quality & outcome (Needs Review)",
            "rule_type": EvaluationRuleType.LLM_JUDGE.value,
            "config": judge_cfg,
            "enabled": True,
            "weight": 1.0,
            "evaluator_version": "v1.0",
            "needs_review": True,
        }
    )

    return drafted


async def create_regression_test_from_call(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    call_id: uuid.UUID | str,
    *,
    suite_id: uuid.UUID | str | None = None,
    name: str | None = None,
    agent_id: str | None = None,
    agent_version_number: int | None = None,
    redact_pii: bool = True,
    pii_policy: dict[str, bool] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    executor: Any | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> TestCase:
    """Convert a persisted Call into a PII-redacted regression TestCase with ``needs_review`` assertions."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    c_id = _ensure_uuid(call_id, "call_id")
    s_id = _ensure_uuid(suite_id, "suite_id") if suite_id is not None else None

    call = (
        await session.execute(
            select(Call).where(Call.id == c_id, Call.tenant_id == t_id)
        )
    ).scalar_one_or_none()
    if call is None:
        raise NotFoundError(f"Call {call_id} not found for tenant.")

    tenant = await session.get(Tenant, t_id)
    pinned = await _resolve_agent_for_call(
        session,
        t_id,
        call,
        explicit_agent_id=agent_id,
        explicit_version_number=agent_version_number,
    )

    turns = list(
        (
            await session.execute(
                select(Turn)
                .where(Turn.call_id == call.id)
                .order_by(Turn.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    redacted_kinds: set[str] = set()
    redaction_counter = [0]

    caller_script: list[dict[str, Any]] = []
    assistant_reference: list[dict[str, Any]] = []

    for idx, turn in enumerate(turns):
        res = _apply_redaction(
            turn.text or "", enabled=redact_pii, policy=pii_policy
        )
        redacted_kinds.update(res.kinds)
        redaction_counter[0] += res.total
        speaker_val = (
            turn.speaker.value
            if hasattr(turn.speaker, "value")
            else str(turn.speaker)
        ).lower()
        if speaker_val == Speaker.USER.value or speaker_val == "user":
            caller_script.append(
                {
                    "role": "user",
                    "content": res.text,
                    "turn_index": len(caller_script),
                    "source_turn_id": str(turn.id),
                }
            )
        else:
            assistant_reference.append(
                {
                    "role": "assistant",
                    "content": res.text,
                    "turn_index": idx,
                    "source_turn_id": str(turn.id),
                }
            )

    if not caller_script:
        fallback_res = _apply_redaction(
            call.summary or "Hello, I need assistance.",
            enabled=redact_pii,
            policy=pii_policy,
        )
        redacted_kinds.update(fallback_res.kinds)
        redaction_counter[0] += fallback_res.total
        caller_script.append(
            {
                "role": "user",
                "content": fallback_res.text,
                "turn_index": 0,
            }
        )

    summary_res = _apply_redaction(
        call.summary or "", enabled=redact_pii, policy=pii_policy
    )
    redacted_kinds.update(summary_res.kinds)
    redaction_counter[0] += summary_res.total

    extracted_tools = list(tool_calls or [])
    if not extracted_tools and isinstance(call.transfer_context, dict):
        raw_tc = call.transfer_context.get("tool_calls")
        if isinstance(raw_tc, list):
            extracted_tools = list(raw_tc)

    redacted_tools: list[dict[str, Any]] = _redact_structure(
        extracted_tools,
        enabled=redact_pii,
        policy=pii_policy,
        kinds_accum=redacted_kinds,
        count_accum=redaction_counter,
    )

    drafted_rules = await _draft_assertions(
        session,
        tenant,
        call=call,
        caller_turns=caller_script,
        assistant_turns=assistant_reference,
        tool_calls=redacted_tools,
        redacted_summary=summary_res.text,
        executor=executor,
    )

    now = _now()
    case_name = (
        name.strip()
        if name and name.strip()
        else f"Regression from call {str(call.id)[:8]} ({call.intent or 'general'})"
    )
    status_str = (
        call.status.value if hasattr(call.status, "value") else str(call.status)
    )

    case_row = TestCase(
        id=uuid.uuid4(),
        tenant_id=t_id,
        suite_id=s_id,
        agent_id=str(pinned["agent_id"]),
        agent_kind=str(pinned["agent_kind"]),
        agent_version_id=pinned["version_id"],
        agent_version_number=int(pinned["version_number"]),
        name=case_name[:200],
        mode=TestRunModeEnum.SIMULATION.value,
        input_messages=caller_script,
        dynamic_variables={},
        metadata_json={
            "source": "production_call",
            "source_call_id": str(call.id),
            "source_call_sid": call.call_sid,
            "needs_review": True,
            "pii_redacted": bool(redact_pii),
            "redacted_kinds": sorted(redacted_kinds),
            "redaction_count": redaction_counter[0],
            "call_outcome": {
                "status": status_str,
                "intent": call.intent,
                "booked": bool(call.booked),
                "escalated": bool(call.escalated),
                "summary": summary_res.text,
            },
            "reference_assistant_turns": assistant_reference,
            "tool_calls": redacted_tools,
        },
        expected_rules=drafted_rules,
        enabled=True,
        created_by=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(case_row)
    await session.flush()

    for rule_spec in drafted_rules:
        session.add(
            EvaluationRule(
                id=uuid.uuid4(),
                tenant_id=t_id,
                suite_id=s_id,
                test_case_id=case_row.id,
                name=str(rule_spec["name"])[:200],
                rule_type=str(rule_spec["rule_type"]),
                config=dict(rule_spec.get("config") or {}),
                evaluator_version=str(rule_spec.get("evaluator_version") or "v1.0"),
                enabled=bool(rule_spec.get("enabled", True)),
                weight=float(rule_spec.get("weight", 1.0)),
                created_by=actor_user_id,
                created_at=now,
                updated_at=now,
            )
        )

    session.add(
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            action=AuditAction.GOVERNANCE_EVENT,
            detail={
                "event": "regression_test.created_from_call",
                "test_case_id": str(case_row.id),
                "source_call_id": str(call.id),
                "needs_review": True,
                "pii_redacted": bool(redact_pii),
                "redacted_kinds": sorted(redacted_kinds),
            },
            created_at=now,
        )
    )
    await session.flush()
    return case_row
