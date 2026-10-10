# File: tests/builder/test_flow_validation.py — Unit tests for conversation-flow graph validation: reachability, dead ends, unique IDs, undefined variables, unreachable globals, and JSON-Schema round-trip (Part 5 / Gate G6)
"""Tests for `app.builder.flow_validation` and `app.builder.node`."""

from __future__ import annotations

from app.builder.flow_validation import validate_flow
from app.builder.node import (
    FLOW_NODE_TYPES,
    FlowGraph,
    export_flow_json_schema,
)


def _valid_flow_dict() -> dict:
    return {
        "version": 1,
        "initial_variables": {"department": "sales"},
        "variables": [
            {
                "name": "department",
                "type": "string",
                "description": "Caller target department",
                "required": True,
                "default": "sales",
            }
        ],
        "nodes": [
            {
                "id": "n_start",
                "type": "start",
                "label": "Start",
                "params": {"greeting": "Welcome to VoxDesk!"},
                "position": {"x": 50, "y": 100},
            },
            {
                "id": "n_convo",
                "type": "conversation",
                "label": "Qualify Caller",
                "params": {
                    "prompt": "Ask caller if they want {{department}}.",
                    "tools": ["lookup_customer"],
                },
                "position": {"x": 300, "y": 100},
                "model_override": {
                    "provider": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "temperature": 0.2,
                    "max_tokens": 256,
                },
                "voice_override": {
                    "provider": "cartesia",
                    "voice_id": "sonic-en",
                    "speed": 1.05,
                },
            },
            {
                "id": "n_fn",
                "type": "function",
                "label": "Lookup Customer",
                "params": {
                    "tool_name": "lookup_customer",
                    "arguments": {"phone": "{{caller_number}}"},
                    "output_variable": "crm_status",
                },
                "position": {"x": 560, "y": 100},
            },
            {
                "id": "n_transfer",
                "type": "transfer",
                "label": "Warm Transfer",
                "params": {
                    "transfer_mode": "warm",
                    "destination": "+14155550199",
                    "whisper_text": "CRM status: {{crm_status}}",
                },
                "position": {"x": 820, "y": 100},
            },
            {
                "id": "n_global_human",
                "type": "transfer",
                "label": "Global Operator Escalation",
                "params": {
                    "transfer_mode": "cold",
                    "destination": "+14155550100",
                },
                "is_global": True,
                "global_config": {
                    "enabled": True,
                    "return_to_previous": False,
                    "trigger_condition": {
                        "kind": "prompt",
                        "prompt": "Caller asks for an operator immediately",
                    },
                },
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n_start",
                "target": "n_convo",
                "condition": {"kind": "always"},
            },
            {
                "id": "e2",
                "source": "n_convo",
                "target": "n_fn",
                "condition": {
                    "kind": "prompt",
                    "prompt": "Caller confirms they need sales",
                },
            },
            {
                "id": "e3",
                "source": "n_fn",
                "target": "n_transfer",
                "condition": {
                    "kind": "equation",
                    "match_mode": "all",
                    "equations": [
                        {
                            "variable": "crm_status",
                            "operator": "is_set",
                            "value": None,
                        }
                    ],
                },
            },
        ],
    }


def test_valid_flow_passes_with_zero_errors() -> None:
    res = validate_flow(_valid_flow_dict())
    assert res.valid is True
    assert res.errors == []
    assert len(FLOW_NODE_TYPES) == 10


def test_missing_and_multiple_start_nodes_reported() -> None:
    flow = _valid_flow_dict()
    flow["nodes"] = [n for n in flow["nodes"] if n["id"] != "n_start"]
    flow["edges"] = [e for e in flow["edges"] if e["source"] != "n_start"]

    res_missing = validate_flow(flow)
    assert res_missing.valid is False
    codes = {e.code for e in res_missing.errors}
    assert "MISSING_START_NODE" in codes

    flow_multi = _valid_flow_dict()
    flow_multi["nodes"].append(
        {"id": "n_start_2", "type": "start", "label": "Second Start", "params": {}}
    )
    res_multi = validate_flow(flow_multi)
    assert res_multi.valid is False
    multi_errs = [e for e in res_multi.errors if e.code == "MULTIPLE_START_NODES"]
    assert len(multi_errs) == 1
    assert multi_errs[0].node_id == "n_start_2"


def test_unreachable_node_and_dead_end_detected_with_node_ids() -> None:
    flow = _valid_flow_dict()
    # Add an isolated conversation node with no incoming or outgoing edges
    flow["nodes"].append(
        {
            "id": "n_orphan",
            "type": "conversation",
            "label": "Orphan Step",
            "params": {"prompt": "Unreachable"},
        }
    )
    res = validate_flow(flow)
    assert res.valid is False
    by_code = {(e.code, e.node_id) for e in res.errors}
    assert ("UNREACHABLE_NODE", "n_orphan") in by_code
    assert ("DEAD_END_NODE", "n_orphan") in by_code


def test_duplicate_node_and_edge_ids_detected() -> None:
    flow = _valid_flow_dict()
    flow["nodes"].append(
        {
            "id": "n_convo",
            "type": "end",
            "label": "Duplicate ID Node",
            "params": {},
        }
    )
    flow["edges"].append(
        {
            "id": "e1",
            "source": "n_start",
            "target": "n_convo",
        }
    )
    res = validate_flow(flow)
    assert res.valid is False
    codes = {e.code for e in res.errors}
    assert "DUPLICATE_NODE_ID" in codes
    assert "DUPLICATE_EDGE_ID" in codes


def test_undefined_variable_in_prompt_and_edge_equation_detected() -> None:
    flow = _valid_flow_dict()
    flow["nodes"][1]["params"]["prompt"] = "Hello {{non_existent_var}}!"
    flow["edges"][2]["condition"] = {
        "kind": "equation",
        "match_mode": "all",
        "equations": [
            {"variable": "mystery_edge_var", "operator": "==", "value": "yes"}
        ],
    }

    res = validate_flow(flow)
    assert res.valid is False
    undef_errs = [e for e in res.errors if e.code == "UNDEFINED_VARIABLE"]
    assert len(undef_errs) >= 2
    offending_nodes = {e.node_id for e in undef_errs}
    assert "n_convo" in offending_nodes
    assert "n_fn" in offending_nodes


def test_unreachable_global_node_without_trigger_detected() -> None:
    flow = _valid_flow_dict()
    flow["nodes"].append(
        {
            "id": "n_bad_global",
            "type": "end",
            "label": "Broken Global",
            "is_global": True,
            "global_config": {
                "enabled": True,
                "trigger_condition": None,
            },
        }
    )
    res = validate_flow(flow)
    assert res.valid is False
    glob_errs = [e for e in res.errors if e.code == "UNREACHABLE_GLOBAL_NODE"]
    assert len(glob_errs) == 1
    assert glob_errs[0].node_id == "n_bad_global"


def test_flow_graph_round_trip_and_json_schema_export() -> None:
    raw = _valid_flow_dict()
    graph = FlowGraph.from_dict(raw)
    serialized = graph.to_dict()
    rehydrated = FlowGraph.from_dict(serialized)
    assert rehydrated.to_dict() == serialized

    schema = export_flow_json_schema()
    assert schema["title"] == "VoxDeskConversationFlow"
    node_enum = schema["$defs"]["FlowNode"]["properties"]["type"]["enum"]
    assert set(node_enum) == set(FLOW_NODE_TYPES)
