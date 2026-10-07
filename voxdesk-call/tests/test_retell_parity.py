"""Comprehensive tests for Retell-parity domain models, validators, state machines, and services."""

from __future__ import annotations

import pytest

from app.domain.chat_agent_models import (
    ChatAgentCreate,
    ChatAgentPublishRequest,
    ChatAgentRollbackRequest,
    ChatAgentStatus,
    ChatAgentUpdate,
    ChatMessageCreate,
    ChatSessionCreate,
    compute_chat_agent_etag,
    compute_chat_config_hash,
    validate_agent_config_dict,
)
from app.domain.contact_memory_models import (
    MemorySaveRequest,
    MemorySource,
    MemoryValueType,
    validate_memory_key,
)
from app.domain.contact_models import (
    ContactCreate,
    ContactLifecycle,
    ContactSource,
    ContactUpdate,
    normalize_phone,
    validate_custom_fields,
)
from app.domain.dynamic_variable_models import (
    DynamicVariableCreate,
    DynamicVariableType,
    DynamicVariableUpdate,
    validate_runtime_value,
    validate_variable_name,
)
from app.domain.transfer_state_machine import (
    TERMINAL_TRANSFER_STATES,
    TransferMode,
    TransferState,
    can_transition,
    require_transition,
)
from app.services import chat_agent_service, contact_memory_service, contact_service
from app.services.chat_agent_service import ChatAgentConflictError
from tests.conftest import make_tenant


def test_transfer_state_machine_transitions() -> None:
    assert can_transition(TransferState.PENDING, TransferState.IN_PROGRESS) is True
    assert can_transition(TransferState.IN_PROGRESS, TransferState.COMPLETED) is True
    assert can_transition(TransferState.IN_PROGRESS, TransferState.FAILED) is True
    assert can_transition(TransferState.COMPLETED, TransferState.IN_PROGRESS) is False
    assert can_transition(TransferState.CANCELLED, TransferState.PENDING) is False

    for term in TERMINAL_TRANSFER_STATES:
        assert can_transition(term, TransferState.IN_PROGRESS) is False

    assert require_transition("pending", "in_progress") == TransferState.IN_PROGRESS
    assert TransferMode.AGENT_TO_AGENT.value == "agent_to_agent"

    with pytest.raises(ValueError, match="illegal transfer state transition"):
        require_transition("completed", "in_progress")


def test_contact_normalization_and_validation() -> None:
    assert normalize_phone("415-555-0199") == "+14155550199"
    assert normalize_phone("+8801711223344") == "+8801711223344"

    contact = ContactCreate(
        phone="+14155550199",
        name="Alice Rahman",
        email="alice@example.com",
        company="Acme Telecom",
        custom_fields={"plan": "enterprise", "region": "APAC"},
        source=ContactSource.CRM,
    )
    assert contact.name == "Alice Rahman"
    assert contact.source == ContactSource.CRM

    patch = ContactUpdate(company="Acme Global", lifecycle=ContactLifecycle.ACTIVE)
    assert patch.lifecycle == ContactLifecycle.ACTIVE

    with pytest.raises(ValueError, match="credential or sensitive key"):
        validate_custom_fields({"stripe_secret_key": "sk_live_123"})


def test_contact_memory_hygiene_and_models() -> None:
    assert validate_memory_key("Preferred_Language") == "preferred_language"
    with pytest.raises(ValueError, match="refusing credential or sensitive memory key"):
        validate_memory_key("customer_password")

    mem = MemorySaveRequest(
        value="Prefers Bengali language support on billing calls",
        value_type=MemoryValueType.STRING,
        source=MemorySource.AGENT,
        confidence=0.96,
        importance=85,
    )
    assert mem.confidence == 0.96
    assert mem.importance == 85


def test_chat_agent_models_and_secret_rejection() -> None:
    agent = ChatAgentCreate(
        name="Omnichannel Concierge",
        description="Handles web and WhatsApp billing inquiries",
        draft_config={"model": "gpt-4o-mini", "temperature": 0.2},
    )
    assert agent.name == "Omnichannel Concierge"
    assert ChatAgentStatus.DRAFT.value == "draft"

    upd = ChatAgentUpdate(description="Updated prompt", draft_config={"temperature": 0.1})
    assert upd.draft_config == {"temperature": 0.1}

    pub = ChatAgentPublishRequest(change_summary="Initial production release")
    assert pub.change_summary == "Initial production release"

    rb = ChatAgentRollbackRequest(target_version=1, reason="Revert prompt")
    assert rb.target_version == 1

    etag1 = compute_chat_agent_etag({"temperature": 0.1}, 1)
    etag2 = compute_chat_agent_etag({"temperature": 0.2}, 1)
    assert etag1.startswith('W/"')
    assert etag1 != etag2
    assert len(compute_chat_config_hash({"temperature": 0.1})) == 16

    with pytest.raises(ValueError, match="secret-shaped configuration key"):
        validate_agent_config_dict({"openai_api_key": "sk-12345"})


