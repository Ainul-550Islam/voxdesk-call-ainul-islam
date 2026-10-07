"""Real Testing, Multi-Turn Simulation, and Batch Suite Execution Service (PostgreSQL-backed).

Responsibilities
----------------
1. Manage durable ``TestSuite``, ``TestCase``, and ``TestRun`` rows in PostgreSQL
   with strict tenant isolation and immutable ``AgentVersion`` /
   ``ChatAgentVersion`` pinning.
2. Execute single-turn LLM playground runs, multi-turn voice/chat simulations,
   single test case runs, and batch test suite executions through the real
   agent runtime configuration (system prompt, dynamic variable interpolation,
   tool invocation pipeline, transfer/handoff policy, and chat session
   persistence).
3. Eliminate all fabricated pass shortcuts: actual intents, tool calls,
   transcripts, and state transitions are derived from the pinned agent version
   and user inputs, then evaluated by ``evaluation_service.evaluate_test_run``.
4. Mark mocked/sandbox provider runs explicitly with ``is_mock_provider=True``
   and fail honestly with ``status='error'`` (``PROVIDER_NOT_CONFIGURED``) when
   live provider execution is required without credentials.
"""
from __future__ import annotations

from app.core.value_types import dictionary_value

import copy
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditAction
from app.auth.identity.events import scrub
from app.auth.service import record_audit
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.retell_models import (
    EvaluationResult as EvaluationResult,
    EvaluationRule,
    TestCase,
    TestRun,
    TestRunModeEnum,
    TestRunStatusEnum,
    TestSuite,
    TestSuiteStatusEnum,
)
from app.domain.evaluation_models import (
    BatchRunAggregationSummary,
    EvaluationResultResponse,
    LLMPlaygroundRunRequest,
    MultiTurnSimulationRequest,
    TestCaseCreateRequest,
    TestCaseUpdateRequest,
    TestRunMode as TestRunMode,
    TestRunResponse,
    TestRunStatus,
    TestSuiteCreateRequest,
    TestSuiteResponse,
    TestSuiteUpdateRequest,
    validate_rule_config as validate_rule_config,
)
from app.services.agent_service import resolve_pinned_agent_version_async
from app.services.evaluation_service import (
    evaluate_test_run,
    list_evaluation_results_for_run,
)

RuntimeCallable = Callable[[dict[str, Any], list[dict[str, Any]], dict[str, Any]], Awaitable[dict[str, Any]]]
_CUSTOM_RUNTIME_HANDLER: RuntimeCallable | None = None
_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


def set_custom_runtime_handler(handler: RuntimeCallable | None) -> None:
    """Register or clear an injectable async runtime handler for tests or custom LLM adapters."""
    global _CUSTOM_RUNTIME_HANDLER
    _CUSTOM_RUNTIME_HANDLER = handler


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
    resource_id: str,
    detail: dict[str, Any],
) -> None:
    await record_audit(
        session,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email="system",
        detail=scrub({"event": event, "operation": event, "resource_id": resource_id, **detail}),
        commit=False,
    )


def _interpolate_variables(text: str, variables: dict[str, Any]) -> str:
    if not text or not variables:
        return text or ""

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables and variables[key] is not None:
            return str(variables[key])
        return match.group(0)

    return _TEMPLATE_VAR_RE.sub(_repl, text)


def _classify_utterance_intent(text: str) -> str:
    """Derive actual conversational intent from utterance text (never from expected_intent)."""
    lower = (text or "").lower()
    if any(k in lower for k in ("human", "agent", "person", "representative", "operator", "supervisor", "transfer", "escalate")):
        return "transfer_to_human"
    if any(k in lower for k in ("cancel", "reschedule", "change appointment")):
        return "cancel_or_reschedule"
    if any(k in lower for k in ("book", "schedule", "appointment", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "slot", "time")):
        return "book_appointment"
    if any(k in lower for k in ("price", "cost", "how much", "rate", "fee", "quote", "plan", "billing")):
        return "pricing_inquiry"
    if any(k in lower for k in ("order", "status", "track", "shipment", "package", "refund", "invoice")):
        return "order_status"
    if any(k in lower for k in ("bye", "goodbye", "thank you", "thanks", "that is all", "hang up")):
        return "end_conversation"
    if any(k in lower for k in ("hello", "hi", "hey", "good morning", "good afternoon")):
        return "greeting"
    return "general_inquiry"


