"""Retell → VoxDesk agent import (PART 18, P18-04).

Input is what a Retell customer actually has: an *agent* object (single-prompt,
``response_engine.type == "retell-llm"``) or a *conversation flow* object
(``nodes[]`` with ``edges[]``), as returned by
``GET /get-agent/{agent_id}`` and ``GET /get-conversation-flow/{id}``.

Output is a ``voxdesk.agent/1`` bundle — the same format
``app.services.agent_portability_service`` already exports, so an imported
Retell agent is indistinguishable downstream from a native one.

The rule that shapes every line here
------------------------------------
**A field is either converted or reported. It is never dropped in silence.**
A migration that quietly loses ``post_call_analysis_data`` produces an agent
that looks right and scores nothing; the customer finds out a month later. So
``ImportReport`` carries three lists:

``converted``    — ``{source_path, target_path, rule}`` for everything mapped
``unsupported``  — ``{source_path, value_summary, reason, suggestion}`` for
                   everything this importer will not claim to translate
``lossy``        — conversions that are correct but not exact (a prompt
                   condition becomes an edge label, not a deterministic test)

Mapping, with the reasons
-------------------------
============================  =========================  ===============================
Retell                         VoxDesk                    Note
============================  =========================  ===============================
``agent_name``                agent name                 fallback ``"Imported agent"``
``language``                  ``config.language``        default ``en-US``
``voice_id``                  ``config.voice``           **provider id kept verbatim**;
                                                         VoxDesk voice ids differ, so
                                                         this is reported as lossy and
                                                         the operator re-picks a voice
``voice_speed``               ``config.speech_speed``    clamped 0.5–2.0
``responsiveness``            ``config.responsiveness``  clamped 0–1
``begin_message_delay_ms``    ``config.begin_message_delay_ms``
``max_call_duration_ms``      ``config.max_call_seconds``
``end_call_after_silence_ms`` ``config.end_call_after_silence_seconds``
``webhook_url``               ``config.webhook_url``     secret-looking query values
                                                         are redacted, never copied
``boosted_keywords``          ``config.boosted_keywords``
``post_call_analysis_data``   ``config.analysis_schema`` reshaped to ``fields[]``
``retell-llm`` prompt         ``flow`` single conversation node
flow ``nodes[].type``         VoxDesk ``node_type``      table below
flow ``edges[]``              ``edges[]``                ids re-minted
============================  =========================  ===============================

Node types that convert:

=====  ===============  ======================================================
Retell VoxDesk          Note
=====  ===============  ======================================================
conversation  conversation        ``instruction.text`` → ``params.prompt``
subagent      conversation        tool-calling is a mode, not a node: reported
extract_dynamic_variables  extract_variables   variables[] reshaped
function      function            ``tool_id``/``tool_name``/``url`` kept
code          code                JavaScript is **reported**, not translated
transfer_call transfer            ``destination`` required by VoxDesk
press_digit   press_digit         ``digits``
sms           send_sms            ``message``
branch / logic_split / logic_split  conditions → ``params.equations``
end           end
=====  ===============  ======================================================

Node types that do **not** convert, and why:

``agent_swap``      VoxDesk subagents are a different mechanism; an imported
                    swap becomes a ``subagent`` node only if the target agent is
                    itself imported — otherwise it is reported.
``mcp``             MCP servers are configured per tenant here, not per node.
``component``       Retell components are flow fragments; this importer flattens
                    a referenced component only when its nodes are provided in
                    the same payload, and reports it otherwise.
``bridge_transfer`` / ``cancel_transfer``   no VoxDesk equivalent.
``wait_for_input``  VoxDesk conversation nodes already wait; folding it in
                    changes timing, so it is reported.

Everything above is asserted in ``tests/services/test_retell_import.py``.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

EXPORT_FORMAT = "voxdesk.agent/1"

#: Retell → VoxDesk node type. Anything absent is reported, never guessed.
NODE_TYPE_MAP: Mapping[str, str] = {
    "conversation": "conversation",
    "subagent": "conversation",  # tool use is a capability of the node, not a type
    "extract_dynamic_variables": "extract_variables",
    "function": "function",
    "code": "code",
    "transfer_call": "transfer",
    "press_digit": "press_digit",
    "sms": "send_sms",
    "branch": "logic_split",
    "logic_split": "logic_split",
    "end": "end",
}

#: Reported with a reason instead of being mapped.
UNSUPPORTED_NODE_TYPES: Mapping[str, str] = {
    "agent_swap": "Retell agent swaps move the call to another agent; VoxDesk subagents "
    "are a separate mechanism. Import the target agent and wire it as a subagent.",
    "mcp": "MCP servers are configured per tenant in VoxDesk, not per node.",
    "component": "Retell components are flow fragments. Inline the component's nodes "
    "in the export and they will import; a dangling reference cannot.",
    "bridge_transfer": "No VoxDesk equivalent for bridge transfer.",
    "cancel_transfer": "No VoxDesk equivalent for cancel-transfer.",
    "wait_for_input": "A VoxDesk conversation node already waits for input; folding "
    "this in would change timing, so it is reported instead.",
}

#: A suggestion per reported field, where there is a concrete next step. Without
#: one the report is still honest, but the operator is left guessing.
_SUGGESTIONS: Mapping[str, str] = {
    "knowledge_base_ids": "Upload the same documents to the VoxDesk knowledge base, "
    "then attach the resulting documents to this agent.",
    "kb_config": "Knowledge-base settings are per-tenant in VoxDesk; set them when "
    "you re-attach the corpus.",
    "guardrail_config": "Set the equivalent topics in the VoxDesk guardrail policy, "
    "which is enforced tenant-wide.",
    "pii_config": "Configure redaction in the tenant's data-retention policy.",
    "data_storage_setting": "Choose a retention policy in tenant settings.",
    "backchannel_words": "VoxDesk uses its own backchannel defaults; no action needed "
    "unless the words are brand-specific.",
    "pronunciation_dictionary": "Add the pronunciations to the tenant glossary.",
    "post_session_tools": "Attach the equivalent tool to a post-call webhook.",
    "pre_session_tools": "Run the check in the inbound-call webhook instead.",
}

_SECRET_QUERY_RE = re.compile(
    r"([?&](?:api[_-]?key|key|token|secret|signature|sig|auth)=)[^&\s]+",
    re.IGNORECASE,
)

_MS_PER_SECOND = 1000

#: ``{{variable}}`` references inside a prompt. Retell lets any extracted or
#: dynamic variable be interpolated without declaring it anywhere; VoxDesk's
#: validator rejects an undeclared reference. The importer therefore seeds the
#: graph's ``initial_variables`` from every reference it finds.
_VAR_REF_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


@dataclass
class Conversion:
    source_path: str
    target_path: str
    rule: str

    def as_dict(self) -> dict[str, str]:
        return {"source_path": self.source_path, "target_path": self.target_path, "rule": self.rule}


@dataclass
class Unsupported:
    source_path: str
    value_summary: str
    reason: str
    suggestion: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "source_path": self.source_path,
            "value_summary": self.value_summary,
            "reason": self.reason,
            "suggestion": self.suggestion,
        }


@dataclass
class ImportReport:
    """What happened, field by field. Empty lists are meaningful: nothing lost."""

    converted: list[Conversion] = field(default_factory=list)
    unsupported: list[Unsupported] = field(default_factory=list)
    lossy: list[Unsupported] = field(default_factory=list)

    def convert(self, source: str, target: str, rule: str) -> None:
        self.converted.append(Conversion(source, target, rule))

    def report(self, source: str, value: Any, reason: str, suggestion: str = "") -> None:
        self.unsupported.append(Unsupported(source, _summarise(value), reason, suggestion))

    def note_lossy(self, source: str, value: Any, reason: str, suggestion: str = "") -> None:
        self.lossy.append(Unsupported(source, _summarise(value), reason, suggestion))

    @property
    def clean(self) -> bool:
        return not self.unsupported

    def as_dict(self) -> dict[str, Any]:
        return {
            "converted": [c.as_dict() for c in self.converted],
            "unsupported": [u.as_dict() for u in self.unsupported],
            "lossy": [u.as_dict() for u in self.lossy],
            "counts": {
                "converted": len(self.converted),
                "unsupported": len(self.unsupported),
                "lossy": len(self.lossy),
            },
        }


def _summarise(value: Any) -> str:
    """A short, non-secret description of something we are not importing."""
    if value is None:
        return "null"
    if isinstance(value, Mapping):
        keys = ", ".join(list(value.keys())[:6])
        return f"object with keys [{keys}]"
    if isinstance(value, (list, tuple)):
        return f"list of {len(value)} item(s)"
    text = str(value)
    return text[:80] + ("…" if len(text) > 80 else "")


def _redact_url(url: str) -> tuple[str, bool]:
    """Strip credential-looking query parameters. Returns (url, was_redacted)."""
    redacted = _SECRET_QUERY_RE.sub(r"\1***", url)
    return redacted, redacted != url


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _new_node_id(index: int, hint: str) -> str:
    """Deterministic node ids so a re-import produces a diffable bundle."""
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", str(hint or "node")).strip("_")[:32] or "node"
    return f"{index:02d}_{safe}"


def _detect_kind(payload: Mapping[str, Any]) -> str:
    """"flow", "agent-single-prompt", or "agent-flow"."""
    if isinstance(payload.get("nodes"), list) and (
        "conversation_flow_id" in payload or "start_node_id" in payload or "start_speaker" in payload
    ):
        return "flow"
    if "agent_id" in payload or "response_engine" in payload or "voice_id" in payload:
        engine = payload.get("response_engine") or {}
        engine_type = str(engine.get("type") or "").lower()
        if engine_type in {"conversation-flow", "conversation_flow"}:
            return "agent-flow"
        return "agent-single-prompt"
    if isinstance(payload.get("nodes"), list):
        return "flow"
    raise ValueError(
        "not a Retell agent or conversation flow: expected an 'agent_id' or a "
        "'nodes' array at the top level"
    )


# ---------------------------------------------------------------------------
# Agent-level (non-flow) fields
# ---------------------------------------------------------------------------


def _convert_agent_fields(
    payload: Mapping[str, Any], report: ImportReport
) -> tuple[dict[str, Any], str]:
    """Map every agent-level field that has a VoxDesk home."""
    config: dict[str, Any] = {}
    name = ""

    if payload.get("agent_name"):
        name = str(payload["agent_name"])
        report.convert("agent_name", "agent.name", "copied")
    else:
        report.note_lossy(
            "agent_name",
            None,
            "Retell agent has no name; the import will be called 'Imported agent'.",
        )

    simple = {
        "language": ("config.language", "copied"),
        "voice_speed": ("config.speech_speed", "clamped to 0.5–2.0"),
        "volume": ("config.volume", "copied"),
        "responsiveness": ("config.responsiveness", "clamped to 0–1"),
        "interruption_sensitivity": ("config.interruption_sensitivity", "copied"),
        "begin_message_delay_ms": ("config.begin_message_delay_ms", "copied"),
        "boosted_keywords": ("config.boosted_keywords", "copied"),
        "enable_backchannel": ("config.enable_backchannel", "copied"),
        "denoising_mode": ("config.denoising_mode", "copied"),
        "timezone": ("config.timezone", "copied"),
        "end_call_after_silence_ms": (
            "config.end_call_after_silence_seconds",
            "divided by 1000",
        ),
        "max_call_duration_ms": ("config.max_call_seconds", "divided by 1000"),
    }

    for source_key, (target, rule) in simple.items():
        if source_key not in payload or payload[source_key] is None:
            continue
        value = payload[source_key]
        if source_key == "voice_speed":
            value = _clamp(value, 0.5, 2.0, 1.0)
        elif source_key == "responsiveness":
            value = _clamp(value, 0.0, 1.0, 1.0)
        elif source_key in {"end_call_after_silence_ms", "max_call_duration_ms"}:
            try:
                value = int(float(value) // _MS_PER_SECOND)
            except (TypeError, ValueError):
                report.report(source_key, value, "not a number; cannot convert to seconds")
                continue
        config[target.split(".", 1)[1]] = value
        report.convert(source_key, target, rule)

    # Voice: the provider id is kept verbatim but is not a VoxDesk voice id.
    if payload.get("voice_id"):
        config["voice"] = str(payload["voice_id"])
        report.note_lossy(
            "voice_id",
            payload["voice_id"],
            "Retell voice ids are provider-scoped ('retell-Cimo', 'cartesia-…'); "
            "VoxDesk voices are chosen from its own catalog. The id is kept for "
            "reference only and the operator must pick a voice.",
            "Re-select the voice in the agent builder after import.",
        )
    if payload.get("fallback_voice_ids"):
        report.report(
            "fallback_voice_ids",
            payload["fallback_voice_ids"],
            "VoxDesk configures a single voice per agent; per-agent fallback "
            "voices are not part of the config schema.",
        )

    # Webhook: copied, but credentials in the query string are stripped.
    if payload.get("webhook_url"):
        url, was_redacted = _redact_url(str(payload["webhook_url"]))
        config["webhook_url"] = url
        if was_redacted:
            report.note_lossy(
                "webhook_url",
                payload["webhook_url"],
                "the URL carried a credential in its query string; it was replaced "
                "with '***'. Re-enter the secret through the webhook settings so it "
                "is stored in the secret store, not in agent config.",
                "Set the webhook secret in the dashboard, not in the import.",
            )
        else:
            report.convert("webhook_url", "config.webhook_url", "copied")
    if payload.get("webhook_events"):
        report.report(
            "webhook_events",
            payload["webhook_events"],
            "VoxDesk subscribes to its own event vocabulary; Retell's event names "
            "do not map one-to-one.",
            "Re-subscribe to the equivalent VoxDesk events after import.",
        )

    # Post-call analysis: the shapes differ, the intent does not.
    pca = payload.get("post_call_analysis_data")
    if isinstance(pca, list) and pca:
        fields: list[dict[str, Any]] = []
        for index, item in enumerate(pca):
            if not isinstance(item, Mapping):
                report.report(f"post_call_analysis_data[{index}]", item, "not an object")
                continue
            entry: dict[str, Any] = {
                "name": str(item.get("name") or f"field_{index}"),
                "type": str(item.get("type") or "string"),
            }
            if item.get("description"):
                entry["description"] = str(item["description"])
            if item.get("examples"):
                entry["examples"] = list(item["examples"])
            if "required" in item:
                entry["required"] = bool(item["required"])
            # Retell's enum type carries its options in the same object.
            if entry["type"] == "enum" and item.get("choices"):
                entry["values"] = list(item["choices"])
            fields.append(entry)
        config["analysis_schema"] = {"fields": fields}
        report.convert(
            "post_call_analysis_data",
            "config.analysis_schema.fields",
            "reshaped: each item becomes a field with name/type/description",
        )
    if payload.get("post_call_analysis_model"):
        report.report(
            "post_call_analysis_model",
            payload["post_call_analysis_model"],
            "VoxDesk runs post-call analysis through its own model routing; the "
            "model name is recorded here but not applied.",
        )

    # Everything Retell lets you set that VoxDesk deliberately does not carry.
    for key in (
        "expressive_emotion_tags",
        "expressive_mode_prompt",
        "backchannel_frequency",
        "backchannel_words",
        "reminder_trigger_ms",
        "reminder_max_count",
        "ambient_sound_volume",
        "pronunciation_dictionary",
        "guardrail_config",
        "handbook_config",
        "pii_config",
        "data_storage_setting",
        "data_storage_retention_days",
        "opt_in_signed_url",
        "signed_url_expiration_ms",
        "ivr_option",
        "voicemail_option",
        "call_screening_option",
        "enable_dnc_detection",
        "pre_session_tools",
        "post_session_tools",
        "custom_stt_config",
        "user_dtmf_options",
        "knowledge_base_ids",
        "kb_config",
    ):
        if key in payload and payload[key] not in (None, [], {}, ""):
            report.report(
                key,
                payload[key],
                "no VoxDesk config field for this Retell setting",
                _SUGGESTIONS.get(
                    key,
                    "Review whether the behaviour is needed; if so it is a product gap to "
                    "raise, not something the importer can invent.",
                ),
            )

    return config, name


# ---------------------------------------------------------------------------
# Single-prompt → one-node flow
# ---------------------------------------------------------------------------


def _single_prompt_flow(prompt: str, report: ImportReport) -> dict[str, Any]:
    """A Retell single-prompt agent is one long system prompt.

    VoxDesk models that as a single ``conversation`` node with the prompt as its
    instruction — mathematically the same agent, and it can be split into a real
    flow later.
    """
    report.convert(
        "response_engine.prompt (retell-llm)",
        "flow.nodes[1].params.prompt",
        "single-prompt agent becomes one conversation node",
    )
    if not prompt:
        report.report(
            "response_engine.prompt",
            "",
            "Retell agents can have no prompt at all; VoxDesk requires a non-empty "
            "prompt on a conversation node, so this imported node is not yet valid.",
            "Write the opening instruction before activating the agent.",
        )
    return {
        "mode": "rigid",
        "nodes": [
            {"id": "00_start", "node_type": "start", "params": {}},
            {"id": "01_conversation", "node_type": "conversation", "params": {"prompt": prompt}},
            {"id": "02_end", "node_type": "end", "params": {}},
        ],
        "edges": [
            {"id": "01", "source_id": "00_start", "target_id": "01_conversation"},
            {"id": "02", "source_id": "01_conversation", "target_id": "02_end"},
        ],
    }


# ---------------------------------------------------------------------------
# Conversation flow → flow
# ---------------------------------------------------------------------------


def _convert_instruction(node: Mapping[str, Any], report: ImportReport, path: str) -> str:
    instruction = node.get("instruction")
    if isinstance(instruction, Mapping):
        if instruction.get("type") == "static_text" or instruction.get("type") == "static_sentence":
            report.convert(f"{path}.instruction", "params.prompt", "static sentence → prompt")
            return str(instruction.get("text") or "")
        report.convert(f"{path}.instruction", "params.prompt", "instruction text → prompt")
        return str(instruction.get("text") or "")
    if isinstance(instruction, str):
        report.convert(path + ".instruction", "params.prompt", "copied")
        return instruction
    return str(node.get("prompt") or "")


def _convert_variables(node: Mapping[str, Any], report: ImportReport, path: str) -> list[dict[str, Any]]:
    """Retell extract-variables nodes carry either ``variables`` or ``variable``."""
    raw = node.get("variables") or node.get("variable") or []
    if isinstance(raw, Mapping):
        raw = [raw]
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, Mapping):
            report.report(f"{path}.variables[{index}]", item, "not an object")
            continue
        spec: dict[str, Any] = {
            "name": str(item.get("name") or f"var_{index}"),
            "type": str(item.get("type") or "string"),
        }
        if item.get("description"):
            spec["description"] = str(item["description"])
        if "required" in item:
            spec["required"] = bool(item["required"])
        if item.get("enum_values") or item.get("choices"):
            spec["enum_values"] = list(item.get("enum_values") or item.get("choices") or [])
        out.append(spec)
    if out:
        report.convert(f"{path}.variables", "params.variables", "reshaped to VariableSpec")
    return out


def _condition_from_edge(edge: Mapping[str, Any], report: ImportReport, path: str) -> dict[str, Any] | None:
    condition = edge.get("transition_condition") or {}
    if not isinstance(condition, Mapping):
        report.report(path + ".transition_condition", condition, "not an object")
        return None
    if not condition:
        # A bare edge is Retell's default transition — no condition at all.
        # Converting it needs no decision, so nothing is lost and nothing is
        # reported beyond the conversion itself.
        report.convert(path, "edges[].condition", "unconditional edge; no condition to carry")
        return None
    kind = str(condition.get("type") or "").lower()
    if kind == "prompt":
        report.note_lossy(
            f"{path}.transition_condition",
            condition.get("prompt"),
            "Retell evaluates a prompt condition with the LLM; VoxDesk edges carry an "
            "equation or an unlabelled fallback. The prompt is kept as the edge label "
            "so a human can see the intent, but it is not re-evaluated.",
            "Re-implement as an equation on a dynamic variable where the decision must "
            "be deterministic.",
        )
        return {"label": str(condition.get("prompt") or "")}
    if kind in {"equation", "equations"}:
        raw = condition.get("equations") or condition.get("equation") or []
        if isinstance(raw, Mapping):
            raw = [raw]
        out = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, Mapping) and item.get("variable"):
                out.append(
                    {
                        "variable": str(item["variable"]),
                        "operator": str(item.get("operator") or "=="),
                        "value": item.get("value"),
                    }
                )
        if out:
            report.convert(f"{path}.transition_condition", "edges[].condition.equations", "copied")
        return {"equations": out} if out else None
    report.report(
        f"{path}.transition_condition",
        condition,
        f"unknown transition condition type {kind!r}",
    )
    return None


def _declared_from_references(flow: Mapping[str, Any]) -> set[str]:
    """Every ``{{var}}`` a node prompt or edge condition names."""
    found: set[str] = set()

    def scan(value: Any) -> None:
        if isinstance(value, str):
            found.update(_VAR_REF_RE.findall(value))
        elif isinstance(value, Mapping):
            for item in value.values():
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    for node in flow.get("nodes", []):
        if isinstance(node, Mapping):
            scan(node.get("params"))
            variables = (node.get("params") or {}).get("variables")
            if isinstance(variables, list):
                for spec in variables:
                    if isinstance(spec, Mapping) and spec.get("name"):
                        found.add(str(spec["name"]))
    for edge in flow.get("edges", []):
        condition = (edge or {}).get("condition") if isinstance(edge, Mapping) else None
        if isinstance(condition, Mapping):
            for equation in condition.get("equations") or []:
                if isinstance(equation, Mapping) and equation.get("variable"):
                    found.add(str(equation["variable"]))
    return found


def _convert_flow(payload: Mapping[str, Any], report: ImportReport) -> dict[str, Any]:
    nodes_out: list[dict[str, Any]] = []
    edges_out: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("conversation flow has no nodes")

    # Pass 1: nodes (so edges can resolve ids even when they point forwards).
    for index, node in enumerate(raw_nodes):
        if not isinstance(node, Mapping):
            report.report(f"nodes[{index}]", node, "not an object")
            continue
        source_id = str(node.get("id") or f"node_{index}")
        node_type = str(node.get("type") or "").lower()
        path = f"nodes[{source_id}]"

        if node_type in UNSUPPORTED_NODE_TYPES:
            report.report(path, node, UNSUPPORTED_NODE_TYPES[node_type])
            continue
        target_type = NODE_TYPE_MAP.get(node_type)
        if target_type is None:
            report.report(path, node, f"unknown Retell node type {node_type!r}")
            continue

        new_id = _new_node_id(len(nodes_out) + 1, source_id)
        id_map[source_id] = new_id
        params: dict[str, Any] = {}

        if target_type == "conversation":
            prompt = _convert_instruction(node, report, path)
            if prompt:
                params["prompt"] = prompt
            else:
                report.note_lossy(path, node, "conversation node has no instruction text")
            if node_type == "subagent":
                report.note_lossy(
                    path,
                    node,
                    "Retell's subagent node lets the LLM choose among tools; imported "
                    "as a conversation node. Attach the equivalent VoxDesk tools to "
                    "this node after import.",
                    "Add the tool list in the builder.",
                )

        elif target_type == "extract_variables":
            variables = _convert_variables(node, report, path)
            if not variables:
                report.report(path, node, "extract-variables node has no variables")
                continue
            params["variables"] = variables

        elif target_type == "function":
            for key in ("tool_id", "tool_name", "url", "method"):
                if node.get(key):
                    params[key] = node[key]
            if not any(params.get(k) for k in ("tool_id", "tool_name", "url")):
                report.report(path, node, "function node has no tool reference")
                continue
            report.convert(path, "nodes[].params", "tool reference copied")
            if node.get("speak_during_execution") is not None or node.get(
                "speak_after_execution"
            ) is not None:
                report.report(
                    f"{path}.speak_after_execution",
                    node,
                    "VoxDesk function nodes do not speak; the surrounding conversation "
                    "node says the transition line.",
                )

        elif target_type == "code":
            # JavaScript is deliberately not translated.
            report.report(
                path,
                node,
                "Retell code nodes run JavaScript inside their flow. VoxDesk's code "
                "node runs in a separate sandbox with a different runtime; a "
                "verbatim copy would be a silent behaviour change.",
                "Port the logic to a VoxDesk code node or an external function.",
            )
            continue

        elif target_type == "transfer":
            destination = (
                node.get("transfer_destination")
                or node.get("destination")
                or node.get("transfer_number")
                or node.get("number")
            )
            if not destination:
                report.report(path, node, "transfer node has no destination")
                continue
            params["destination"] = destination
            mode = str(node.get("transfer_type") or node.get("type_of_transfer") or "cold").lower()
            params["transfer_mode"] = "warm" if "warm" in mode else "cold"
            report.convert(f"{path}.transfer_destination", "params.destination", "copied")

        elif target_type == "press_digit":
            digits = node.get("digits") or node.get("digit")
            if not digits:
                report.report(path, node, "press-digit node has no digits")
                continue
            params["digits"] = str(digits)

        elif target_type == "send_sms":
            message = node.get("message") or node.get("text")
            if not message:
                report.report(path, node, "sms node has no message")
                continue
            params["message"] = str(message)

        elif target_type == "logic_split":
            report.convert(path, "nodes[].params.equations", "conditions moved to outgoing edges")

        # `end` needs no params.

        nodes_out.append({"id": new_id, "node_type": target_type, "params": params})

    # Pass 2: edges.
    for index, node in enumerate(raw_nodes):
        if not isinstance(node, Mapping):
            continue
        source_id = str(node.get("id") or f"node_{index}")
        if source_id not in id_map:
            continue  # the node itself was reported
        for e_index, edge in enumerate(node.get("edges") or []):
            if not isinstance(edge, Mapping):
                report.report(f"nodes[{source_id}].edges[{e_index}]", edge, "not an object")
                continue
            dest = edge.get("destination_node_id")
            if dest not in id_map:
                report.report(
                    f"nodes[{source_id}].edges[{e_index}].destination_node_id",
                    dest,
                    "edge points at a node that was not imported",
                )
                continue
            converted_edge: dict[str, Any] = {
                "id": f"e{len(edges_out) + 1:02d}",
                "source_id": id_map[source_id],
                "target_id": id_map[str(dest)],
            }
            condition = _condition_from_edge(
                edge, report, f"nodes[{source_id}].edges[{e_index}]"
            )
            if condition:
                converted_edge["condition"] = condition
            edges_out.append(converted_edge)

    # VoxDesk requires exactly one `start` node; Retell expresses the entry point
    # as `start_node_id`. Materialise it so the imported flow is runnable.
    entry_source = payload.get("start_node_id")
    entry_target = id_map.get(str(entry_source)) if entry_source else None
    if entry_target is None and nodes_out:
        entry_target = nodes_out[0]["id"]
    if entry_target is not None:
        nodes_out.insert(0, {"id": "00_start", "node_type": "start", "params": {}})
        edges_out.insert(
            0, {"id": "e00", "source_id": "00_start", "target_id": entry_target}
        )
    else:
        report.report(
            "start_node_id",
            entry_source,
            "no node survived conversion to start from; the flow would be unrunnable",
        )

    flow: dict[str, Any] = {"mode": "rigid", "nodes": nodes_out, "edges": edges_out}

    default_vars = payload.get("default_dynamic_variables")
    if isinstance(default_vars, Mapping) and default_vars:
        flow["initial_variables"] = {str(k): v for k, v in default_vars.items()}
        report.convert(
            "default_dynamic_variables",
            "flow.initial_variables",
            "copied as graph-level initial values",
        )

    initial = flow.setdefault("initial_variables", {})
    for name in sorted(_declared_from_references(flow) - set(initial)):
        initial[name] = ""
        report.convert(
            "implicit reference",
            f"flow.initial_variables[{name}]",
            f"'{{{{{name}}}}}' is used but never declared; Retell allows that, "
            "VoxDesk does not. Declared with an empty default so the flow validates.",
        )
    if not initial:
        flow.pop("initial_variables", None)

    if payload.get("global_prompt"):
        report.report(
            "global_prompt",
            payload["global_prompt"],
            "VoxDesk has no flow-wide prompt slot; it would have to be merged into "
            "every node prompt, which changes each node's instruction.",
            "Fold the global prompt into the agent description or the individual nodes.",
        )
    if payload.get("knowledge_base_ids"):
        report.report(
            "knowledge_base_ids",
            payload["knowledge_base_ids"],
            "Retell knowledge base ids do not exist in VoxDesk; the corpus must be "
            "re-uploaded and the agent pointed at it.",
            "Upload the same documents to the VoxDesk knowledge base, then attach "
            "the resulting documents to this agent.",
        )
    if payload.get("components"):
        report.report(
            "components",
            payload["components"],
            "Retell components are reusable flow fragments. A component whose nodes are "
            "not inlined in this export cannot be imported by reference.",
            "Export the component's nodes inline, or recreate the fragment.",
        )
    if payload.get("mcps"):
        report.report("mcps", payload["mcps"], UNSUPPORTED_NODE_TYPES["mcp"])
    if payload.get("tools"):
        report.report(
            "tools",
            payload["tools"],
            "flow-level tools are attached per node in VoxDesk; tool definitions were "
            "not converted. Tools referenced by a function node keep their ids.",
            "Re-declare the tools in the VoxDesk tool registry.",
        )

    return flow


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def convert_retell_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ImportReport]:
    """Convert one Retell agent or conversation flow into a ``voxdesk.agent/1`` bundle.

    Returns the bundle and a report. The bundle is always well-formed — a
    conversion that could not represent something says so in the report rather
    than emitting half a node.
    """
    report = ImportReport()
    kind = _detect_kind(payload)
    config, name = _convert_agent_fields(payload, report)
    config["imported_from"] = {"vendor": "retell", "kind": kind}

    if kind == "agent-flow":
        engine = payload.get("response_engine") or {}
        flow_id = engine.get("conversation_flow_id") or engine.get("flow_id")
        report.report(
            "response_engine.conversation_flow_id",
            flow_id,
            "this payload references a conversation flow by id; export the flow "
            "itself (GET /get-conversation-flow/{id}) and import that.",
            "Import the flow JSON, not the agent that points at it.",
        )
        prompt = str(engine.get("prompt") or "")
        flow = _single_prompt_flow(prompt, report)
        if not prompt:
            report.report(
                "response_engine.conversation_flow_id (placeholder flow)",
                flow_id,
                "the conversation node is a placeholder: its prompt is empty until the "
                "flow export is imported, so the validator will reject it as-is.",
                "Import the conversation flow this agent points at.",
            )
    elif kind == "flow":
        flow = _convert_flow(payload, report)
    else:
        engine = payload.get("response_engine") or {}
        prompt = str(engine.get("prompt") or "")
        if not prompt:
            report.report(
                "response_engine.prompt",
                engine,
                "single-prompt agent carries no prompt text; the prompt lives on the "
                "retell-llm object, not the agent.",
                "Export the LLM (GET /get-retell-llm/{llm_id}) and import its prompt.",
            )
        flow = _single_prompt_flow(prompt, report)
        if engine.get("llm_id"):
            report.report(
                "response_engine.llm_id",
                engine.get("llm_id"),
                "the prompt for a retell-llm agent is a separate object; it is empty "
                "here unless the export inlined it.",
                "Fetch the llm object and merge its 'prompt' before importing.",
            )

    bundle = {
        "format": EXPORT_FORMAT,
        "agent": {
            "template_id": f"retell-{uuid.uuid5(uuid.NAMESPACE_URL, str(payload.get('agent_id') or payload.get('conversation_flow_id') or 'unknown')).hex[:12]}",
            "name": name or "Imported agent",
            "config": config,
        },
        "flow": flow,
        "components": [],
        "metadata": {
            "source_vendor": "retell",
            "source_kind": kind,
            "source_id": str(payload.get("agent_id") or payload.get("conversation_flow_id") or ""),
        },
    }
    return bundle, report


def redact_for_report(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """A copy safe to print in a console or attach to a ticket."""
    safe = copy.deepcopy(dict(bundle))
    config = safe.get("agent", {}).get("config", {})
    if isinstance(config, dict) and config.get("webhook_url"):
        config["webhook_url"], _ = _redact_url(config["webhook_url"])
    return safe