def test_dynamic_variable_validation_and_runtime_checking() -> None:
    assert validate_variable_name("customer_tier") == "customer_tier"
    with pytest.raises(ValueError, match="refusing credential-shaped variable name"):
        validate_variable_name("api_key_override")

    defn = DynamicVariableCreate(
        name="customer_tier",
        var_type=DynamicVariableType.ENUM,
        default_value={"value": "standard"},
        constraints={"choices": ["standard", "gold", "platinum"]},
        is_required=True,
    )
    assert defn.name == "customer_tier"

    upd = DynamicVariableUpdate(description="Updated tier choices", is_required=False)
    assert upd.is_required is False

    assert (
        validate_runtime_value(
            DynamicVariableType.ENUM,
            "platinum",
            constraints={"choices": ["standard", "gold", "platinum"]},
        )
        == "platinum"
    )
    with pytest.raises(ValueError, match="not in allowed enum choices"):
        validate_runtime_value(
            DynamicVariableType.ENUM,
            "diamond",
            constraints={"choices": ["standard", "gold", "platinum"]},
        )
    assert validate_runtime_value(DynamicVariableType.NUMBER, 42, constraints={"min": 0, "max": 100}) == 42


@pytest.mark.asyncio
async def test_chat_agent_service_etag_publish_rollback_and_session(db) -> None:
    tenant = await make_tenant(db, name="Parity Service Tenant")
    await db.commit()

    agent = await chat_agent_service.create(
        db,
        tenant,
        ChatAgentCreate(
            name="Support Bot",
            description="Primary chat concierge",
            draft_config={
                "system_prompt": "You are Support Bot v1.",
                "first_message": "Welcome to Support Bot!",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
            },
        ),
    )
    await db.commit()

    initial_etag = compute_chat_agent_etag(agent.draft_config, agent.draft_version)
    with pytest.raises(ChatAgentConflictError):
        await chat_agent_service.update_draft(
            db,
            tenant,
            agent.id,
            ChatAgentUpdate(description="Should fail"),
            if_match='W/"stale-etag"',
        )

    updated = await chat_agent_service.update_draft(
        db,
        tenant,
        agent.id,
        ChatAgentUpdate(
            draft_config={
                "system_prompt": "You are Support Bot v1 updated.",
                "first_message": "Welcome to Support Bot!",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
            }
        ),
        if_match=initial_etag,
    )
    assert updated is not None
    assert updated.draft_version == 2

    _, v1 = await chat_agent_service.publish(
        db, tenant, agent.id, ChatAgentPublishRequest(change_summary="v1 release")
    )
    assert v1.version == 1

    await chat_agent_service.update_draft(
        db,
        tenant,
        agent.id,
        ChatAgentUpdate(
            draft_config={
                "system_prompt": "You are Support Bot v2.",
                "first_message": "Hello from v2!",
                "model": "gpt-4o",
                "temperature": 0.4,
            }
        ),
    )
    _, v2 = await chat_agent_service.publish(
        db, tenant, agent.id, ChatAgentPublishRequest(change_summary="v2 release")
    )
    assert v2.version == 2

    # Rollback to v1 mints v3 while keeping v1 immutable
    rolled_agent, v3 = await chat_agent_service.rollback_to_version(
        db, tenant, agent.id, 1, reason="Revert to v1"
    )
    assert v3.version == 3
    assert rolled_agent.published_version == 3
    assert rolled_agent.published_config["system_prompt"] == "You are Support Bot v1 updated."

    v1_reload = await chat_agent_service.get_version(db, tenant, agent.id, 1)
    assert v1_reload is not None
    assert v1_reload.config["system_prompt"] == "You are Support Bot v1 updated."

    # Start chat session with contact + memory
    chat_sess = await chat_agent_service.create_session(
        db,
        tenant,
        agent.id,
        ChatSessionCreate(
            contact_phone="+14155550177",
            contact_name="Rahim Uddin",
            channel="web",
            dynamic_variables={"tier": "enterprise"},
        ),
    )
    assert chat_sess.message_count == 1  # Initial greeting message

    turn = await chat_agent_service.send_message(
        db,
        tenant,
        chat_sess.id,
        ChatMessageCreate(
            content="Please check my invoice [remember billing_cycle=annual]",
            memory_updates={"preferred_language": "Bengali"},
        ),
    )
    assert turn.user_message.sequence == 2
    assert turn.assistant_message.sequence == 3
    assert "billing_cycle" in turn.memory_keys_saved
    assert "preferred_language" in turn.memory_keys_saved
    assert "Bengali" in turn.assistant_message.content

    contact = await contact_service.get_by_phone(db, tenant, "+14155550177")
    assert contact is not None
    memories = await contact_memory_service.list_entries(db, tenant, contact)
    assert {m.key for m in memories} == {"billing_cycle", "preferred_language"}


