"""Deterministic and LLM-as-Judge Evaluation Engine (PostgreSQL-backed).

Responsibilities
----------------
1. Manage durable ``EvaluationRule`` and ``EvaluationResult`` rows scoped to
   ``tenant_id``.
2. Execute all 13 deterministic rule types (``contains``, ``not_contains``,
   ``regex``, ``json_path_equals``, ``json_path_exists``, ``tool_called``,
   ``tool_not_called``, ``transfer_occurred``, ``variable_equals``,
   ``turn_count_max``, ``turn_count_min``, ``latency_ms_max``,
   ``final_state_equals``) plus optional ``llm_judge`` against actual persisted
   ``TestRun`` evidence (transcripts, tool calls, events, latency, variables).
3. Support re-evaluating an existing ``TestRun`` against updated rules without
   re-running the conversation or mutating ``transcript_snapshot``.
4. Compute transparent QA scorecards that explicitly report ``NO_ASSERTIONS``
   when zero enabled rules are present and distinguish ``FAILED_ASSERTION``
   from ``EVALUATION_ERROR``.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditAction
from app.auth.identity.events import scrub
from app.auth.service import record_audit
from app.core.errors import BadRequestError, NotFoundError
from app.db.retell_models import (
    EvaluationResult,
    EvaluationResultStatusEnum,
    EvaluationRule,
    EvaluationRuleTypeEnum as EvaluationRuleTypeEnum,
    TestCase,
    TestRun,
    TestRunStatusEnum,
    TestSuite,
)
from app.domain.evaluation_models import (
    EvaluationEvidencePayload,
    EvaluationResultStatus as EvaluationResultStatus,
    EvaluationRuleCreateRequest,
    EvaluationRuleType,
    EvaluationRuleUpdateRequest,
    QAScorecardSummary,
    ScorecardStatus,
    _coerce_rule_type,
    validate_rule_config,
    validate_safe_json_path,
    validate_safe_regex_pattern,
)

EVALUATOR_ENGINE_VERSION = "v1.0"
SCORECARD_FORMULA_VERSION = "weighted_v1"
MAX_REGEX_INPUT_CHARS = 65536

JudgeCallable = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
_CUSTOM_JUDGE_HANDLER: JudgeCallable | None = None


def set_custom_judge_handler(handler: JudgeCallable | None) -> None:
    """Register or clear an injectable async LLM judge handler for tests or custom providers."""
    global _CUSTOM_JUDGE_HANDLER
    _CUSTOM_JUDGE_HANDLER = handler


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(value: uuid.UUID | str, field_name: str = "id") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {field_name}: {value}") from exc


async def _audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    event: str,
    detail: dict[str, Any],
) -> None:
    await record_audit(
        session,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email="system",
        detail=scrub({"event": event, "operation": event, **detail}),
        commit=False,
    )


# --------------------------------------------------------- Rule CRUD Operations


async def create_evaluation_rule(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: EvaluationRuleCreateRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> EvaluationRule:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    validated_cfg = validate_rule_config(payload.rule_type, payload.config)

    if payload.suite_id is not None:
        suite = (
            await session.execute(
                select(TestSuite).where(
                    TestSuite.id == payload.suite_id,
                    TestSuite.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if suite is None:
            raise NotFoundError(f"TestSuite {payload.suite_id} not found for tenant.")

    if payload.test_case_id is not None:
        case = (
            await session.execute(
                select(TestCase).where(
                    TestCase.id == payload.test_case_id,
                    TestCase.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if case is None:
            raise NotFoundError(f"TestCase {payload.test_case_id} not found for tenant.")

    now = _now()
    row = EvaluationRule(
        id=uuid.uuid4(),
        tenant_id=t_id,
        suite_id=payload.suite_id,
        test_case_id=payload.test_case_id,
        name=payload.name.strip(),
        rule_type=payload.rule_type.value,
        config=validated_cfg,
        evaluator_version=payload.evaluator_version or EVALUATOR_ENGINE_VERSION,
        enabled=bool(payload.enabled),
        weight=float(payload.weight),
        created_by=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()

    await _audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="evaluation.rule.created",
        detail={
            "rule_id": str(row.id),
            "name": row.name,
            "rule_type": row.rule_type,
            "suite_id": str(row.suite_id) if row.suite_id else None,
            "test_case_id": str(row.test_case_id) if row.test_case_id else None,
        },
    )
    return row


async def list_evaluation_rules(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    suite_id: uuid.UUID | str | None = None,
    test_case_id: uuid.UUID | str | None = None,
    enabled_only: bool = False,
) -> list[EvaluationRule]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(EvaluationRule).where(EvaluationRule.tenant_id == t_id)
    if suite_id is not None:
        stmt = stmt.where(EvaluationRule.suite_id == _ensure_uuid(suite_id, "suite_id"))
    if test_case_id is not None:
        stmt = stmt.where(
            EvaluationRule.test_case_id == _ensure_uuid(test_case_id, "test_case_id")
        )
    if enabled_only:
        stmt = stmt.where(EvaluationRule.enabled.is_(True))
    stmt = stmt.order_by(EvaluationRule.created_at.asc())
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def get_evaluation_rule(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    rule_id: uuid.UUID | str,
) -> EvaluationRule:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    r_id = _ensure_uuid(rule_id, "rule_id")
    row = (
        await session.execute(
            select(EvaluationRule).where(
                EvaluationRule.id == r_id,
                EvaluationRule.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"EvaluationRule {rule_id} not found.")
    return row


async def update_evaluation_rule(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    rule_id: uuid.UUID | str,
    payload: EvaluationRuleUpdateRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> EvaluationRule:
    row = await get_evaluation_rule(session, tenant_id, rule_id)
    target_type = payload.rule_type.value if payload.rule_type is not None else row.rule_type
    target_cfg = payload.config if payload.config is not None else row.config
    validated_cfg = validate_rule_config(target_type, target_cfg)

    if payload.name is not None:
        row.name = payload.name.strip()
    row.rule_type = target_type
    row.config = validated_cfg
    if payload.evaluator_version is not None:
        row.evaluator_version = payload.evaluator_version
    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)
    if payload.weight is not None:
        row.weight = float(payload.weight)
    row.updated_at = _now()
    await session.flush()

    await _audit(
        session,
        tenant_id=row.tenant_id,
        actor_user_id=actor_user_id,
        event="evaluation.rule.updated",
        detail={"rule_id": str(row.id), "rule_type": row.rule_type, "enabled": row.enabled},
    )
    return row


async def delete_evaluation_rule(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    rule_id: uuid.UUID | str,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> bool:
    row = await get_evaluation_rule(session, tenant_id, rule_id)
    t_id = row.tenant_id
    r_id = str(row.id)
    await session.delete(row)
    await session.flush()
    await _audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="evaluation.rule.deleted",
        detail={"rule_id": r_id},
    )
    return True


# ---------------------------------------------- Bounded JSONPath & Text Helpers


def _resolve_json_path(data: Any, path: str) -> tuple[bool, Any]:
    """Resolve a bounded JSONPath against nested dict/list structures safely."""
    normalized = validate_safe_json_path(path)
    expr = normalized.lstrip("$").lstrip(".")
    if not expr:
        return True, data

    tokens: list[str | int] = []
    for part in expr.split("."):
        if not part:
            continue
        bracket_matches = list(re.finditer(r"([^\[\]]+)|\[(\d+|'[^']+'|\"[^\"]+\")\]", part))
        for m in bracket_matches:
            key_token, idx_token = m.group(1), m.group(2)
            if key_token is not None:
                tokens.append(key_token)
            elif idx_token is not None:
                if idx_token.isdigit():
                    tokens.append(int(idx_token))
                else:
                    tokens.append(idx_token.strip("'\""))

    current: Any = data
    for tok in tokens:
        if isinstance(tok, int):
            if isinstance(current, list) and 0 <= tok < len(current):
                current = current[tok]
            else:
                return False, None
        else:
            if isinstance(current, dict) and tok in current:
                current = current[tok]
            else:
                return False, None
    return True, current


def _extract_assistant_turns(transcript: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for idx, turn in enumerate(transcript or []):
        role = str(turn.get("role") or "").lower()
        if role in ("assistant", "agent", "bot"):
            out.append((idx, turn))
    return out


def _extract_target_text(
    transcript: list[dict[str, Any]],
    final_output: dict[str, Any],
    target: str,
) -> tuple[str, list[tuple[int, str]]]:
    """Return combined text and per-turn ``(turn_index, content)`` pairs for inspection."""
    assistant_turns = _extract_assistant_turns(transcript)
    if target == "last_reply":
        if not assistant_turns:
            return "", []
        last_idx, last_turn = assistant_turns[-1]
        txt = str(last_turn.get("content") or last_turn.get("text") or "")
        return txt, [(last_idx, txt)]
    if target == "full_transcript":
        pairs = [
            (idx, str(t.get("content") or t.get("text") or ""))
            for idx, t in enumerate(transcript or [])
        ]
        return "\n".join(p[1] for p in pairs), pairs
    if target == "final_output":
        serialized = json.dumps(final_output or {}, sort_keys=True)
        return serialized, [(0, serialized)]

    # Default: assistant_transcript
    pairs = [
        (idx, str(t.get("content") or t.get("text") or ""))
        for idx, t in assistant_turns
    ]
    return "\n".join(p[1] for p in pairs), pairs


def _collect_all_tool_calls(
    transcript: list[dict[str, Any]],
    events: list[dict[str, Any]],
    final_output: dict[str, Any],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for idx, turn in enumerate(transcript or []):
        for tc in turn.get("tool_calls") or []:
            if isinstance(tc, dict):
                item = dict(tc)
                item.setdefault("turn_index", idx)
                collected.append(item)
        if str(turn.get("role") or "").lower() == "tool":
            collected.append(
                {
                    "name": turn.get("tool_name") or turn.get("name") or "tool",
                    "arguments": turn.get("arguments") or {},
                    "result": turn.get("content") or turn.get("result"),
                    "turn_index": idx,
                }
            )
    for ev in events or []:
        if isinstance(ev, dict) and ev.get("event") in ("tool_call", "tool_invoked", "tool_called"):
            collected.append(
                {
                    "name": ev.get("tool_name") or ev.get("name") or "",
                    "arguments": ev.get("arguments") or ev.get("args") or {},
                    "turn_index": ev.get("turn_index", 0),
                }
            )
    for tc in (final_output or {}).get("tool_calls") or []:
        if isinstance(tc, dict):
            collected.append(dict(tc))
    return collected


# ------------------------------------------------ Single Rule Execution Engine


async def evaluate_single_rule(
    *,
    rule_name: str,
    rule_type: str | EvaluationRuleType,
    config: dict[str, Any],
    weight: float = 1.0,
    evaluator_version: str = EVALUATOR_ENGINE_VERSION,
    transcript: list[dict[str, Any]],
    events: list[dict[str, Any]],
    usage_metadata: dict[str, Any],
    latency_metadata: dict[str, Any],
    final_output: dict[str, Any],
    dynamic_variables: dict[str, Any] | None = None,
    run_status: str = "completed",
    allow_mock_judge: bool = False,
) -> dict[str, Any]:
    """Evaluate a single rule against persisted run evidence and return a structured result dict."""
    try:
        rt = _coerce_rule_type(rule_type)
        cfg = validate_rule_config(rt, config or {})
    except Exception as exc:
        evidence = EvaluationEvidencePayload(error_detail=str(exc)).model_dump()
        return {
            "rule_name": rule_name,
            "rule_type": rule_type.value if hasattr(rule_type, "value") else str(rule_type),
            "status": EvaluationResultStatusEnum.EVALUATION_ERROR.value,
            "score": 0.0,
            "weight": float(weight),
            "evidence": evidence,
            "explanation": f"Invalid rule configuration: {exc}",
            "evaluator_version": evaluator_version,
            "formula_version": SCORECARD_FORMULA_VERSION,
        }

    run_context = {
        "final_output": final_output or {},
        "variables": {
            **(dynamic_variables or {}),
            **((final_output or {}).get("variables") or {}),
        },
        "usage": usage_metadata or {},
        "latency": latency_metadata or {},
        "transcript": transcript or [],
        "events": events or [],
        "status": str((final_output or {}).get("final_state") or run_status),
        **(final_output or {}),
    }

    try:
        # 1. CONTAINS / NOT_CONTAINS
        if rt in (EvaluationRuleType.CONTAINS, EvaluationRuleType.NOT_CONTAINS):
            substring = str(cfg["substring"])
            case_sensitive = bool(cfg.get("case_sensitive", False))
            target = str(cfg.get("target") or "assistant_transcript")
            full_text, turn_pairs = _extract_target_text(transcript, final_output, target)
            needle = substring if case_sensitive else substring.lower()

            matched_indices: list[int] = []
            matched_snippets: list[str] = []
            for idx, txt in turn_pairs:
                haystack = txt if case_sensitive else txt.lower()
                if needle in haystack:
                    matched_indices.append(idx)
                    matched_snippets.append(txt[:240])

            found = len(matched_indices) > 0 or (
                needle in (full_text if case_sensitive else full_text.lower())
            )
            passed = found if rt == EvaluationRuleType.CONTAINS else not found
            status = (
                EvaluationResultStatusEnum.PASSED.value
                if passed
                else EvaluationResultStatusEnum.FAILED_ASSERTION.value
            )
            explanation = (
                f"Substring {substring!r} {'found' if found else 'not found'} in {target}."
            )
            evidence = EvaluationEvidencePayload(
                matched_turn_indices=matched_indices,
                matched_snippets=matched_snippets,
                expected=substring if rt == EvaluationRuleType.CONTAINS else f"NOT {substring}",
                actual=matched_snippets[0] if matched_snippets else full_text[:240],
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": status,
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": explanation,
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 2. REGEX
        if rt == EvaluationRuleType.REGEX:
            pattern_str = validate_safe_regex_pattern(str(cfg["pattern"]))
            flags = 0 if cfg.get("case_sensitive", False) else re.IGNORECASE
            compiled = re.compile(pattern_str, flags)
            target = str(cfg.get("target") or "assistant_transcript")
            full_text, turn_pairs = _extract_target_text(transcript, final_output, target)

            matched_indices = []
            matched_snippets = []
            for idx, txt in turn_pairs:
                bounded_txt = txt[:MAX_REGEX_INPUT_CHARS]
                m = compiled.search(bounded_txt)
                if m:
                    matched_indices.append(idx)
                    matched_snippets.append(m.group(0)[:240])

            passed = len(matched_indices) > 0
            status = (
                EvaluationResultStatusEnum.PASSED.value
                if passed
                else EvaluationResultStatusEnum.FAILED_ASSERTION.value
            )
            evidence = EvaluationEvidencePayload(
                matched_turn_indices=matched_indices,
                matched_snippets=matched_snippets,
                expected=f"regex:{pattern_str}",
                actual=matched_snippets[0] if matched_snippets else full_text[:240],
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": status,
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": (
                    f"Regex /{pattern_str}/ matched turn(s) {matched_indices}."
                    if passed
                    else f"Regex /{pattern_str}/ did not match any turn in {target}."
                ),
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 3. JSON_PATH_EXISTS / JSON_PATH_EQUALS
        if rt in (EvaluationRuleType.JSON_PATH_EXISTS, EvaluationRuleType.JSON_PATH_EQUALS):
            path_expr = str(cfg["path"])
            exists, actual_val = _resolve_json_path(run_context, path_expr)
            if rt == EvaluationRuleType.JSON_PATH_EXISTS:
                passed = exists and actual_val is not None
                expected_repr: Any = "path_exists"
                explanation = (
                    f"JSONPath {path_expr} exists with value {actual_val!r}."
                    if passed
                    else f"JSONPath {path_expr} was not found or was null."
                )
            else:
                expected_repr = cfg.get("expected")
                passed = exists and actual_val == expected_repr
                explanation = (
                    f"JSONPath {path_expr} matched expected value {expected_repr!r}."
                    if passed
                    else f"JSONPath {path_expr} resolved to {actual_val!r}, expected {expected_repr!r}."
                )
            evidence = EvaluationEvidencePayload(
                expected=expected_repr,
                actual=actual_val,
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": explanation,
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 4. TOOL_CALLED / TOOL_NOT_CALLED
        if rt in (EvaluationRuleType.TOOL_CALLED, EvaluationRuleType.TOOL_NOT_CALLED):
            expected_tool = str(cfg["tool_name"]).strip()
            expected_args = cfg.get("expected_args")
            all_tools = _collect_all_tool_calls(transcript, events, final_output)
            matching_calls: list[dict[str, Any]] = []
            for tc in all_tools:
                t_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
                if t_name == expected_tool:
                    if isinstance(expected_args, dict) and expected_args:
                        actual_args = tc.get("arguments") or tc.get("args") or {}
                        if all(actual_args.get(k) == v for k, v in expected_args.items()):
                            matching_calls.append(tc)
                    else:
                        matching_calls.append(tc)

            called = len(matching_calls) > 0
            passed = called if rt == EvaluationRuleType.TOOL_CALLED else not called
            matched_turns = [
                int(tc.get("turn_index", 0))
                for tc in matching_calls
                if isinstance(tc.get("turn_index"), int)
            ]
            evidence = EvaluationEvidencePayload(
                matched_turn_indices=matched_turns,
                tool_calls_inspected=all_tools,
                expected=(
                    f"tool_called:{expected_tool}"
                    if rt == EvaluationRuleType.TOOL_CALLED
                    else f"tool_not_called:{expected_tool}"
                ),
                actual=[tc.get("name") or tc.get("tool_name") for tc in all_tools],
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": (
                    f"Tool {expected_tool!r} was {'called' if called else 'not called'} "
                    f"({len(matching_calls)} matching call(s))."
                ),
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 5. TRANSFER_OCCURRED
        if rt == EvaluationRuleType.TRANSFER_OCCURRED:
            expected_transfer = bool(cfg.get("expected", True))
            expected_dest = cfg.get("destination")
            all_tools = _collect_all_tool_calls(transcript, events, final_output)
            transfer_tools = [
                tc
                for tc in all_tools
                if str(tc.get("name") or tc.get("tool_name") or "").lower()
                in ("transfer_call", "escalate_to_human", "handoff_agent", "warm_transfer", "cold_transfer")
            ]
            transfer_events = [
                ev
                for ev in (events or [])
                if isinstance(ev, dict)
                and str(ev.get("event") or "").lower()
                in ("transfer", "transferred", "escalated", "handoff")
            ]
            fo_transferred = bool((final_output or {}).get("transferred"))
            occurred = bool(transfer_tools or transfer_events or fo_transferred)
            actual_dest = (
                (final_output or {}).get("transfer_destination")
                or (
                    (transfer_tools[0].get("arguments") or {}).get("destination")
                    if transfer_tools
                    else None
                )
                or (transfer_events[0].get("destination") if transfer_events else None)
            )
            dest_ok = True
            if expected_transfer and expected_dest:
                dest_ok = str(actual_dest or "").strip() == str(expected_dest).strip()

            passed = (occurred == expected_transfer) and dest_ok
            evidence = EvaluationEvidencePayload(
                tool_calls_inspected=transfer_tools,
                expected={"transfer_occurred": expected_transfer, "destination": expected_dest},
                actual={"transfer_occurred": occurred, "destination": actual_dest},
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": (
                    f"Transfer occurred={occurred} (destination={actual_dest!r}), "
                    f"expected={expected_transfer}."
                ),
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 6. VARIABLE_EQUALS
        if rt == EvaluationRuleType.VARIABLE_EQUALS:
            var_name = str(cfg["variable_name"])
            expected_val = cfg.get("expected")
            merged_vars = run_context["variables"]
            actual_val = merged_vars.get(var_name)
            passed = var_name in merged_vars and actual_val == expected_val
            evidence = EvaluationEvidencePayload(
                expected={var_name: expected_val},
                actual={var_name: actual_val},
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": (
                    f"Variable {var_name!r} equals {actual_val!r} (expected {expected_val!r})."
                ),
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 7. TURN_COUNT_MAX / TURN_COUNT_MIN
        if rt in (EvaluationRuleType.TURN_COUNT_MAX, EvaluationRuleType.TURN_COUNT_MIN):
            threshold = int(cfg["threshold"])
            scope = str(cfg.get("scope") or "all")
            if scope == "assistant":
                actual_turns = len(_extract_assistant_turns(transcript))
            else:
                actual_turns = len(transcript or [])
            passed = (
                actual_turns <= threshold
                if rt == EvaluationRuleType.TURN_COUNT_MAX
                else actual_turns >= threshold
            )
            evidence = EvaluationEvidencePayload(
                expected=f"{'<=' if rt == EvaluationRuleType.TURN_COUNT_MAX else '>='} {threshold}",
                actual=actual_turns,
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": (
                    f"Observed turn count {actual_turns} "
                    f"({'<=' if rt == EvaluationRuleType.TURN_COUNT_MAX else '>='} {threshold})."
                ),
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 8. LATENCY_MS_MAX
        if rt == EvaluationRuleType.LATENCY_MS_MAX:
            max_ms = int(cfg["max_ms"])
            turn_latencies = [
                int(t.get("latency_ms"))
                for t in (transcript or [])
                if t.get("latency_ms") is not None
            ]
            actual_ms = int(
                (latency_metadata or {}).get("p95_ms")
                or (latency_metadata or {}).get("max_turn_latency_ms")
                or (max(turn_latencies) if turn_latencies else 0)
                or (latency_metadata or {}).get("total_duration_ms")
                or 0
            )
            passed = actual_ms <= max_ms
            evidence = EvaluationEvidencePayload(
                expected=f"<= {max_ms}ms",
                actual=actual_ms,
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": f"Observed latency {actual_ms}ms (max allowed {max_ms}ms).",
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 9. FINAL_STATE_EQUALS
        if rt == EvaluationRuleType.FINAL_STATE_EQUALS:
            expected_state = str(cfg["expected_state"]).strip().lower()
            actual_state = str(
                (final_output or {}).get("final_state")
                or (final_output or {}).get("state")
                or run_status
                or ""
            ).strip().lower()
            passed = actual_state == expected_state
            evidence = EvaluationEvidencePayload(
                expected=expected_state,
                actual=actual_state,
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": 1.0 if passed else 0.0,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": f"Final state was {actual_state!r} (expected {expected_state!r}).",
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        # 10. LLM_JUDGE
        if rt == EvaluationRuleType.LLM_JUDGE:
            rubric = str(cfg["rubric"])
            pass_threshold = float(cfg.get("pass_threshold", 0.7))
            judge_provider = str(cfg.get("provider") or "openai")
            judge_model = str(cfg.get("model") or "gpt-4o-mini")
            judge_prompt_version = str(cfg.get("prompt_version") or "judge_v1")
            require_live_judge = bool(cfg.get("require_live_judge", False))

            full_text, _ = _extract_target_text(transcript, final_output, "full_transcript")
            if _CUSTOM_JUDGE_HANDLER is not None:
                judge_out = await _CUSTOM_JUDGE_HANDLER(rubric, full_text, cfg)
                score = float(judge_out.get("score", 0.0))
                passed = score >= pass_threshold
                rationale = str(judge_out.get("rationale") or "Evaluated by custom LLM judge.")
                evidence = EvaluationEvidencePayload(
                    expected=f"score >= {pass_threshold}",
                    actual=score,
                    judge_model=str(judge_out.get("model") or judge_model),
                    judge_provider=str(judge_out.get("provider") or judge_provider),
                    judge_prompt_version=judge_prompt_version,
                    judge_rationale=rationale,
                    is_mock_judge=bool(judge_out.get("is_mock", False)),
                ).model_dump()
                return {
                    "rule_name": rule_name,
                    "rule_type": rt.value,
                    "status": (
                        EvaluationResultStatusEnum.PASSED.value
                        if passed
                        else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                    ),
                    "score": score,
                    "weight": float(weight),
                    "evidence": evidence,
                    "explanation": rationale,
                    "evaluator_version": evaluator_version,
                    "formula_version": SCORECARD_FORMULA_VERSION,
                }

            has_live_key = bool(
                os.environ.get("OPENAI_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("GROQ_API_KEY")
            )
            if not has_live_key and (require_live_judge or not allow_mock_judge):
                evidence = EvaluationEvidencePayload(
                    judge_model=judge_model,
                    judge_provider=judge_provider,
                    judge_prompt_version=judge_prompt_version,
                    is_mock_judge=False,
                    error_detail="LLM judge provider credentials not configured in environment.",
                ).model_dump()
                return {
                    "rule_name": rule_name,
                    "rule_type": rt.value,
                    "status": EvaluationResultStatusEnum.EVALUATION_ERROR.value,
                    "score": 0.0,
                    "weight": float(weight),
                    "evidence": evidence,
                    "explanation": (
                        "LLM judge evaluation failed: live judge provider credentials are not configured."
                    ),
                    "evaluator_version": evaluator_version,
                    "formula_version": SCORECARD_FORMULA_VERSION,
                }

            # Deterministic rubric fallback when allow_mock_judge=True in test/sandbox mode
            required_terms = [
                w.strip(".,:;\"'").lower()
                for w in cfg.get("required_keywords", [])
                if str(w).strip()
            ]
            assistant_text, _ = _extract_target_text(
                transcript, final_output, "assistant_transcript"
            )
            lower_reply = assistant_text.lower()
            if required_terms:
                hits = sum(1 for term in required_terms if term in lower_reply)
                score = round(hits / len(required_terms), 4)
            else:
                score = 1.0 if len(assistant_text.strip()) > 0 else 0.0
            passed = score >= pass_threshold
            rationale = (
                f"[Sandbox Rubric Judge] Evaluated rubric ({rubric[:120]!r}) against "
                f"{len(_extract_assistant_turns(transcript))} assistant turn(s); score={score:.2f}."
            )
            evidence = EvaluationEvidencePayload(
                expected=f"score >= {pass_threshold}",
                actual=score,
                judge_model=judge_model,
                judge_provider="sandbox_rubric_judge",
                judge_prompt_version=judge_prompt_version,
                judge_rationale=rationale,
                is_mock_judge=True,
            ).model_dump()
            return {
                "rule_name": rule_name,
                "rule_type": rt.value,
                "status": (
                    EvaluationResultStatusEnum.PASSED.value
                    if passed
                    else EvaluationResultStatusEnum.FAILED_ASSERTION.value
                ),
                "score": score,
                "weight": float(weight),
                "evidence": evidence,
                "explanation": rationale,
                "evaluator_version": evaluator_version,
                "formula_version": SCORECARD_FORMULA_VERSION,
            }

        raise ValueError(f"Unsupported evaluation rule type: {rule_type}")
    except Exception as exc:
        evidence = EvaluationEvidencePayload(error_detail=str(exc)).model_dump()
        return {
            "rule_name": rule_name,
            "rule_type": str(rule_type),
            "status": EvaluationResultStatusEnum.EVALUATION_ERROR.value,
            "score": 0.0,
            "weight": float(weight),
            "evidence": evidence,
            "explanation": f"Evaluator runtime error: {exc}",
            "evaluator_version": evaluator_version,
            "formula_version": SCORECARD_FORMULA_VERSION,
        }


# ----------------------------------------- Scorecard & Full Run Evaluation API


def compute_scorecard_summary(
    results: list[dict[str, Any]],
    *,
    total_rules_count: int | None = None,
    pass_threshold: float = 100.0,
) -> QAScorecardSummary:
    """Compute a transparent QA scorecard from evaluation results.

    When zero enabled rules are evaluated, returns ``status = NO_ASSERTIONS``
    and ``overall_score = None`` — never a fabricated 100% pass.
    """
    enabled_count = len(results)
    total_count = total_rules_count if total_rules_count is not None else enabled_count
    skipped_count = max(0, total_count - enabled_count)
    now_iso = _now().isoformat()

    if enabled_count == 0:
        return QAScorecardSummary(
            status=ScorecardStatus.NO_ASSERTIONS,
            overall_score=None,
            pass_threshold=pass_threshold,
            formula_version=SCORECARD_FORMULA_VERSION,
            evaluator_version=EVALUATOR_ENGINE_VERSION,
            total_rules=total_count,
            enabled_rules=0,
            passed_count=0,
            failed_assertion_count=0,
            evaluation_error_count=0,
            skipped_count=skipped_count,
            weighted_earned=0.0,
            weighted_possible=0.0,
            explanation=(
                "No enabled evaluation rules configured for this run; "
                "status is NO_ASSERTIONS and no pass/fail score was fabricated."
            ),
            evaluated_at=now_iso,
        )

    passed_count = sum(
        1 for r in results if r.get("status") == EvaluationResultStatusEnum.PASSED.value
    )
    failed_count = sum(
        1 for r in results if r.get("status") == EvaluationResultStatusEnum.FAILED_ASSERTION.value
    )
    error_count = sum(
        1 for r in results if r.get("status") == EvaluationResultStatusEnum.EVALUATION_ERROR.value
    )

    weighted_possible = sum(max(0.0, float(r.get("weight", 1.0))) for r in results)
    weighted_earned = sum(
        max(0.0, float(r.get("weight", 1.0))) * float(r.get("score", 0.0))
        for r in results
        if r.get("status") == EvaluationResultStatusEnum.PASSED.value
    )
    overall_score = (
        round((weighted_earned / weighted_possible) * 100.0, 2)
        if weighted_possible > 0
        else 0.0
    )

    if error_count > 0:
        status = ScorecardStatus.EVALUATION_ERROR
        explanation = (
            f"{error_count} rule(s) encountered an evaluator runtime/configuration error; "
            f"{passed_count}/{enabled_count} passed ({overall_score:.1f}%)."
        )
    elif failed_count > 0 or overall_score < pass_threshold:
        status = ScorecardStatus.FAILED_ASSERTION
        explanation = (
            f"{failed_count} assertion(s) failed; {passed_count}/{enabled_count} passed "
            f"with weighted score {overall_score:.1f}% (threshold {pass_threshold:.1f}%)."
        )
    else:
        status = ScorecardStatus.PASSED
        explanation = (
            f"All {passed_count}/{enabled_count} enabled assertion(s) passed "
            f"with weighted score {overall_score:.1f}%."
        )

    return QAScorecardSummary(
        status=status,
        overall_score=overall_score,
        pass_threshold=pass_threshold,
        formula_version=SCORECARD_FORMULA_VERSION,
        evaluator_version=EVALUATOR_ENGINE_VERSION,
        total_rules=total_count,
        enabled_rules=enabled_count,
        passed_count=passed_count,
        failed_assertion_count=failed_count,
        evaluation_error_count=error_count,
        skipped_count=skipped_count,
        weighted_earned=round(weighted_earned, 4),
        weighted_possible=round(weighted_possible, 4),
        explanation=explanation,
        evaluated_at=now_iso,
    )


async def evaluate_test_run(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    test_run: TestRun,
    *,
    inline_rules: list[dict[str, Any]] | None = None,
    allow_mock_judge: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> tuple[list[EvaluationResult], QAScorecardSummary]:
    """Evaluate a persisted ``TestRun`` against all applicable rules without re-running the call.

    Collects rules from:
    1. ``EvaluationRule`` rows bound to ``test_run.suite_id``
    2. ``EvaluationRule`` rows bound to ``test_run.test_case_id``
    3. ``TestCase.expected_rules`` on the linked ``TestCase`` (if any)
    4. ``inline_rules`` passed at execution time (persisted in ``final_output.inline_rules``)

    Deletes any prior ``EvaluationResult`` rows for ``test_run.id`` and writes fresh
    evidence-backed rows, updating ``test_run.scorecard_summary`` and ``test_run.status``.
    """
    t_id = _ensure_uuid(tenant_id, "tenant_id")

    db_rules: list[EvaluationRule] = []
    if test_run.suite_id is not None:
        suite_rules = await list_evaluation_rules(
            session, t_id, suite_id=test_run.suite_id, enabled_only=False
        )
        # Include suite-level rules that are not pinned to a different test case
        db_rules.extend(
            r
            for r in suite_rules
            if r.test_case_id is None or r.test_case_id == test_run.test_case_id
        )

    if test_run.test_case_id is not None:
        case_rules = await list_evaluation_rules(
            session, t_id, test_case_id=test_run.test_case_id, enabled_only=False
        )
        seen_ids = {r.id for r in db_rules}
        db_rules.extend(r for r in case_rules if r.id not in seen_ids)

    case_inline_rules: list[dict[str, Any]] = []
    if test_run.test_case_id is not None:
        case_row = (
            await session.execute(
                select(TestCase).where(
                    TestCase.id == test_run.test_case_id,
                    TestCase.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if case_row is not None and isinstance(case_row.expected_rules, list):
            case_inline_rules.extend(case_row.expected_rules)

    persisted_inline = (test_run.final_output or {}).get("inline_rules") or []
    effective_inline = inline_rules if inline_rules is not None else persisted_inline

    # Build combined list of rule specs
    all_specs: list[dict[str, Any]] = []
    for r in db_rules:
        all_specs.append(
            {
                "evaluation_rule_id": r.id,
                "name": r.name,
                "rule_type": r.rule_type,
                "config": r.config or {},
                "enabled": bool(r.enabled),
                "weight": float(r.weight),
                "evaluator_version": r.evaluator_version or EVALUATOR_ENGINE_VERSION,
            }
        )
    for spec in [*case_inline_rules, *effective_inline]:
        if isinstance(spec, dict):
            raw_rt = spec.get("rule_type") or "contains"
            rt_str = (
                raw_rt.value
                if hasattr(raw_rt, "value")
                else str(raw_rt).split(".")[-1].lower()
            )
            all_specs.append(
                {
                    "evaluation_rule_id": None,
                    "name": str(spec.get("name") or rt_str or "Inline Rule"),
                    "rule_type": rt_str,
                    "config": dict(spec.get("config") or {}),
                    "enabled": bool(spec.get("enabled", True)),
                    "weight": float(spec.get("weight", 1.0)),
                    "evaluator_version": str(
                        spec.get("evaluator_version") or EVALUATOR_ENGINE_VERSION
                    ),
                }
            )

    enabled_specs = [s for s in all_specs if s.get("enabled", True)]

    # Clear existing EvaluationResult rows for this test_run so reruns replace cleanly
    await session.execute(
        delete(EvaluationResult).where(
            EvaluationResult.tenant_id == t_id,
            EvaluationResult.test_run_id == test_run.id,
        )
    )

    dynamic_vars = (test_run.final_output or {}).get("dynamic_variables") or {}
    eval_dicts: list[dict[str, Any]] = []
    persisted_results: list[EvaluationResult] = []
    now = _now()

    for spec in enabled_specs:
        res_dict = await evaluate_single_rule(
            rule_name=spec["name"],
            rule_type=spec["rule_type"],
            config=spec["config"],
            weight=spec["weight"],
            evaluator_version=spec["evaluator_version"],
            transcript=list(test_run.transcript_snapshot or []),
            events=list(test_run.events_snapshot or []),
            usage_metadata=dict(test_run.usage_metadata or {}),
            latency_metadata=dict(test_run.latency_metadata or {}),
            final_output=dict(test_run.final_output or {}),
            dynamic_variables=dynamic_vars,
            run_status=test_run.status,
            allow_mock_judge=allow_mock_judge,
        )
        eval_dicts.append(res_dict)
        row = EvaluationResult(
            id=uuid.uuid4(),
            tenant_id=t_id,
            test_run_id=test_run.id,
            evaluation_rule_id=spec["evaluation_rule_id"],
            rule_name=res_dict["rule_name"],
            rule_type=res_dict["rule_type"],
            status=res_dict["status"],
            score=res_dict["score"],
            weight=res_dict["weight"],
            evidence=res_dict["evidence"],
            explanation=res_dict["explanation"],
            evaluator_version=res_dict["evaluator_version"],
            formula_version=res_dict["formula_version"],
            created_at=now,
        )
        session.add(row)
        persisted_results.append(row)

    pass_threshold = 100.0
    if test_run.suite_id is not None:
        suite_row = (
            await session.execute(
                select(TestSuite).where(
                    TestSuite.id == test_run.suite_id,
                    TestSuite.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if suite_row is not None and suite_row.pass_policy == "min_score":
            pass_threshold = float(suite_row.min_pass_score)

    scorecard = compute_scorecard_summary(
        eval_dicts,
        total_rules_count=len(all_specs),
        pass_threshold=pass_threshold,
    )
    test_run.scorecard_summary = scorecard.model_dump()

    # Only update test_run.status based on evaluation if the runtime execution itself succeeded
    if test_run.status not in (
        TestRunStatusEnum.ERROR.value,
        TestRunStatusEnum.NOT_RUN.value,
        TestRunStatusEnum.CANCELLED.value,
    ):
        if scorecard.status == ScorecardStatus.EVALUATION_ERROR:
            test_run.status = TestRunStatusEnum.ERROR.value
            test_run.error_code = "EVALUATION_ERROR"
            test_run.error_message = scorecard.explanation
        elif scorecard.status == ScorecardStatus.FAILED_ASSERTION:
            test_run.status = TestRunStatusEnum.FAILED.value
        else:
            # Either PASSED or NO_ASSERTIONS (runtime completed cleanly)
            test_run.status = TestRunStatusEnum.PASSED.value

    test_run.updated_at = now
    await session.flush()

    await _audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="evaluation.run.completed",
        detail={
            "test_run_id": str(test_run.id),
            "scorecard_status": scorecard.status.value,
            "overall_score": scorecard.overall_score,
            "enabled_rules": scorecard.enabled_rules,
        },
    )
    return persisted_results, scorecard


async def list_evaluation_results_for_run(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    test_run_id: uuid.UUID | str,
) -> list[EvaluationResult]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    r_id = _ensure_uuid(test_run_id, "test_run_id")
    stmt = (
        select(EvaluationResult)
        .where(
            EvaluationResult.tenant_id == t_id,
            EvaluationResult.test_run_id == r_id,
        )
        .order_by(EvaluationResult.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())
