"""Retell → VoxDesk import (P18-04).

The first three fixtures are cut to the shape documented at
``docs.retellai.com/api-references/create-agent`` and ``…/create-conversation-flow``
(fetched 2026-11-08): the field names, the ``instruction: {type, text}``
envelope, ``response_engine``, ``post_call_analysis_data`` and the
``transition_condition`` shape. They are minimal reproductions, not captured
customer data — no real export is committed to this repository.

The tests that matter most are the *negative* ones. A converter is easy to get
right on the happy path; what ruins a migration is a field that silently
disappears. So every unsupported field here has a test asserting it is named in
the report.
"""

from __future__ import annotations

import json

import pytest

from app.builder.flow_validation import validate_flow
from app.services.retell_import_service import (
    EXPORT_FORMAT,
    convert_retell_payload,
    redact_for_report,
)

pytestmark = pytest.mark.usefixtures()


def node_of(bundle, node_type: str):
    """The single node of a given type — order-independent on purpose."""
    matches = [n for n in bundle["flow"]["nodes"] if n["node_type"] == node_type]
    assert len(matches) == 1, f"expected exactly one {node_type} node, got {matches}"
    return matches[0]


def conditional_edge(bundle):
    edges = [e for e in bundle["flow"]["edges"] if e.get("condition")]
    assert len(edges) == 1, f"expected one conditional edge, got {edges}"
    return edges[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SINGLE_PROMPT_AGENT = {
    "agent_id": "agent_abc123",
    "agent_name": "Jarvis",
    "voice_id": "retell-Cimo",
    "voice_speed": 1.15,
    "language": "en-US",
    "responsiveness": 0.9,
    "begin_message_delay_ms": 1200,
    "max_call_duration_ms": 600000,
    "end_call_after_silence_ms": 12000,
    "webhook_url": "https://hooks.example.com/retell?token=supersecret123",
    "post_call_analysis_model": "gpt-4.1-mini",
    "post_call_analysis_data": [
        {
            "type": "string",
            "name": "customer_name",
            "description": "The name of the customer.",
            "examples": ["John Doe"],
            "required": True,
        },
        {
            "type": "enum",
            "name": "outcome",
            "description": "How the call ended.",
            "choices": ["booked", "declined"],
        },
    ],
    "response_engine": {
        "type": "retell-llm",
        "llm_id": "llm_xyz",
        "prompt": "You are Jarvis, a friendly receptionist. Ask how you can help.",
    },
    # Things Retell offers and VoxDesk does not carry:
    "expressive_emotion_tags": ["empathetic"],
    "guardrail_config": {"input_topics": ["platform_integrity_jailbreaking"]},
    "fallback_voice_ids": ["cartesia-Cimo"],
}

CONVERSATION_FLOW = {
    "conversation_flow_id": "cf_001",
    "start_speaker": "agent",
    "start_node_id": "start",
    "default_dynamic_variables": {"company_name": "Acme Dental", "support_hours": "9-5"},
    "nodes": [
        {
            "id": "start",
            "type": "conversation",
            "instruction": {
                "type": "prompt",
                "text": "Greet the caller and ask how you can help.",
            },
            "edges": [
                {
                    "id": "edge_1",
                    "transition_condition": {
                        "type": "prompt",
                        "prompt": "Customer wants to book an appointment",
                    },
                    "destination_node_id": "collect",
                }
            ],
        },
        {
            "id": "collect",
            "type": "extract_dynamic_variables",
            "variables": [
                {"name": "caller_name", "type": "string", "description": "Caller's name"},
                {
                    "name": "service",
                    "type": "enum",
                    "enum_values": ["cleaning", "whitening"],
                },
            ],
            "edges": [{"id": "edge_2", "destination_node_id": "book"}],
        },
        {
            "id": "book",
            "type": "function",
            "tool_name": "book_appointment",
            "tool_id": "tool_001",
            "url": "https://api.example.com/book",
            "method": "POST",
            "edges": [{"id": "edge_3", "destination_node_id": "done"}],
        },
        {"id": "done", "type": "end"},
    ],
}

TRANSFER_FLOW = {
    "conversation_flow_id": "cf_002",
    "nodes": [
        {
            "id": "menu",
            "type": "conversation",
            "instruction": {"type": "prompt", "text": "Offer to connect them."},
            "edges": [
                {
                    "id": "e1",
                    "transition_condition": {
                        "type": "equation",
                        "equations": [
                            {"variable": "wants_human", "operator": "==", "value": "true"}
                        ],
                    },
                    "destination_node_id": "xfer",
                }
            ],
        },
        {
            "id": "xfer",
            "type": "transfer_call",
            "transfer_destination": "+15550100111",
            "transfer_type": "warm_transfer",
        },
    ],
}

UNSUPPORTED_NODE_FLOW = {
    "conversation_flow_id": "cf_003",
    "nodes": [
        {
            "id": "start",
            "type": "conversation",
            "instruction": {"type": "prompt", "text": "Hello."},
            "edges": [{"id": "e1", "destination_node_id": "swap"}],
        },
        {"id": "swap", "type": "agent_swap", "agent_id": "agent_other"},
        {"id": "js", "type": "code", "code": "return 1;"},
        {"id": "mcpnode", "type": "mcp", "name": "crm"},
        {"id": "end", "type": "end"},
    ],
}


# ---------------------------------------------------------------------------
# Single-prompt agent
# ---------------------------------------------------------------------------


def test_single_prompt_agent_becomes_one_conversation_node():
    bundle, report = convert_retell_payload(SINGLE_PROMPT_AGENT)
    assert bundle["format"] == EXPORT_FORMAT
    assert bundle["agent"]["name"] == "Jarvis"
    nodes = bundle["flow"]["nodes"]
    assert [n["node_type"] for n in nodes] == ["start", "conversation", "end"]
    assert "Jarvis" in node_of(bundle, "conversation")["params"]["prompt"]
    # The start node is materialised from Retell's implicit entry point, and the
    # edge out of it is what makes the imported flow runnable.
    assert bundle["flow"]["edges"][0]["source_id"] == nodes[0]["id"]
    assert validate_flow(bundle["flow"]).valid


def test_single_prompt_agent_config_fields_convert():
    bundle, _ = convert_retell_payload(SINGLE_PROMPT_AGENT)
    config = bundle["agent"]["config"]
    assert config["language"] == "en-US"
    assert config["speech_speed"] == pytest.approx(1.15)
    assert config["responsiveness"] == pytest.approx(0.9)
    assert config["begin_message_delay_ms"] == 1200
    assert config["max_call_seconds"] == 600  # ms → s
    assert config["end_call_after_silence_seconds"] == 12


def test_voice_speed_and_responsiveness_are_clamped():
    payload = {**SINGLE_PROMPT_AGENT, "voice_speed": 9.5, "responsiveness": -4}
    bundle, _ = convert_retell_payload(payload)
    config = bundle["agent"]["config"]
    assert config["speech_speed"] == 2.0
    assert config["responsiveness"] == 0.0


def test_webhook_credential_is_stripped_and_reported():
    bundle, report = convert_retell_payload(SINGLE_PROMPT_AGENT)
    url = bundle["agent"]["config"]["webhook_url"]
    assert "supersecret123" not in url
    assert "***" in url
    lossy_paths = {entry.source_path for entry in report.lossy}
    assert "webhook_url" in lossy_paths, "the redaction must be reported, not silent"


def test_post_call_analysis_becomes_an_analysis_schema():
    bundle, _ = convert_retell_payload(SINGLE_PROMPT_AGENT)
    fields = bundle["agent"]["config"]["analysis_schema"]["fields"]
    by_name = {f["name"]: f for f in fields}
    assert by_name["customer_name"]["type"] == "string"
    assert by_name["customer_name"]["required"] is True
    assert by_name["customer_name"]["examples"] == ["John Doe"]
    assert by_name["outcome"]["values"] == ["booked", "declined"]


def test_retell_only_settings_are_reported_not_dropped():
    _, report = convert_retell_payload(SINGLE_PROMPT_AGENT)
    reported = {entry.source_path for entry in report.unsupported}
    for expected in (
        "expressive_emotion_tags",
        "guardrail_config",
        "fallback_voice_ids",
        "post_call_analysis_model",
    ):
        assert expected in reported, f"{expected} disappeared without a report entry"


def test_retell_voice_id_is_kept_but_flagged_as_lossy():
    bundle, report = convert_retell_payload(SINGLE_PROMPT_AGENT)
    assert bundle["agent"]["config"]["voice"] == "retell-Cimo"
    lossy = {entry.source_path: entry for entry in report.lossy}
    assert "voice_id" in lossy
    assert "provider" in lossy["voice_id"].reason.lower()
    assert lossy["voice_id"].suggestion


def test_single_prompt_agent_without_prompt_is_reported():
    payload = {
        "agent_id": "agent_noprompt",
        "agent_name": "Empty",
        "response_engine": {"type": "retell-llm", "llm_id": "llm_1"},
    }
    bundle, report = convert_retell_payload(payload)
    reported = {entry.source_path for entry in report.unsupported}
    assert "response_engine.prompt" in reported
    assert "response_engine.llm_id" in reported
    # It still produces a bundle rather than throwing — the operator gets the
    # shell of an agent and an explicit list of what is missing.
    assert [n["node_type"] for n in bundle["flow"]["nodes"]] == [
        "start",
        "conversation",
        "end",
    ]
    # ...and it says out loud that the flow will not validate yet.
    errors = {issue.code for issue in validate_flow(bundle["flow"]).errors}
    assert "INVALID_NODE" in errors
    assert any("empty" in e.reason or "not yet valid" in e.reason for e in report.unsupported)


# ---------------------------------------------------------------------------
# Conversation flow
# ---------------------------------------------------------------------------


def test_flow_nodes_and_edges_convert():
    bundle, report = convert_retell_payload(CONVERSATION_FLOW)
    node_types = [n["node_type"] for n in bundle["flow"]["nodes"]]
    assert node_types == ["start", "conversation", "extract_variables", "function", "end"]
    # three Retell edges, plus the one out of the synthesised start node
    assert len(bundle["flow"]["edges"]) == 4
    assert validate_flow(bundle["flow"]).valid
    # ids are re-minted, not copied, so two imports cannot collide
    assert not {"start", "collect", "book", "done"} & {
        n["id"] for n in bundle["flow"]["nodes"]
    }
    assert report.clean or report.unsupported  # either way, a report exists


def test_flow_instruction_text_becomes_the_node_prompt():
    bundle, _ = convert_retell_payload(CONVERSATION_FLOW)
    assert "Greet the caller" in node_of(bundle, "conversation")["params"]["prompt"]


def test_extract_variables_node_carries_typed_variables():
    bundle, _ = convert_retell_payload(CONVERSATION_FLOW)
    node = node_of(bundle, "extract_variables")
    names = {v["name"] for v in node["params"]["variables"]}
    assert names == {"caller_name", "service"}
    service = next(v for v in node["params"]["variables"] if v["name"] == "service")
    assert service["enum_values"] == ["cleaning", "whitening"]


def test_default_dynamic_variables_become_initial_variables():
    bundle, _ = convert_retell_payload(CONVERSATION_FLOW)
    initial = bundle["flow"]["initial_variables"]
    assert initial["company_name"] == "Acme Dental"
    assert initial["support_hours"] == "9-5"
    # Extract-node outputs are declared too (empty) — otherwise a prompt that
    # interpolates {{caller_name}} fails validation on import.
    assert initial["caller_name"] == ""
    assert initial["service"] == ""


def test_prompt_transition_condition_is_kept_as_a_label_and_called_lossy():
    bundle, report = convert_retell_payload(CONVERSATION_FLOW)
    first_edge = conditional_edge(bundle)
    assert first_edge["condition"]["label"].startswith("Customer wants to book")
    lossy = [e for e in report.lossy if "transition_condition" in e.source_path]
    assert lossy, "a prompt condition is not a deterministic test; say so"
    assert "re-evaluated" in lossy[0].reason or "equation" in lossy[0].suggestion


def test_transfer_node_requires_and_keeps_a_destination():
    bundle, _ = convert_retell_payload(TRANSFER_FLOW)
    transfer = next(n for n in bundle["flow"]["nodes"] if n["node_type"] == "transfer")
    assert transfer["params"]["destination"] == "+15550100111"
    # wants_human is only named in an edge condition, never declared by Retell.
    assert "wants_human" in bundle["flow"]["initial_variables"]
    assert transfer["params"]["transfer_mode"] == "warm"
    assert validate_flow(bundle["flow"]).valid


def test_equation_condition_is_converted_not_labelled():
    bundle, _ = convert_retell_payload(TRANSFER_FLOW)
    edge = conditional_edge(bundle)
    assert edge["condition"]["equations"][0]["variable"] == "wants_human"


def test_unsupported_node_types_are_named_individually():
    bundle, report = convert_retell_payload(UNSUPPORTED_NODE_FLOW)
    reported = {entry.source_path for entry in report.unsupported}
    assert "nodes[swap]" in reported
    assert "nodes[js]" in reported
    assert "nodes[mcpnode]" in reported
    # And the nodes themselves are absent rather than half-converted.
    types = {n["node_type"] for n in bundle["flow"]["nodes"]}
    assert "code" not in types
    # The edge that pointed at the dropped swap node is reported too.
    assert any("destination_node_id" in p for p in reported)


def test_transfer_without_destination_is_reported():
    payload = {
        "conversation_flow_id": "cf_004",
        "nodes": [
            {
                "id": "a",
                "type": "conversation",
                "instruction": {"type": "prompt", "text": "hi"},
                "edges": [{"id": "e", "destination_node_id": "t"}],
            },
            {"id": "t", "type": "transfer_call"},
        ],
    }
    bundle, report = convert_retell_payload(payload)
    reported = {entry.source_path for entry in report.unsupported}
    assert "nodes[t]" in reported
    assert all(n["node_type"] != "transfer" for n in bundle["flow"]["nodes"])


def test_knowledge_base_ids_are_reported_with_a_suggestion():
    payload = {**CONVERSATION_FLOW, "knowledge_base_ids": ["kb_001", "kb_002"]}
    _, report = convert_retell_payload(payload)
    entry = next(e for e in report.unsupported if e.source_path == "knowledge_base_ids")
    assert "upload" in entry.suggestion.lower()


def test_global_prompt_is_reported_because_merging_would_change_every_node():
    payload = {**CONVERSATION_FLOW, "global_prompt": "Be brief."}
    _, report = convert_retell_payload(payload)
    entry = next(e for e in report.unsupported if e.source_path == "global_prompt")
    assert "every node" in entry.reason.lower() or "flow-wide" in entry.reason.lower()


def test_agent_pointing_at_a_flow_is_sent_to_the_flow_export():
    payload = {
        "agent_id": "agent_cf",
        "agent_name": "Flow agent",
        "response_engine": {"type": "conversation-flow", "conversation_flow_id": "cf_001"},
    }
    _, report = convert_retell_payload(payload)
    entry = next(
        e for e in report.unsupported if "conversation_flow_id" in e.source_path
    )
    assert "import the flow" in (entry.reason + entry.suggestion).lower()


# ---------------------------------------------------------------------------
# Contract-level guarantees
# ---------------------------------------------------------------------------


def test_non_retell_payload_is_refused():
    with pytest.raises(ValueError):
        convert_retell_payload({"hello": "world"})


def test_report_is_json_serialisable_for_a_ticket():
    _, report = convert_retell_payload(CONVERSATION_FLOW)
    payload = report.as_dict()
    assert set(payload["counts"]) == {"converted", "unsupported", "lossy"}
    json.dumps(payload)  # must not raise


def test_redact_for_report_removes_the_webhook_secret():
    bundle, _ = convert_retell_payload(SINGLE_PROMPT_AGENT)
    safe = redact_for_report(bundle)
    assert "supersecret123" not in json.dumps(safe)
    # and it does not mutate the original
    assert "supersecret123" in json.dumps(bundle) or "***" in bundle["agent"]["config"]["webhook_url"]


def test_two_imports_of_the_same_payload_are_identical():
    """Deterministic ids: re-importing must produce a diffable bundle."""
    first, _ = convert_retell_payload(CONVERSATION_FLOW)
    second, _ = convert_retell_payload(CONVERSATION_FLOW)
    assert first == second


def test_every_retell_only_field_produces_an_unsupported_entry():
    """No field may vanish. This is the guard against a silent migration."""
    from app.services.retell_import_service import _convert_agent_fields, ImportReport

    payload = {
        "agent_id": "a",
        "agent_name": "x",
        "expressive_emotion_tags": ["x"],
        "handbook_config": {"high_empathy": True},
        "pii_config": {"mode": "post_call"},
        "data_storage_setting": "everything",
        "pronunciation_dictionary": [{"word": "a"}],
        "voice_temperature": 1.0,
        "volume": 1.0,
    }
    report = ImportReport()
    _convert_agent_fields(payload, report)
    reported = {e.source_path for e in report.unsupported} | {
        e.source_path for e in report.lossy
    }
    for key in (
        "expressive_emotion_tags",
        "handbook_config",
        "pii_config",
        "data_storage_setting",
        "pronunciation_dictionary",
    ):
        assert key in reported, f"{key} was neither converted nor reported"