# ------------------------------------ Prompt 3: Evaluation & Simulation Tests


def test_bounded_regex_and_json_path_validation() -> None:
    from app.domain.evaluation_models import (
        validate_rule_config,
        validate_safe_json_path,
        validate_safe_regex_pattern,
    )

    assert validate_safe_regex_pattern(r"APT-\d{4}") == r"APT-\d{4}"
    with pytest.raises(ValueError):
        validate_safe_regex_pattern("(a+)+")

    assert validate_safe_json_path("variables.booking_status") == "$.variables.booking_status"
    assert validate_safe_json_path("$.final_output.transferred") == "$.final_output.transferred"
    with pytest.raises(ValueError):
        validate_safe_json_path("$.foo[?(@.bar==1)]")

    cfg = validate_rule_config("contains", {"substring": "APT-2026"})
    assert cfg["substring"] == "APT-2026"
    assert cfg["target"] == "assistant_transcript"


@pytest.mark.asyncio
async def test_all_deterministic_evaluators_and_zero_assertion_scorecard() -> None:
    from app.domain.evaluation_models import ScorecardStatus, TestRunStatus
    from app.services.evaluation_service import (
        compute_scorecard_summary,
        evaluate_single_rule,
    )
    from app.services.simulation_service import compute_batch_overall_status

    # 1. Zero enabled rules -> NO_ASSERTIONS, overall_score=None
    empty_card = compute_scorecard_summary([])
    assert empty_card.status == ScorecardStatus.NO_ASSERTIONS
    assert empty_card.overall_score is None

    transcript = [
        {"role": "user", "content": "Book me tomorrow at 10 AM", "turn_index": 0},
        {
            "role": "assistant",
            "content": "Confirmed your appointment for tomorrow at 10:00 AM (APT-2026).",
            "turn_index": 1,
            "latency_ms": 110,
            "tool_calls": [
                {
                    "name": "book_appointment",
                    "arguments": {"slot": "tomorrow at 10:00 AM"},
                    "result": {"status": "confirmed"},
                    "turn_index": 1,
                }
            ],
        },
    ]
    events = [{"event": "tool_called", "tool_name": "book_appointment", "turn_index": 1}]
    usage = {"total_tokens": 96, "turn_count": 2}
    latency = {"p95_ms": 110, "max_turn_latency_ms": 110}
    final_output = {
        "final_state": "completed",
        "transferred": False,
        "variables": {"booking_status": "confirmed", "confirmation_code": "APT-2026"},
    }

    r_contains = await evaluate_single_rule(
        rule_name="Has code",
        rule_type="contains",
        config={"substring": "APT-2026"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_contains["status"] == "passed"
    assert 1 in r_contains["evidence"]["matched_turn_indices"]

    r_not_contains = await evaluate_single_rule(
        rule_name="No error text",
        rule_type="not_contains",
        config={"substring": "INTERNAL_ERROR"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_not_contains["status"] == "passed"

    r_regex = await evaluate_single_rule(
        rule_name="Code pattern",
        rule_type="regex",
        config={"pattern": r"APT-\d{4}"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_regex["status"] == "passed"

    r_jpath_eq = await evaluate_single_rule(
        rule_name="Booking confirmed",
        rule_type="json_path_equals",
        config={"path": "$.variables.booking_status", "expected": "confirmed"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_jpath_eq["status"] == "passed"

    r_tool = await evaluate_single_rule(
        rule_name="Booking tool called",
        rule_type="tool_called",
        config={"tool_name": "book_appointment"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_tool["status"] == "passed"

    r_var = await evaluate_single_rule(
        rule_name="Variable match",
        rule_type="variable_equals",
        config={"variable_name": "confirmation_code", "expected": "APT-2026"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_var["status"] == "passed"

    r_lat = await evaluate_single_rule(
        rule_name="SLA 500ms",
        rule_type="latency_ms_max",
        config={"max_ms": 500},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_lat["status"] == "passed"

    r_state = await evaluate_single_rule(
        rule_name="Completed state",
        rule_type="final_state_equals",
        config={"expected_state": "completed"},
        transcript=transcript,
        events=events,
        usage_metadata=usage,
        latency_metadata=latency,
        final_output=final_output,
    )
    assert r_state["status"] == "passed"

    card = compute_scorecard_summary(
        [r_contains, r_not_contains, r_regex, r_jpath_eq, r_tool, r_var, r_lat, r_state]
    )
    assert card.status == ScorecardStatus.PASSED
    assert card.overall_score == 100.0

    # Batch status aggregation honesty check
    assert compute_batch_overall_status(["passed", "failed"]) == TestRunStatus.FAILED
    assert compute_batch_overall_status(["passed", "error"]) == TestRunStatus.ERROR
    assert compute_batch_overall_status(["passed", "passed"]) == TestRunStatus.PASSED