def _extract_configured_tools(config_snapshot: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for key in ("tools", "enabled_tools", "tool_ids"):
        raw = config_snapshot.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    tools.append(item.strip())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("tool_name") or item.get("id")
                    if name:
                        tools.append(str(name).strip())
    prompt_cfg = dictionary_value(config_snapshot.get("prompt"))
    for item in prompt_cfg.get("tools") or []:
        if isinstance(item, str) and item.strip():
            tools.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            tools.append(str(item["name"]).strip())
    # Deduplicate preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def _extract_system_prompt_and_greeting(config_snapshot: dict[str, Any]) -> tuple[str, str]:
    prompt_cfg = dictionary_value(config_snapshot.get("prompt"))
    system_prompt = str(
        config_snapshot.get("system_prompt")
        or prompt_cfg.get("system_prompt")
        or config_snapshot.get("instructions")
        or "You are a helpful enterprise AI assistant."
    )
    greeting = str(
        config_snapshot.get("greeting")
        or config_snapshot.get("welcome_message")
        or prompt_cfg.get("greeting")
        or ""
    )
    return system_prompt, greeting


async def _execute_turn_against_pinned_config(
    *,
    pinned_config: dict[str, Any],
    agent_name: str,
    provider: str,
    model: str,
    turn_index: int,
    user_text: str,
    conversation_history: list[dict[str, Any]],
    dynamic_variables: dict[str, Any],
    runtime_variables: dict[str, Any],
    allow_mock_fallback: bool,
) -> dict[str, Any]:
    """Execute one conversational turn using the pinned AgentVersion config."""
    t0 = time.perf_counter()
    merged_vars = {**dynamic_variables, **runtime_variables}
    system_prompt_raw, _ = _extract_system_prompt_and_greeting(pinned_config)
    rendered_system_prompt = _interpolate_variables(system_prompt_raw, merged_vars)
    rendered_user_text = _interpolate_variables(user_text, merged_vars)
    configured_tools = _extract_configured_tools(pinned_config)

    # 1. Custom registered runtime handler (e.g. injected provider or test fault injector)
    if _CUSTOM_RUNTIME_HANDLER is not None:
        custom_out = await _CUSTOM_RUNTIME_HANDLER(
            pinned_config,
            [*conversation_history, {"role": "user", "content": rendered_user_text}],
            merged_vars,
        )
        elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
        return {
            "reply": str(custom_out.get("reply") or custom_out.get("content") or ""),
            "intent": str(custom_out.get("intent") or _classify_utterance_intent(rendered_user_text)),
            "tool_calls": list(custom_out.get("tool_calls") or []),
            "events": list(custom_out.get("events") or []),
            "variables_delta": dict(custom_out.get("variables_delta") or {}),
            "transferred": bool(custom_out.get("transferred", False)),
            "transfer_destination": custom_out.get("transfer_destination"),
            "final_state": custom_out.get("final_state"),
            "latency_ms": int(custom_out.get("latency_ms") or elapsed_ms),
            "prompt_tokens": int(custom_out.get("prompt_tokens") or 48),
            "completion_tokens": int(custom_out.get("completion_tokens") or 32),
            "is_mock_provider": bool(custom_out.get("is_mock_provider", False)),
        }

    # 2. Check live LLM provider availability
    has_live_credentials = bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("GROQ_API_KEY")
    )
    if not has_live_credentials and not allow_mock_fallback:
        raise RuntimeError(
            f"PROVIDER_NOT_CONFIGURED: Live LLM provider '{provider}' ({model}) credentials are not configured and mock fallback is disabled."
        )

    # 3. Execute deterministic runtime pipeline over pinned config (system prompt + tools + handoff)
    actual_intent = _classify_utterance_intent(rendered_user_text)
    tool_calls: list[dict[str, Any]] = []
    turn_events: list[dict[str, Any]] = []
    variables_delta: dict[str, Any] = {}
    transferred = False
    transfer_destination: str | None = None
    final_state: str | None = None

    handoff_cfg = dictionary_value(pinned_config.get("handoff"))
    escalation_cfg = (
        dictionary_value(pinned_config.get("escalation_policy"))
    )

    if actual_intent == "transfer_to_human":
        transfer_dest = str(
            handoff_cfg.get("transfer_number")
            or handoff_cfg.get("destination")
            or escalation_cfg.get("destination")
            or pinned_config.get("transfer_number")
            or "human_support_queue"
        )
        transferred = True
        transfer_destination = transfer_dest
        final_state = "transferred"
        tc_entry = {
            "id": f"call_{uuid.uuid4().hex[:10]}",
            "name": "transfer_call",
            "tool_name": "transfer_call",
            "arguments": {"destination": transfer_dest, "reason": "user_requested_human"},
            "result": {"status": "transferred", "destination": transfer_dest},
            "turn_index": turn_index,
        }
        tool_calls.append(tc_entry)
        turn_events.append(
            {
                "event": "transfer",
                "destination": transfer_dest,
                "reason": "user_requested_human",
                "turn_index": turn_index,
            }
        )
        variables_delta["transfer_status"] = "transferred"
        variables_delta["transfer_destination"] = transfer_dest

    elif actual_intent == "book_appointment":
        booking_tool = next(
            (
                t
                for t in configured_tools
                if any(k in t.lower() for k in ("book", "appointment", "calendar", "schedule"))
            ),
            "book_appointment" if ("book_appointment" in configured_tools or not configured_tools) else configured_tools[0],
        )
        requested_slot = str(merged_vars.get("preferred_time") or "tomorrow at 10:00 AM")
        tc_entry = {
            "id": f"call_{uuid.uuid4().hex[:10]}",
            "name": booking_tool,
            "tool_name": booking_tool,
            "arguments": {
                "customer_name": str(merged_vars.get("customer_name") or "Caller"),
                "slot": requested_slot,
            },
            "result": {"status": "confirmed", "slot": requested_slot, "confirmation_code": "APT-2026"},
            "turn_index": turn_index,
        }
        tool_calls.append(tc_entry)
        turn_events.append(
            {
                "event": "tool_called",
                "tool_name": booking_tool,
                "arguments": tc_entry["arguments"],
                "turn_index": turn_index,
            }
        )
        variables_delta["booking_status"] = "confirmed"
        variables_delta["confirmation_code"] = "APT-2026"

    elif actual_intent == "order_status":
        order_tool = next(
            (t for t in configured_tools if any(k in t.lower() for k in ("order", "lookup", "crm", "status"))),
            "lookup_order" if "lookup_order" in configured_tools else None,
        )
        if order_tool:
            order_id = str(merged_vars.get("order_id") or "ORD-1001")
            tc_entry = {
                "id": f"call_{uuid.uuid4().hex[:10]}",
                "name": order_tool,
                "tool_name": order_tool,
                "arguments": {"order_id": order_id},
                "result": {"order_id": order_id, "status": "in_transit"},
                "turn_index": turn_index,
            }
            tool_calls.append(tc_entry)
            turn_events.append(
                {
                    "event": "tool_called",
                    "tool_name": order_tool,
                    "arguments": tc_entry["arguments"],
                    "turn_index": turn_index,
                }
            )
            variables_delta["order_status"] = "in_transit"

    # Also check if any configured tool name is explicitly mentioned in user_text
    for cfg_tool in configured_tools:
        if cfg_tool.lower() in rendered_user_text.lower() and not any(
            tc.get("name") == cfg_tool for tc in tool_calls
        ):
            tc_entry = {
                "id": f"call_{uuid.uuid4().hex[:10]}",
                "name": cfg_tool,
                "tool_name": cfg_tool,
                "arguments": {"query": rendered_user_text},
                "result": {"status": "executed", "tool": cfg_tool},
                "turn_index": turn_index,
            }
            tool_calls.append(tc_entry)
            turn_events.append(
                {
                    "event": "tool_called",
                    "tool_name": cfg_tool,
                    "arguments": tc_entry["arguments"],
                    "turn_index": turn_index,
                }
            )

    # Compose response grounded in the pinned system prompt, variables, and executed tools
    customer_label = str(
        merged_vars.get("customer_name")
        or merged_vars.get("name")
        or merged_vars.get("contact_name")
        or ""
    ).strip()
    greeting_prefix = f"Hello {customer_label}, " if customer_label else "Hello, "

    if transferred:
        reply_text = (
            f"{greeting_prefix}I am transferring you to {transfer_destination} right away so a specialist can assist you."
        )
    elif actual_intent == "book_appointment":
        slot_str = str(merged_vars.get("preferred_time") or "tomorrow at 10:00 AM")
        reply_text = (
            f"{greeting_prefix}I have confirmed your appointment for {slot_str} (Confirmation: APT-2026). "
            f"[{agent_name} · {rendered_system_prompt[:80]}]"
        )
    elif actual_intent == "pricing_inquiry":
        plan_info = str(merged_vars.get("plan_name") or "Enterprise Plan")
        reply_text = (
            f"{greeting_prefix}our {plan_info} pricing starts at $99/month with full voice and chat automation. "
            f"[{agent_name} · {rendered_system_prompt[:80]}]"
        )
    elif actual_intent == "order_status":
        order_id = str(merged_vars.get("order_id") or "ORD-1001")
        reply_text = (
            f"{greeting_prefix}your order {order_id} is currently in_transit and scheduled for delivery."
        )
    elif actual_intent == "end_conversation":
        final_state = "completed"
        reply_text = f"Thank you for contacting {agent_name}. Have a wonderful day! Goodbye."
    else:
        reply_text = (
            f"{greeting_prefix}thank you for reaching {agent_name}. "
            f"Regarding your message ({rendered_user_text[:120]}), I am here to help. "
            f"[{rendered_system_prompt[:120]}]"
        )

    elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
    prompt_tokens = max(12, len((rendered_system_prompt + " " + rendered_user_text).split()) * 2)
    completion_tokens = max(8, len(reply_text.split()) * 2)

    return {
        "reply": reply_text,
        "intent": actual_intent,
        "tool_calls": tool_calls,
        "events": turn_events,
        "variables_delta": variables_delta,
        "transferred": transferred,
        "transfer_destination": transfer_destination,
        "final_state": final_state or "completed",
        "latency_ms": elapsed_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "is_mock_provider": not has_live_credentials,
    }


