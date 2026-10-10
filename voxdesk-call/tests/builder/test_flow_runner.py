# File: tests/builder/test_flow_runner.py — Tests for FlowRunner transitions, fake judge, variable extraction, action nodes (function/transfer/DTMF/SMS), W-12 bindings, flow API routes, and E2E published flow call timeline (Part 5 / Gate G6)
"""Tests for `app.builder.flow_runner` and `app.api.agent_flow_routes`."""

from __future__ import annotations

import asyncio
import pytest

import app.db.models as db_models
from app.agent.flow_processor import build_flow_processor_for_runtime
from app.builder.flow_runner import FlowRunner, load_agent_flow_bindings
from app.db.enterprise_models import AgentTool, KnowledgeCollection, WorkflowTrigger
from app.db.models import Call, CallStatus, UserRole
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony.number_provisioning import PhoneNumber, PhoneNumberStatus
from pipecat.frames.frames import TranscriptionFrame
from tests.conftest import auth_headers, make_tenant, make_user


def _build_multi_node_flow() -> dict:
    return {
        "version": 1,
        "initial_variables": {"department": "general", "attempt": 1},
        "variables": [
            {"name": "department", "type": "string", "default": "general"},
            {"name": "customer_email", "type": "string"},
            {"name": "crm_tier", "type": "string"},
        ],
        "nodes": [
            {
                "id": "node_start",
                "type": "start",
                "label": "Start Call",
                "params": {"greeting": "Welcome to Acme!"},
            },
            {
                "id": "node_intake",
                "type": "conversation",
                "label": "Intake Conversation",
                "params": {
                    "prompt": "Greet the caller and ask for their email and department ({{department}}).",
                    "tools": ["lookup_crm"],
                    "knowledge_collection_ids": ["col_faq"],
                },
                "variables": [
                    {
                        "name": "customer_email",
                        "type": "string",
                        "pattern": r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
                    }
                ],
                "model_override": {
                    "provider": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "temperature": 0.15,
                },
            },
            {
                "id": "node_extract",
                "type": "extract_variables",
                "label": "Mark Sales Dept",
                "params": {"set_variables": {"department": "sales"}},
            },
            {
                "id": "node_split",
                "type": "logic_split",
                "label": "Route by Department",
                "params": {},
            },
            {
                "id": "node_dtmf",
                "type": "press_digit",
                "label": "Send IVR Extension",
                "params": {"digits": "42#", "pause_ms": 200},
            },
            {
                "id": "node_sms",
                "type": "send_sms",
                "label": "Send Confirmation SMS",
                "params": {
                    "to_number": "+14155550111",
                    "message": "Hi {{customer_email}}, connecting you to {{department}}.",
                },
            },
            {
                "id": "node_crm",
                "type": "function",
                "label": "Lookup CRM Tier",
                "params": {
                    "tool_name": "lookup_crm",
                    "arguments": {"email": "{{customer_email}}"},
                    "output_variable": "crm_result",
                    "response_mapping": {"crm_tier": "tier"},
                },
            },
            {
                "id": "node_transfer",
                "type": "transfer",
                "label": "Warm Transfer to Enterprise AE",
                "params": {
                    "transfer_mode": "warm",
                    "destination": "+14155550199",
                    "whisper_text": "Customer {{customer_email}} ({{crm_tier}}) ready.",
                },
            },
            {
                "id": "node_end_fallback",
                "type": "end",
                "label": "Fallback End",
                "params": {
                    "reason": "fallback",
                    "speak_text": "Goodbye!",
                },
            },
            {
                "id": "node_global_cancel",
                "type": "end",
                "label": "Global Cancel",
                "params": {
                    "reason": "caller_cancelled",
                    "speak_text": "Understood, ending the call now.",
                },
                "is_global": True,
                "global_config": {
                    "enabled": True,
                    "return_to_previous": False,
                    "trigger_condition": {
                        "kind": "prompt",
                        "prompt": "Caller explicitly asks to hang up or cancel immediately",
                    },
                },
            },
        ],
        "edges": [
            {
                "id": "e_start_intake",
                "source": "node_start",
                "target": "node_intake",
                "condition": {"kind": "always"},
            },
            {
                "id": "e_intake_extract",
                "source": "node_intake",
                "target": "node_extract",
                "priority": 10,
                "condition": {
                    "kind": "prompt",
                    "prompt": "Caller provides email and wants sales",
                },
            },
            {
                "id": "e_extract_split",
                "source": "node_extract",
                "target": "node_split",
                "condition": {"kind": "always"},
            },
            {
                "id": "e_split_dtmf",
                "source": "node_split",
                "target": "node_dtmf",
                "priority": 10,
                "condition": {
                    "kind": "equation",
                    "match_mode": "all",
                    "equations": [
                        {"variable": "department", "operator": "==", "value": "sales"},
                        {"variable": "customer_email", "operator": "contains", "value": "@"},
                    ],
                },
            },
            {
                "id": "e_split_fallback",
                "source": "node_split",
                "target": "node_end_fallback",
                "priority": 0,
                "condition": {"kind": "else"},
            },
            {
                "id": "e_dtmf_sms",
                "source": "node_dtmf",
                "target": "node_sms",
                "condition": {"kind": "always"},
            },
            {
                "id": "e_sms_crm",
                "source": "node_sms",
                "target": "node_crm",
                "condition": {"kind": "always"},
            },
            {
                "id": "e_crm_transfer",
                "source": "node_crm",
                "target": "node_transfer",
                "condition": {
                    "kind": "equation",
                    "match_mode": "all",
                    "equations": [
                        {"variable": "crm_tier", "operator": "==", "value": "enterprise"}
                    ],
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_flow_runner_transitions_equations_fake_judge_and_action_nodes() -> None:
    flow = _build_multi_node_flow()
    judge_calls: list[tuple[str, str]] = []
    dtmf_sent: list[tuple[str, int]] = []
    sms_sent: list[tuple[str, str | None]] = []
    transfers: list[dict] = []

    async def fake_judge(condition_prompt: str, utterance: str, variables: dict) -> bool:
        judge_calls.append((condition_prompt, utterance))
        if "hang up" in condition_prompt.lower():
            return "cancel call" in utterance.lower()
        if "provides email and wants sales" in condition_prompt.lower():
            return "sales" in utterance.lower() and "@" in utterance.lower()
        return False

    async def fake_fn_handler(tool_name: str, args: dict) -> dict:
        assert tool_name == "lookup_crm"
        assert args["email"] == "alice@acme.io"
        return {"ok": True, "tier": "enterprise", "account_id": "acc_99"}

    async def fake_dtmf(digits: str, pause_ms: int) -> dict:
        dtmf_sent.append((digits, pause_ms))
        return {"ok": True, "digits": digits}

    async def fake_sms(message: str, to_number: str | None) -> dict:
        sms_sent.append((message, to_number))
        return {"ok": True, "message": message}

    async def fake_transfer(payload: dict) -> dict:
        transfers.append(payload)
        return {"ok": True, **payload}

    runner = FlowRunner(
        flow,
        base_system_prompt="You are Acme Voice Assistant.",
        available_tools=[
            {
                "type": "function",
                "function": {"name": "lookup_crm", "description": "Lookup CRM"},
            },
            {
                "type": "function",
                "function": {"name": "unused_tool", "description": "Should be filtered out"},
            },
        ],
        knowledge_collections=[
            {
                "id": "col_faq",
                "name": "Pricing FAQ",
                "description": "Enterprise seats start at $99/mo.",
            }
        ],
        judge=fake_judge,
        function_handler=fake_fn_handler,
        dtmf_handler=fake_dtmf,
        sms_handler=fake_sms,
        transfer_handler=fake_transfer,
    )

    # 1. Start advances automatically from `node_start` -> `node_intake`
    start_res = await runner.start()
    assert start_res.current_node_id == "node_intake"
    assert start_res.current_node_type == "conversation"
    assert "Pricing FAQ" in start_res.system_prompt
    assert len(start_res.tools) == 1
    assert start_res.tools[0]["function"]["name"] == "lookup_crm"
    assert start_res.model_override is not None
    assert start_res.model_override["model"] == "llama-3.3-70b-versatile"

    # 2. User turn triggers regex variable extraction + fake judge -> extract -> split -> dtmf -> sms -> crm -> transfer
    step_res = await runner.step(
        "Hi, my email is alice@acme.io and I need to talk to sales please."
    )
    assert step_res.transitioned is True
    assert step_res.current_node_id == "node_transfer"
    assert step_res.transferred is True
    assert step_res.ended is True

    # Verify dynamic variables updated across the chain
    assert runner.variables["customer_email"] == "alice@acme.io"
    assert runner.variables["department"] == "sales"
    assert runner.variables["crm_tier"] == "enterprise"

    # Verify DTMF, SMS, Function, and Transfer handlers were all invoked
    assert dtmf_sent == [("42#", 200)]
    assert sms_sent == [
        ("Hi alice@acme.io, connecting you to sales.", "+14155550111")
    ]
    assert len(transfers) == 1
    assert transfers[0]["destination"] == "+14155550199"
    assert "alice@acme.io (enterprise)" in transfers[0]["whisper_text"]


@pytest.mark.asyncio
async def test_flow_runner_judge_timeout_and_global_node_interception() -> None:
    flow = _build_multi_node_flow()

    async def slow_or_global_judge(condition_prompt: str, utterance: str, _vars: dict) -> bool:
        if "hang up" in condition_prompt.lower() and "cancel" in utterance.lower():
            return True
        await asyncio.sleep(0.2)
        return True

    runner = FlowRunner(
        flow,
        judge=slow_or_global_judge,
        judge_timeout_s=0.02,
    )
    await runner.start()
    assert runner.current_node_id == "node_intake"

    # Judge times out on normal utterance -> stays on `node_intake`
    res_timeout = await runner.step("Tell me more about your product")
    assert res_timeout.transitioned is False
    assert res_timeout.current_node_id == "node_intake"

    # Global node intercepts from anywhere when caller says "cancel"
    res_global = await runner.step("Please cancel call right now")
    assert res_global.transitioned is True
    assert res_global.current_node_id == "node_global_cancel"
    assert res_global.ended is True
    assert res_global.speak_text == "Understood, ending the call now."


@pytest.mark.asyncio
async def test_e2e_flow_published_bound_to_number_drives_call_timeline_and_w12_bindings(
    client, db
) -> None:
    """End-to-end Gate G6 acceptance test:
    1. Registers W-12 rows (`AgentTool`, `KnowledgeCollection`, `WorkflowTrigger`).
    2. Saves a 4-node flow (`start -> conversation -> function -> transfer`) via `PUT /api/agents/{id}/flow`.
    3. Validates and simulates via `/api/agents/{id}/flow/validate` & `/api/agents/{id}/flow/simulate`.
    4. Publishes via `POST /api/agents/{id}/flow/publish` and binds the agent to a `PhoneNumber`.
    5. Resolves the call-time config via `resolve_for_call` and drives a `Call` through `FlowProcessor`
       across `conversation -> function -> transfer`, verifying node-transition events in `call.transfer_context["flow_transitions"]`.
    """
    tenant = await make_tenant(db, name="Flow Builder E2E Tenant")
    admin = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    viewer = await make_user(db, tenant=tenant, role=UserRole.VIEWER)
    other_tenant = await make_tenant(db, name="Other Isolated Tenant")
    other_admin = await make_user(db, tenant=other_tenant, role=UserRole.ADMIN)

    headers = await auth_headers(client, admin)
    viewer_headers = await auth_headers(client, viewer)
    other_headers = await auth_headers(client, other_admin)

    # Create an agent via POST /api/agents
    create_resp = await client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Inbound Sales Flow Agent", "description": "Gate G6 E2E"},
    )
    assert create_resp.status_code in (200, 201), create_resp.text
    agent_id = create_resp.json()["id"]

    # Register W-12 entities: AgentTool, KnowledgeCollection, WorkflowTrigger
    db.add(
        AgentTool(
            tenant_id=tenant.id,
            agent_id=str(agent_id),
            name="verify_account",
            description="Verify caller CRM account via HTTPS",
            schema={"type": "object", "properties": {"phone": {"type": "string"}}},
            auth_binding={"endpoint_url": "https://crm.example.com/verify", "http_method": "POST"},
            is_enabled=True,
        )
    )
    kc = KnowledgeCollection(
        tenant_id=tenant.id,
        name="Enterprise SLA Docs",
        description="99.99% uptime guarantee for enterprise voice.",
        agent_ids=[str(agent_id)],
        is_active=True,
    )
    db.add(kc)
    db.add(
        WorkflowTrigger(
            tenant_id=tenant.id,
            workflow_id=str(agent_id),
            event_type="transfer_initiated",
            config={"action_type": "webhook", "url": "https://hooks.example.com/transfer"},
            is_enabled=True,
        )
    )
    await db.commit()
    await db.refresh(kc)

    bindings = await load_agent_flow_bindings(
        db, tenant_id=tenant.id, agent_id=db_models.uuid.UUID(agent_id)
    )
    assert any(t["function"]["name"] == "verify_account" for t in bindings["tools"])
    assert any(c["name"] == "Enterprise SLA Docs" for c in bindings["knowledge_collections"])
    assert len(bindings["workflow_triggers"]) == 1

    e2e_flow = {
        "version": 1,
        "initial_variables": {"acct_status": ""},
        "variables": [{"name": "acct_status", "type": "string"}],
        "nodes": [
            {
                "id": "n_start",
                "type": "start",
                "label": "Start",
                "params": {"greeting": "Welcome to Enterprise Support!"},
            },
            {
                "id": "n_conversation",
                "type": "conversation",
                "label": "Verify Intent",
                "params": {
                    "prompt": "Ask caller if they need enterprise routing.",
                    "tools": ["verify_account"],
                    "knowledge_collection_ids": [str(kc.id)],
                },
            },
            {
                "id": "n_function",
                "type": "function",
                "label": "Run CRM Verify",
                "params": {
                    "tool_name": "verify_account",
                    "arguments": {"phone": "{{caller_number}}"},
                    "output_variable": "acct_status",
                },
            },
            {
                "id": "n_transfer",
                "type": "transfer",
                "label": "Transfer to Specialist",
                "params": {
                    "transfer_mode": "warm",
                    "destination": "+14155550199",
                    "whisper_text": "Verified caller {{caller_number}} ({{acct_status}})",
                },
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n_start",
                "target": "n_conversation",
                "condition": {"kind": "always"},
            },
            {
                "id": "e2",
                "source": "n_conversation",
                "target": "n_function",
                "condition": {
                    "kind": "prompt",
                    "prompt": "Caller asks for enterprise specialist",
                },
            },
            {
                "id": "e3",
                "source": "n_function",
                "target": "n_transfer",
                "condition": {
                    "kind": "equation",
                    "match_mode": "all",
                    "equations": [
                        {"variable": "acct_status", "operator": "is_set", "value": None}
                    ],
                },
            },
        ],
    }

    # Viewer cannot PUT flow (403)
    viewer_put = await client.put(
        f"/api/agents/{agent_id}/flow",
        headers=viewer_headers,
        json={"mode": "flow", "flow": e2e_flow},
    )
    assert viewer_put.status_code == 403

    # Cross-tenant admin gets 404
    cross_get = await client.get(
        f"/api/agents/{agent_id}/flow",
        headers=other_headers,
    )
    assert cross_get.status_code == 404

    # Save valid draft flow via PUT /api/agents/{id}/flow
    put_resp = await client.put(
        f"/api/agents/{agent_id}/flow",
        headers=headers,
        json={"mode": "flow", "flow": e2e_flow},
    )
    assert put_resp.status_code == 200, put_resp.text
    put_data = put_resp.json()
    assert put_data["validation"]["valid"] is True

    # Simulate via POST /api/agents/{id}/flow/simulate
    sim_resp = await client.post(
        f"/api/agents/{agent_id}/flow/simulate",
        headers=headers,
        json={
            "turns": ["Please connect me to an enterprise specialist"],
            "initial_variables": {"caller_number": "+14155550123"},
        },
    )
    assert sim_resp.status_code == 200, sim_resp.text
    sim_data = sim_resp.json()
    assert sim_data["final_node_id"] == "n_transfer"
    assert sim_data["transferred"] is True
    assert len(sim_data["transitions"]) >= 3

    # Publish via POST /api/agents/{id}/flow/publish
    pub_resp = await client.post(
        f"/api/agents/{agent_id}/flow/publish",
        headers=headers,
        json={"changelog": "E2E published flow v1", "environment": "production"},
    )
    assert pub_resp.status_code == 200, pub_resp.text
    pub_data = pub_resp.json()
    assert pub_data["version"] == 1
    assert pub_data["mode"] == "flow"

    # Bind published agent to a PhoneNumber and resolve call-time config
    phone = PhoneNumber(
        tenant_id=tenant.id,
        e164="+14155550999",
        friendly_name="Sales Line",
        inbound_agent_id=db_models.uuid.UUID(agent_id),
        status=PhoneNumberStatus.ACTIVE.value,
    )
    db.add(phone)
    call = Call(
        tenant_id=tenant.id,
        call_sid="CA_FLOW_E2E_001",
        from_number="+14155550123",
        to_number="+14155550999",
        status=CallStatus.IN_PROGRESS,
        transfer_context={},
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)

    runtime_cfg = await resolve_runtime_config(
        db,
        call=call,
        tenant=tenant,
    )
    assert runtime_cfg.raw_config.get("mode") == "flow"
    assert len(runtime_cfg.raw_config.get("flow", {}).get("nodes", [])) == 4

    # Drive real Call through FlowProcessor (`start -> n_conversation -> n_function -> n_transfer`)
    emitted_frames = []

    class _DummyHandlers:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def dispatch(self, name: str, args: dict) -> dict:
            self.calls.append((name, args))
            return {"ok": True, "status": "verified_enterprise"}

    dummy_handlers = _DummyHandlers()
    processor = build_flow_processor_for_runtime(
        runtime_config=runtime_cfg,
        handlers=dummy_handlers,
        call=call,
        judge=lambda prompt, text, _vars: "specialist" in text.lower(),
    )
    assert processor is not None
    processor._frame_pusher = lambda frame: _append_async(emitted_frames, frame)

    async def _append_async(lst: list, item: object) -> None:
        lst.append(item)

    await processor.initialize()
    assert processor.runner.current_node_id == "n_conversation"

    await processor.process_frame(
        TranscriptionFrame(
            text="I need an enterprise specialist right away",
            user_id="caller",
            timestamp="2026-10-08T12:00:00Z",
        )
    )

    assert processor.runner.current_node_id == "n_transfer"
    assert processor.runner.transferred is True
    timeline = call.transfer_context.get("flow_transitions", [])
    visited_Target_Nodes = [t["to_node_id"] for t in timeline]
    assert visited_Target_Nodes == ["n_conversation", "n_function", "n_transfer"]
    assert any(name == "verify_account" for name, _ in dummy_handlers.calls)
    assert any(name == "warm_transfer" for name, _ in dummy_handlers.calls)