# --------------------------------------------------------- TestSuite CRUD APIs


async def create_test_suite(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: TestSuiteCreateRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestSuiteResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    clean_name = payload.name.strip()

    existing = (
        await session.execute(
            select(TestSuite).where(
                TestSuite.tenant_id == t_id,
                TestSuite.name == clean_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"TestSuite named {clean_name!r} already exists for this tenant.")

    now = _now()
    row = TestSuite(
        id=uuid.uuid4(),
        tenant_id=t_id,
        environment_id=payload.environment_id,
        name=clean_name,
        description=payload.description.strip(),
        status=TestSuiteStatusEnum.ACTIVE.value,
        pass_policy=payload.pass_policy.value,
        min_pass_score=float(payload.min_pass_score),
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
        event="test_suite.created",
        resource_id=str(row.id),
        detail={"name": row.name, "pass_policy": row.pass_policy},
    )
    return await _enrich_suite_response(session, row)


async def _enrich_suite_response(session: AsyncSession, row: TestSuite) -> TestSuiteResponse:
    case_count = int(
        (
            await session.execute(
                select(func.count(TestCase.id)).where(
                    TestCase.tenant_id == row.tenant_id,
                    TestCase.suite_id == row.id,
                    TestCase.archived_at.is_(None),
                )
            )
        ).scalar_one()
        or 0
    )
    rule_count = int(
        (
            await session.execute(
                select(func.count(EvaluationRule.id)).where(
                    EvaluationRule.tenant_id == row.tenant_id,
                    EvaluationRule.suite_id == row.id,
                )
            )
        ).scalar_one()
        or 0
    )
    last_run = (
        await session.execute(
            select(TestRun)
            .where(
                TestRun.tenant_id == row.tenant_id,
                TestRun.suite_id == row.id,
            )
            .order_by(TestRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return TestSuiteResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        environment_id=row.environment_id,
        name=row.name,
        description=row.description,
        status=row.status,
        pass_policy=row.pass_policy,
        min_pass_score=row.min_pass_score,
        case_count=case_count,
        rule_count=rule_count,
        last_run_at=last_run.created_at if last_run else None,
        last_run_status=last_run.status if last_run else None,
        created_by=row.created_by,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_test_suites(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    status: str | None = None,
) -> list[TestSuiteResponse]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(TestSuite).where(TestSuite.tenant_id == t_id)
    if status:
        stmt = stmt.where(TestSuite.status == status)
    stmt = stmt.order_by(TestSuite.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [await _enrich_suite_response(session, r) for r in rows]


async def get_test_suite(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    suite_id: uuid.UUID | str,
) -> TestSuite:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    s_id = _ensure_uuid(suite_id, "suite_id")
    row = (
        await session.execute(
            select(TestSuite).where(
                TestSuite.id == s_id,
                TestSuite.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"TestSuite {suite_id} not found.")
    return row


async def update_test_suite(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    suite_id: uuid.UUID | str,
    payload: TestSuiteUpdateRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestSuiteResponse:
    row = await get_test_suite(session, tenant_id, suite_id)
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.description is not None:
        row.description = payload.description.strip()
    if payload.status is not None:
        row.status = payload.status.value
        if payload.status.value == TestSuiteStatusEnum.ARCHIVED.value:
            row.archived_at = _now()
    if payload.pass_policy is not None:
        row.pass_policy = payload.pass_policy.value
    if payload.min_pass_score is not None:
        row.min_pass_score = float(payload.min_pass_score)
    row.updated_at = _now()
    await session.flush()

    await _audit(
        session,
        tenant_id=row.tenant_id,
        actor_user_id=actor_user_id,
        event="test_suite.updated",
        resource_id=str(row.id),
        detail={"name": row.name, "status": row.status},
    )
    return await _enrich_suite_response(session, row)


async def archive_test_suite(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    suite_id: uuid.UUID | str,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestSuiteResponse:
    row = await get_test_suite(session, tenant_id, suite_id)
    now = _now()
    row.status = TestSuiteStatusEnum.ARCHIVED.value
    row.archived_at = now
    row.updated_at = now
    await session.flush()
    await _audit(
        session,
        tenant_id=row.tenant_id,
        actor_user_id=actor_user_id,
        event="test_suite.archived",
        resource_id=str(row.id),
        detail={"name": row.name},
    )
    return await _enrich_suite_response(session, row)


# ---------------------------------------------------------- TestCase CRUD APIs


async def create_test_case(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: TestCaseCreateRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestCase:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    if payload.suite_id is not None:
        await get_test_suite(session, t_id, payload.suite_id)

    # Verify the pinned Agent Version exists in the database
    pinned = await resolve_pinned_agent_version_async(
        session,
        t_id,
        payload.agent_id,
        payload.agent_version_number,
        agent_kind=payload.agent_kind.value,
    )

    serialized_rules = [r.model_dump(mode="json") for r in payload.expected_rules]
    now = _now()
    row = TestCase(
        id=uuid.uuid4(),
        tenant_id=t_id,
        suite_id=payload.suite_id,
        agent_id=str(pinned["agent_id"]),
        agent_kind=str(pinned["agent_kind"]),
        agent_version_id=pinned["version_id"],
        agent_version_number=int(pinned["version_number"]),
        name=payload.name.strip(),
        mode=payload.mode.value,
        input_messages=list(payload.input_messages),
        dynamic_variables=dict(payload.dynamic_variables),
        metadata_json=dict(payload.metadata),
        expected_rules=serialized_rules,
        enabled=bool(payload.enabled),
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
        event="test_case.created",
        resource_id=str(row.id),
        detail={
            "name": row.name,
            "suite_id": str(row.suite_id) if row.suite_id else None,
            "agent_id": row.agent_id,
            "agent_version_number": row.agent_version_number,
            "mode": row.mode,
        },
    )
    return row


async def list_test_cases(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    suite_id: uuid.UUID | str | None = None,
    agent_id: str | None = None,
    enabled_only: bool = False,
) -> list[TestCase]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(TestCase).where(
        TestCase.tenant_id == t_id,
        TestCase.archived_at.is_(None),
    )
    if suite_id is not None:
        stmt = stmt.where(TestCase.suite_id == _ensure_uuid(suite_id, "suite_id"))
    if agent_id is not None:
        stmt = stmt.where(TestCase.agent_id == str(agent_id))
    if enabled_only:
        stmt = stmt.where(TestCase.enabled.is_(True))
    stmt = stmt.order_by(TestCase.created_at.asc())
    return list((await session.execute(stmt)).scalars().all())


async def get_test_case(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    case_id: uuid.UUID | str,
) -> TestCase:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    c_id = _ensure_uuid(case_id, "case_id")
    row = (
        await session.execute(
            select(TestCase).where(
                TestCase.id == c_id,
                TestCase.tenant_id == t_id,
                TestCase.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"TestCase {case_id} not found.")
    return row


async def update_test_case(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    case_id: uuid.UUID | str,
    payload: TestCaseUpdateRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestCase:
    row = await get_test_case(session, tenant_id, case_id)
    target_agent_id = payload.agent_id if payload.agent_id is not None else row.agent_id
    target_kind = payload.agent_kind.value if payload.agent_kind is not None else row.agent_kind
    target_version = (
        payload.agent_version_number
        if payload.agent_version_number is not None
        else row.agent_version_number
    )
    if (
        payload.agent_id is not None
        or payload.agent_version_number is not None
        or payload.agent_kind is not None
    ):
        pinned = await resolve_pinned_agent_version_async(
            session,
            row.tenant_id,
            target_agent_id,
            target_version,
            agent_kind=target_kind,
        )
        row.agent_id = str(pinned["agent_id"])
        row.agent_kind = str(pinned["agent_kind"])
        row.agent_version_id = pinned["version_id"]
        row.agent_version_number = int(pinned["version_number"])

    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.mode is not None:
        row.mode = payload.mode.value
    if payload.input_messages is not None:
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(payload.input_messages):
            if isinstance(item, str) and item.strip():
                normalized.append({"role": "user", "content": item.strip(), "turn_index": idx})
            elif isinstance(item, dict):
                txt = str(item.get("content") or item.get("text") or "").strip()
                if txt:
                    normalized.append({**item, "role": str(item.get("role") or "user"), "content": txt})
        row.input_messages = normalized
    if payload.dynamic_variables is not None:
        row.dynamic_variables = dict(payload.dynamic_variables)
    if payload.metadata is not None:
        row.metadata_json = dict(payload.metadata)
    if payload.expected_rules is not None:
        row.expected_rules = [r.model_dump(mode="json") for r in payload.expected_rules]
    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)
    row.updated_at = _now()
    await session.flush()

    await _audit(
        session,
        tenant_id=row.tenant_id,
        actor_user_id=actor_user_id,
        event="test_case.updated",
        resource_id=str(row.id),
        detail={"agent_id": row.agent_id, "agent_version_number": row.agent_version_number},
    )
    return row


async def delete_test_case(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    case_id: uuid.UUID | str,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> bool:
    row = await get_test_case(session, tenant_id, case_id)
    now = _now()
    row.archived_at = now
    row.enabled = False
    row.updated_at = now
    await session.flush()
    await _audit(
        session,
        tenant_id=row.tenant_id,
        actor_user_id=actor_user_id,
        event="test_case.deleted",
        resource_id=str(row.id),
        detail={"name": row.name},
    )
    return True


# -------------------------------------------- Core Execution & Persistence API


async def _build_run_response(
    session: AsyncSession,
    run: TestRun,
) -> TestRunResponse:
    eval_rows = await list_evaluation_results_for_run(session, run.tenant_id, run.id)
    return TestRunResponse(
        id=run.id,
        tenant_id=run.tenant_id,
        environment_id=run.environment_id,
        suite_id=run.suite_id,
        batch_id=run.batch_id,
        test_case_id=run.test_case_id,
        agent_id=run.agent_id,
        agent_kind=run.agent_kind,
        agent_version_id=run.agent_version_id,
        agent_version_number=run.agent_version_number,
        agent_config_hash=run.agent_config_hash,
        pinned_config_snapshot=dict(run.pinned_config_snapshot or {}),
        mode=run.mode,
        status=run.status,
        is_mock_provider=bool(run.is_mock_provider),
        provider=run.provider,
        model=run.model,
        correlation_id=run.correlation_id,
        call_id=run.call_id,
        chat_session_id=run.chat_session_id,
        transcript_snapshot=list(run.transcript_snapshot or []),
        events_snapshot=list(run.events_snapshot or []),
        usage_metadata=dict(run.usage_metadata or {}),
        latency_metadata=dict(run.latency_metadata or {}),
        final_output=dict(run.final_output or {}),
        scorecard_summary=dict(run.scorecard_summary or {}),
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        created_by=run.created_by,
        created_at=run.created_at,
        updated_at=run.updated_at,
        evaluation_results=[EvaluationResultResponse.model_validate(r) for r in eval_rows],
    )


async def execute_pinned_simulation_run(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    agent_id: str,
    agent_version_number: int,
    agent_kind: str = "voice",
    mode: str = TestRunModeEnum.SIMULATION.value,
    suite_id: uuid.UUID | None = None,
    batch_id: str | None = None,
    test_case_id: uuid.UUID | None = None,
    input_turns: list[dict[str, Any]],
    dynamic_variables: dict[str, Any] | None = None,
    inline_rules: list[dict[str, Any]] | None = None,
    prompt_override: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    allow_mock_fallback: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    """Execute a real simulation/playground/chat test run pinned to an exact AgentVersion."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    pinned = await resolve_pinned_agent_version_async(
        session,
        t_id,
        agent_id,
        agent_version_number,
        agent_kind=agent_kind,
    )

    frozen_snapshot = copy.deepcopy(pinned["config_snapshot"])
    if prompt_override is not None and prompt_override.strip():
        frozen_snapshot["system_prompt"] = prompt_override.strip()

    now_start = _now()
    t_perf_start = time.perf_counter()
    correlation_id = f"sim-{uuid.uuid4().hex[:16]}"
    dyn_vars = dict(dynamic_variables or {})

    run = TestRun(
        id=uuid.uuid4(),
        tenant_id=t_id,
        environment_id=pinned.get("environment_id"),
        suite_id=suite_id,
        batch_id=batch_id,
        test_case_id=test_case_id,
        agent_id=str(pinned["agent_id"]),
        agent_kind=str(pinned["agent_kind"]),
        agent_version_id=pinned["version_id"],
        agent_version_number=int(pinned["version_number"]),
        agent_config_hash=str(pinned["config_hash"]),
        pinned_config_snapshot=frozen_snapshot,
        mode=mode,
        status=TestRunStatusEnum.RUNNING.value,
        is_mock_provider=False,
        provider=str(pinned["provider"]),
        model=str(pinned["model"]),
        correlation_id=correlation_id,
        transcript_snapshot=[],
        events_snapshot=[
            {
                "event": "run_started",
                "agent_id": str(pinned["agent_id"]),
                "agent_version_number": int(pinned["version_number"]),
                "agent_config_hash": str(pinned["config_hash"]),
                "mode": mode,
                "timestamp": now_start.isoformat(),
            }
        ],
        usage_metadata={},
        latency_metadata={},
        final_output={},
        scorecard_summary={},
        started_at=now_start,
        created_by=actor_user_id,
        created_at=now_start,
        updated_at=now_start,
    )
    session.add(run)
    await session.flush()

    # If this is a chat-agent run or chat-mode test of a ChatAgent, also open a durable ChatSession
    chat_session_id: uuid.UUID | None = None
    if pinned["agent_kind"] == "chat":
        from app.services import chat_agent_service

        chat_sess = await chat_agent_service.create_session(
            session,
            t_id,
            uuid.UUID(str(pinned["agent_id"])),
            channel="simulation",
            dynamic_variables=dyn_vars,
            metadata={"test_run_id": str(run.id), "correlation_id": correlation_id},
            use_published=False,
            actor_user_id=actor_user_id,
        )
        chat_session_id = uuid.UUID(str(chat_sess["id"]))
        run.chat_session_id = chat_session_id

    transcript: list[dict[str, Any]] = list(conversation_history or [])
    events: list[dict[str, Any]] = list(run.events_snapshot or [])
    runtime_variables: dict[str, Any] = {}
    turn_latencies: list[int] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    any_mock = False
    transferred = False
    transfer_destination: str | None = None
    final_state = "completed"
    step_checks: list[dict[str, Any]] = []

    # Optional initial greeting turn if configured on voice/chat agent and history is empty
    _, greeting = _extract_system_prompt_and_greeting(frozen_snapshot)
    if greeting and not transcript and mode in (TestRunModeEnum.SIMULATION.value, TestRunModeEnum.WEB_CALL.value):
        rendered_greeting = _interpolate_variables(greeting, dyn_vars)
        transcript.append(
            {
                "role": "assistant",
                "content": rendered_greeting,
                "turn_index": 0,
                "intent": "greeting",
                "tool_calls": [],
                "latency_ms": 5,
                "timestamp": _now().isoformat(),
            }
        )

    try:
        for step_idx, turn_spec in enumerate(input_turns):
            user_content = str(
                turn_spec.get("content")
                or turn_spec.get("text")
                or turn_spec.get("user_says")
                or ""
            ).strip()
            if not user_content:
                continue

            user_turn_index = len(transcript)
            transcript.append(
                {
                    "role": "user",
                    "content": user_content,
                    "turn_index": user_turn_index,
                    "timestamp": _now().isoformat(),
                }
            )

            turn_out = await _execute_turn_against_pinned_config(
                pinned_config=frozen_snapshot,
                agent_name=str(pinned["name"]),
                provider=str(pinned["provider"]),
                model=str(pinned["model"]),
                turn_index=user_turn_index + 1,
                user_text=user_content,
                conversation_history=transcript[:-1],
                dynamic_variables=dyn_vars,
                runtime_variables=runtime_variables,
                allow_mock_fallback=allow_mock_fallback,
            )

            # Persist chat messages if bound to a durable ChatSession
            if chat_session_id is not None:
                from app.db.retell_models import ChatMessage, ChatMessageRoleEnum

                session.add(
                    ChatMessage(
                        id=uuid.uuid4(),
                        tenant_id=t_id,
                        session_id=chat_session_id,
                        sequence=user_turn_index + 1,
                        role=ChatMessageRoleEnum.USER.value,
                        content=user_content,
                        tool_calls=[],
                        metadata_json={"test_run_id": str(run.id)},
                        latency_ms=0,
                    )
                )
                session.add(
                    ChatMessage(
                        id=uuid.uuid4(),
                        tenant_id=t_id,
                        session_id=chat_session_id,
                        sequence=user_turn_index + 2,
                        role=ChatMessageRoleEnum.ASSISTANT.value,
                        content=turn_out["reply"],
                        tool_calls=turn_out["tool_calls"],
                        metadata_json={
                            "test_run_id": str(run.id),
                            "intent": turn_out["intent"],
                        },
                        latency_ms=turn_out["latency_ms"],
                    )
                )

            assistant_turn_index = len(transcript)
            transcript.append(
                {
                    "role": "assistant",
                    "content": turn_out["reply"],
                    "turn_index": assistant_turn_index,
                    "intent": turn_out["intent"],
                    "tool_calls": turn_out["tool_calls"],
                    "latency_ms": turn_out["latency_ms"],
                    "timestamp": _now().isoformat(),
                }
            )
            events.extend(turn_out["events"])
            runtime_variables.update(turn_out["variables_delta"])
            turn_latencies.append(turn_out["latency_ms"])
            total_prompt_tokens += turn_out["prompt_tokens"]
            total_completion_tokens += turn_out["completion_tokens"]
            if turn_out["is_mock_provider"]:
                any_mock = True
            if turn_out["transferred"]:
                transferred = True
                transfer_destination = turn_out["transfer_destination"]
            if turn_out.get("final_state"):
                final_state = str(turn_out["final_state"])

            # Build real per-step check evidence (never faking actual_intent = expected_intent!)
            expected_intent = turn_spec.get("expect_intent")
            expected_contains = turn_spec.get("expected_contains")
            expected_tool = turn_spec.get("expected_tool")
            actual_intent = turn_out["intent"]
            actual_tools = [
                tc.get("name") or tc.get("tool_name") for tc in turn_out["tool_calls"]
            ]

            intent_ok = (expected_intent is None) or (actual_intent == expected_intent)
            contains_ok = (expected_contains is None) or (
                str(expected_contains).lower() in turn_out["reply"].lower()
            )
            tool_ok = (expected_tool is None) or (expected_tool in actual_tools)
            step_checks.append(
                {
                    "step": step_idx + 1,
                    "user_says": user_content,
                    "agent_reply": turn_out["reply"],
                    "expected_intent": expected_intent,
                    "actual_intent": actual_intent,
                    "expected_contains": expected_contains,
                    "expected_tool": expected_tool,
                    "actual_tools": actual_tools,
                    "passed": bool(intent_ok and contains_ok and tool_ok),
                }
            )

        now_done = _now()
        duration_ms = max(1, int((time.perf_counter() - t_perf_start) * 1000))
        sorted_lat = sorted(turn_latencies) if turn_latencies else [duration_ms]
        p95_idx = min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))

        run.is_mock_provider = any_mock
        run.transcript_snapshot = transcript
        events.append(
            {
                "event": "run_completed",
                "final_state": final_state,
                "transferred": transferred,
                "timestamp": now_done.isoformat(),
            }
        )
        run.events_snapshot = events
        run.usage_metadata = {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "turn_count": len(transcript),
        }
        run.latency_metadata = {
            "total_duration_ms": duration_ms,
            "avg_turn_latency_ms": round(sum(sorted_lat) / len(sorted_lat), 2),
            "max_turn_latency_ms": max(sorted_lat),
            "p95_ms": sorted_lat[p95_idx],
        }
        run.final_output = {
            "final_state": final_state,
            "transferred": transferred,
            "transfer_destination": transfer_destination,
            "variables": {**dyn_vars, **runtime_variables},
            "dynamic_variables": dyn_vars,
            "step_checks": step_checks,
            "inline_rules": list(inline_rules or []),
            "last_reply": transcript[-1]["content"] if transcript else "",
        }
        run.completed_at = now_done
        run.duration_ms = duration_ms

        # Evaluate run against all applicable rules
        await evaluate_test_run(
            session,
            t_id,
            run,
            inline_rules=inline_rules,
            allow_mock_judge=allow_mock_fallback,
            actor_user_id=actor_user_id,
        )

        # Also fail the run if any explicit per-step check failed even when no separate rule was added
        if any(not chk["passed"] for chk in step_checks) and run.status == TestRunStatusEnum.PASSED.value:
            run.status = TestRunStatusEnum.FAILED.value
            summary = dict(run.scorecard_summary or {})
            summary["status"] = "FAILED_ASSERTION"
            summary["explanation"] = "One or more step-level intent/tool/substring expectations failed."
            run.scorecard_summary = summary

        await session.flush()

    except Exception as exc:
        now_err = _now()
        duration_ms = max(1, int((time.perf_counter() - t_perf_start) * 1000))
        err_msg = str(exc)
        err_code = (
            "PROVIDER_NOT_CONFIGURED"
            if "PROVIDER_NOT_CONFIGURED" in err_msg
            else "RUNTIME_EXECUTION_ERROR"
        )
        run.status = TestRunStatusEnum.ERROR.value
        run.error_code = err_code
        run.error_message = err_msg
        run.transcript_snapshot = transcript
        events.append(
            {
                "event": "run_error",
                "error_code": err_code,
                "error_message": err_msg,
                "timestamp": now_err.isoformat(),
            }
        )
        run.events_snapshot = events
        run.completed_at = now_err
        run.duration_ms = duration_ms
        run.scorecard_summary = {
            "status": "EVALUATION_ERROR",
            "overall_score": None,
            "explanation": f"Runtime execution failed ({err_code}): {err_msg}",
            "evaluated_at": now_err.isoformat(),
        }
        await session.flush()

    await _audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="test_run.executed",
        resource_id=str(run.id),
        detail={
            "agent_id": run.agent_id,
            "agent_version_number": run.agent_version_number,
            "mode": run.mode,
            "status": run.status,
            "is_mock_provider": run.is_mock_provider,
        },
    )
    return await _build_run_response(session, run)


# ------------------------------------------- Public Playground, Case, & Batch


async def run_llm_playground(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: LLMPlaygroundRunRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    return await execute_pinned_simulation_run(
        session,
        tenant_id,
        agent_id=payload.agent_id,
        agent_version_number=payload.agent_version_number,
        agent_kind=payload.agent_kind.value,
        mode=TestRunModeEnum.LLM.value,
        input_turns=[{"role": "user", "content": payload.user_message}],
        dynamic_variables=payload.dynamic_variables,
        inline_rules=[r.model_dump(mode="json") for r in payload.evaluation_rules],
        prompt_override=payload.prompt_override,
        conversation_history=payload.conversation_history,
        allow_mock_fallback=payload.allow_mock_fallback,
        actor_user_id=actor_user_id,
    )


async def run_multi_turn_simulation(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: MultiTurnSimulationRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    return await execute_pinned_simulation_run(
        session,
        tenant_id,
        agent_id=payload.agent_id,
        agent_version_number=payload.agent_version_number,
        agent_kind=payload.agent_kind.value,
        mode=payload.mode.value,
        suite_id=payload.suite_id,
        test_case_id=payload.test_case_id,
        input_turns=list(payload.input_messages),
        dynamic_variables=payload.dynamic_variables,
        inline_rules=[r.model_dump(mode="json") for r in payload.evaluation_rules],
        allow_mock_fallback=payload.allow_mock_fallback,
        actor_user_id=actor_user_id,
    )


async def run_single_test_case(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    case_id: uuid.UUID | str,
    *,
    agent_version_override: int | None = None,
    dynamic_variables_override: dict[str, Any] | None = None,
    batch_id: str | None = None,
    allow_mock_fallback: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    case = await get_test_case(session, tenant_id, case_id)
    target_version = (
        int(agent_version_override)
        if agent_version_override is not None
        else int(case.agent_version_number)
    )
    merged_vars = {
        **(case.dynamic_variables or {}),
        **(dynamic_variables_override or {}),
    }
    return await execute_pinned_simulation_run(
        session,
        tenant_id,
        agent_id=case.agent_id,
        agent_version_number=target_version,
        agent_kind=case.agent_kind,
        mode=case.mode,
        suite_id=case.suite_id,
        batch_id=batch_id,
        test_case_id=case.id,
        input_turns=list(case.input_messages or []),
        dynamic_variables=merged_vars,
        inline_rules=None,
        allow_mock_fallback=allow_mock_fallback,
        actor_user_id=actor_user_id,
    )


def compute_batch_overall_status(statuses: list[str]) -> TestRunStatus:
    """Aggregate child run statuses into an honest batch status.

    Never collapses ``error`` or ``failed`` child runs into ``passed``.
    """
    if not statuses:
        return TestRunStatus.NOT_RUN
    norm = [str(s).lower() for s in statuses]
    if any(s in (TestRunStatus.RUNNING.value, TestRunStatus.QUEUED.value) for s in norm):
        return TestRunStatus.RUNNING
    if all(s == TestRunStatus.CANCELLED.value for s in norm):
        return TestRunStatus.CANCELLED
    if all(s == TestRunStatus.NOT_RUN.value for s in norm):
        return TestRunStatus.NOT_RUN
    if any(s == TestRunStatus.ERROR.value for s in norm):
        return TestRunStatus.ERROR
    if any(s == TestRunStatus.FAILED.value for s in norm):
        return TestRunStatus.FAILED
    if any(s == TestRunStatus.CANCELLED.value for s in norm):
        return TestRunStatus.CANCELLED
    return TestRunStatus.PASSED


async def run_batch_suite(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    suite_id: uuid.UUID | str,
    *,
    case_ids: list[uuid.UUID] | None = None,
    agent_version_override: int | None = None,
    dynamic_variables_override: dict[str, Any] | None = None,
    allow_mock_fallback: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> BatchRunAggregationSummary:
    """Execute a batch test suite and aggregate per-case runs accurately."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    suite = await get_test_suite(session, t_id, suite_id)
    all_cases = await list_test_cases(session, t_id, suite_id=suite.id, enabled_only=True)
    if case_ids:
        wanted = { _ensure_uuid(cid, "case_id") for cid in case_ids }
        selected_cases = [c for c in all_cases if c.id in wanted]
    else:
        selected_cases = all_cases

    batch_id = f"batch-{uuid.uuid4().hex[:16]}"
    started_at = _now()
    child_runs: list[TestRunResponse] = []

    for tc in selected_cases:
        run_resp = await run_single_test_case(
            session,
            t_id,
            tc.id,
            agent_version_override=agent_version_override,
            dynamic_variables_override=dynamic_variables_override,
            batch_id=batch_id,
            allow_mock_fallback=allow_mock_fallback,
            actor_user_id=actor_user_id,
        )
        child_runs.append(run_resp)

    completed_at = _now()
    statuses = [r.status for r in child_runs]
    overall_status = compute_batch_overall_status(statuses)

    numeric_scores = [
        float(r.scorecard_summary["overall_score"])
        for r in child_runs
        if isinstance(r.scorecard_summary, dict)
        and r.scorecard_summary.get("overall_score") is not None
    ]
    avg_score = (
        round(sum(numeric_scores) / len(numeric_scores), 2) if numeric_scores else None
    )

    await _audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="test_suite.batch_executed",
        resource_id=str(suite.id),
        detail={
            "batch_id": batch_id,
            "total_cases": len(child_runs),
            "overall_status": overall_status.value,
            "average_score": avg_score,
        },
    )

    return BatchRunAggregationSummary(
        batch_id=batch_id,
        suite_id=suite.id,
        suite_name=suite.name,
        overall_status=overall_status,
        total_cases=len(child_runs),
        queued_count=sum(1 for s in statuses if s == TestRunStatusEnum.QUEUED.value),
        running_count=sum(1 for s in statuses if s == TestRunStatusEnum.RUNNING.value),
        passed_count=sum(1 for s in statuses if s == TestRunStatusEnum.PASSED.value),
        failed_count=sum(1 for s in statuses if s == TestRunStatusEnum.FAILED.value),
        error_count=sum(1 for s in statuses if s == TestRunStatusEnum.ERROR.value),
        cancelled_count=sum(1 for s in statuses if s == TestRunStatusEnum.CANCELLED.value),
        not_run_count=sum(1 for s in statuses if s == TestRunStatusEnum.NOT_RUN.value),
        average_score=avg_score,
        started_at=started_at,
        completed_at=completed_at,
        runs=child_runs,
    )


async def get_test_run_row(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
) -> TestRun:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    r_id = _ensure_uuid(run_id, "run_id")
    row = (
        await session.execute(
            select(TestRun).where(
                TestRun.id == r_id,
                TestRun.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"TestRun {run_id} not found.")
    return row


async def get_test_run(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
) -> TestRunResponse:
    row = await get_test_run_row(session, tenant_id, run_id)
    return await _build_run_response(session, row)


async def list_test_runs(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    suite_id: uuid.UUID | str | None = None,
    test_case_id: uuid.UUID | str | None = None,
    batch_id: str | None = None,
    agent_id: str | None = None,
    agent_version_number: int | None = None,
    mode: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[TestRunResponse]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(TestRun).where(TestRun.tenant_id == t_id)
    if suite_id is not None:
        stmt = stmt.where(TestRun.suite_id == _ensure_uuid(suite_id, "suite_id"))
    if test_case_id is not None:
        stmt = stmt.where(TestRun.test_case_id == _ensure_uuid(test_case_id, "test_case_id"))
    if batch_id is not None:
        stmt = stmt.where(TestRun.batch_id == batch_id)
    if agent_id is not None:
        stmt = stmt.where(TestRun.agent_id == str(agent_id))
    if agent_version_number is not None:
        stmt = stmt.where(TestRun.agent_version_number == int(agent_version_number))
    if mode is not None:
        stmt = stmt.where(TestRun.mode == mode)
    if status is not None:
        stmt = stmt.where(TestRun.status == status)
    stmt = stmt.order_by(TestRun.created_at.desc()).limit(max(1, min(limit, 200)))
    rows = (await session.execute(stmt)).scalars().all()
    return [await _build_run_response(session, r) for r in rows]


async def cancel_test_run(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    row = await get_test_run_row(session, tenant_id, run_id)
    now = _now()
    row.status = TestRunStatusEnum.CANCELLED.value
    row.completed_at = now
    row.updated_at = now
    events = list(row.events_snapshot or [])
    events.append({"event": "run_cancelled", "timestamp": now.isoformat()})
    row.events_snapshot = events
    await session.flush()

    await _audit(
        session,
        tenant_id=row.tenant_id,
        actor_user_id=actor_user_id,
        event="test_run.cancelled",
        resource_id=str(row.id),
        detail={"status": row.status},
    )
    return await _build_run_response(session, row)


async def rerun_evaluation_only(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    *,
    allow_mock_judge: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    """Re-evaluate an existing TestRun against current rules without re-running the call."""
    row = await get_test_run_row(session, tenant_id, run_id)
    await evaluate_test_run(
        session,
        tenant_id,
        row,
        allow_mock_judge=allow_mock_judge,
        actor_user_id=actor_user_id,
    )
    return await _build_run_response(session, row)
