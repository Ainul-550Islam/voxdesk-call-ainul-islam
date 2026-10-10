# File: app/builder/flow_runner.py — Deterministic + LLM-judged Conversation FlowRunner state machine (Part 5 / Gate G6, closes W-12 AgentTool/WorkflowTrigger/KnowledgeCollection)
"""Runtime state machine for VoxDesk Conversation Flows (`FlowRunner`).

Responsibilities:
  - Tracks current node, dynamic variables, transition timeline (`history`), and
    executed side-effect actions (`actions`).
  - Builds per-node LLM system prompts (with `{{variable}}` interpolation and
    scoped `KnowledgeCollection` context) and per-node tool schemas (`AgentTool`).
  - Evaluates transitions after each turn:
      1. Global interrupt nodes (`is_global=True`)
      2. Deterministic `equation` edges first
      3. Natural-language `prompt` edges via an injected async judge with timeout
      4. Unconditional `always` / `default` fallback edges
  - Automatically steps through non-interactive action/routing nodes (`logic_split`,
    `extract_variables`, `function`, `press_digit`, `send_sms`) and triggers
    terminal nodes (`transfer`, `subagent`, `end`) + `WorkflowTrigger` hooks.
  - Fully testable without a live LLM or media stream.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.node import (
    EdgeCondition,
    FlowEdge,
    FlowGraph,
    FlowNode,
    VariableSpec,
)
from app.core.logging import log

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

AUTO_ADVANCE_NODE_TYPES: frozenset[str] = frozenset(
    {
        "logic_split",
        "condition",
        "extract_variables",
        "extract_variable",
        "function",
        "function_call",
        "webhook",
        "press_digit",
        "send_sms",
    }
)

TERMINAL_ACTION_NODE_TYPES: frozenset[str] = frozenset(
    {
        "end",
        "transfer",
        "transfer_human",
        "transfer_agent",
        "subagent",
    }
)


def interpolate_template(text: str, variables: Mapping[str, Any]) -> str:
    """Replace ``{{var}}`` placeholders in ``text`` with values from ``variables``."""
    if not text:
        return ""

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = variables.get(key)
        return "" if val is None else str(val)

    return _TEMPLATE_VAR_RE.sub(_repl, text)


def _default_extract_variables(
    specs: list[VariableSpec],
    utterance: str,
    existing_vars: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministic fallback variable extractor for offline/hermetic execution."""
    extracted: dict[str, Any] = {}
    text = (utterance or "").strip()
    if not text:
        for spec in specs:
            if spec.name not in existing_vars and spec.default is not None:
                extracted[spec.name] = spec.default
        return extracted

    for spec in specs:
        name = spec.name
        if not name:
            continue

        # 1. Explicit key=value or key: value in utterance
        kv_pattern = re.compile(
            rf"\b{re.escape(name)}\s*[:=]\s*([^\s,;]+)", re.IGNORECASE
        )
        kv_match = kv_pattern.search(text)
        if kv_match:
            raw_val = kv_match.group(1).strip("\"'")
            extracted[name] = _coerce_extracted_type(raw_val, spec.type)
            continue

        # 2. Custom regex pattern on VariableSpec
        if spec.pattern:
            try:
                pat_match = re.search(spec.pattern, text, re.IGNORECASE)
                if pat_match:
                    val_str = pat_match.group(1) if pat_match.lastindex else pat_match.group(0)
                    extracted[name] = _coerce_extracted_type(val_str, spec.type)
                    continue
            except re.error:
                pass

        # 3. Type-based deterministic extraction
        if spec.type == "enum" and spec.enum_values:
            lower_text = text.lower()
            for candidate in spec.enum_values:
                if candidate.lower() in lower_text:
                    extracted[name] = candidate
                    break
        elif spec.type == "number":
            num_match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
            if num_match:
                raw_num = num_match.group(0)
                extracted[name] = float(raw_num) if "." in raw_num else int(raw_num)
        elif spec.type == "boolean":
            low = text.lower()
            if any(w in low for w in ("yes", "true", "sure", "correct", "confirm", "agree")):
                extracted[name] = True
            elif any(w in low for w in ("no", "false", "cancel", "decline", "never")):
                extracted[name] = False
        else:
            # Common named entity patterns (order_id, email, phone, name)
            low_name = name.lower()
            if "email" in low_name:
                m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
                if m:
                    extracted[name] = m.group(0)
            elif "phone" in low_name or "number" in low_name:
                m = re.search(r"\+?\d[\d\-\s]{6,14}\d", text)
                if m:
                    extracted[name] = re.sub(r"[\s\-]", "", m.group(0))
            elif "id" in low_name or "order" in low_name or "account" in low_name:
                m = re.search(r"\b([A-Z0-9]{3,}[-_]?[A-Z0-9]+|\d{4,})\b", text, re.IGNORECASE)
                if m:
                    extracted[name] = m.group(1)
            else:
                extracted[name] = text

        if name not in extracted and name not in existing_vars and spec.default is not None:
            extracted[name] = spec.default

    return extracted


def _coerce_extracted_type(raw: str, var_type: str) -> Any:
    if var_type == "number":
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw
    if var_type == "boolean":
        return raw.strip().lower() in {"true", "1", "yes", "y"}
    return raw


@dataclass
class NodeTransitionEvent:
    """Recorded transition between two nodes in a conversation flow."""

    from_node_id: str
    to_node_id: str
    to_node_type: str
    edge_id: str | None
    reason: str
    turn_index: int
    variables_snapshot: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "to_node_type": self.to_node_type,
            "edge_id": self.edge_id,
            "reason": self.reason,
            "turn_index": self.turn_index,
            "variables_snapshot": dict(self.variables_snapshot),
            "timestamp": self.timestamp,
        }


@dataclass
class NodeActionResult:
    """Result of executing an action node (`function`, `transfer`, `press_digit`, `send_sms`, etc.)."""

    action_type: str
    node_id: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "node_id": self.node_id,
            "ok": self.ok,
            "output": dict(self.output),
            "timestamp": self.timestamp,
        }


@dataclass
class FlowTurnResult:
    """Result returned by `FlowRunner.start()` or `FlowRunner.step()`."""

    previous_node_id: str
    current_node_id: str
    current_node_type: str
    transitioned: bool
    transitions: list[NodeTransitionEvent] = field(default_factory=list)
    actions: list[NodeActionResult] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    model_override: dict[str, Any] | None = None
    voice_override: dict[str, Any] | None = None
    speak_text: str | None = None
    ended: bool = False
    transferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_node_id": self.previous_node_id,
            "current_node_id": self.current_node_id,
            "current_node_type": self.current_node_type,
            "transitioned": self.transitioned,
            "transitions": [t.to_dict() for t in self.transitions],
            "actions": [a.to_dict() for a in self.actions],
            "variables": dict(self.variables),
            "system_prompt": self.system_prompt,
            "tools": list(self.tools),
            "model_override": dict(self.model_override) if self.model_override else None,
            "voice_override": dict(self.voice_override) if self.voice_override else None,
            "speak_text": self.speak_text,
            "ended": self.ended,
            "transferred": self.transferred,
        }


class FlowRunner:
    """Deterministic + judge-driven conversation flow engine."""

    def __init__(
        self,
        graph: FlowGraph | Mapping[str, Any],
        *,
        base_system_prompt: str = "",
        available_tools: list[dict[str, Any]] | None = None,
        knowledge_collections: list[dict[str, Any]] | None = None,
        workflow_triggers: list[dict[str, Any]] | None = None,
        initial_variables: Mapping[str, Any] | None = None,
        judge: Callable[..., Awaitable[bool] | bool] | None = None,
        judge_timeout_seconds: float = 2.0,
        judge_timeout_s: float | None = None,
        variable_extractor: Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        function_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        transfer_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        dtmf_handler: Callable[[str, int], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        sms_handler: Callable[[str, str | None], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        subagent_handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        workflow_trigger_handler: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        max_auto_steps: int = 10,
    ) -> None:
        self.graph: FlowGraph = (
            graph if isinstance(graph, FlowGraph) else FlowGraph.from_dict(graph)
        )
        self.base_system_prompt = base_system_prompt
        self.available_tools: list[dict[str, Any]] = list(available_tools or [])
        self.knowledge_collections: list[dict[str, Any]] = list(knowledge_collections or [])
        self.workflow_triggers: list[dict[str, Any]] = list(workflow_triggers or [])

        self.judge = judge
        effective_timeout = judge_timeout_s if judge_timeout_s is not None else judge_timeout_seconds
        self.judge_timeout_seconds = max(0.001, float(effective_timeout))
        self.variable_extractor = variable_extractor
        self.function_handler = function_handler
        self.transfer_handler = transfer_handler
        self.dtmf_handler = dtmf_handler
        self.sms_handler = sms_handler
        self.subagent_handler = subagent_handler
        self.workflow_trigger_handler = workflow_trigger_handler
        self.max_auto_steps = max(1, int(max_auto_steps))

        self.variables: dict[str, Any] = {}
        for spec in self.graph.variables:
            if spec.default is not None:
                self.variables[spec.name] = spec.default
        self.variables.update(self.graph.initial_variables)
        if initial_variables:
            self.variables.update(initial_variables)
        self.variables.setdefault("turn_count", 0)
        self.variables.setdefault("last_user_utterance", "")

        self._node_map: dict[str, FlowNode] = self.graph.node_map()
        start_nodes = self.graph.start_nodes()
        if start_nodes:
            self.current_node_id: str = start_nodes[0].node_id
        elif self.graph.nodes:
            self.current_node_id = self.graph.nodes[0].node_id
        else:
            raise ValueError("FlowGraph must contain at least one node")

        self._previous_non_global_node_id: str | None = None
        self.turn_index: int = 0
        self.history: list[NodeTransitionEvent] = []
        self.executed_actions: list[NodeActionResult] = []
        self.ended: bool = False
        self.transferred: bool = False
        self._started: bool = False

    @property
    def current_node(self) -> FlowNode:
        return self._node_map[self.current_node_id]

    def build_llm_system_prompt(self, node: FlowNode | None = None) -> str:
        """Assemble the active system prompt for ``node`` with interpolated variables."""
        target = node or self.current_node
        parts: list[str] = []
        if self.base_system_prompt.strip():
            parts.append(interpolate_template(self.base_system_prompt.strip(), self.variables))

        node_prompt = interpolate_template(target.prompt.strip(), self.variables)
        if node_prompt:
            parts.append(f"[Current Flow Node: {target.label} ({target.node_id})]\n{node_prompt}")

        # Attach scoped KnowledgeCollection summaries when bound
        node_kb_ids = set(target.knowledge_collection_ids)
        matched_kbs = [
            kb
            for kb in self.knowledge_collections
            if not node_kb_ids or str(kb.get("id")) in node_kb_ids or str(kb.get("name")) in node_kb_ids
        ]
        if matched_kbs:
            kb_names = ", ".join(str(kb.get("name") or kb.get("id")) for kb in matched_kbs)
            parts.append(f"[Active Knowledge Collections: {kb_names}]")

        return "\n\n".join(parts).strip()

    def build_llm_tools(self, node: FlowNode | None = None) -> list[dict[str, Any]]:
        """Build the OpenAI-format tool list scoped to ``node``."""
        target = node or self.current_node
        requested_names = target.tools
        by_name: dict[str, dict[str, Any]] = {}
        for tool in self.available_tools:
            if "function" in tool and isinstance(tool["function"], Mapping):
                t_name = str(tool["function"].get("name") or "")
                if t_name:
                    by_name[t_name] = dict(tool)
            elif tool.get("name"):
                t_name = str(tool["name"])
                schema_obj = tool.get("schema") or tool.get("input_schema") or {
                    "type": "object",
                    "properties": {},
                }
                by_name[t_name] = {
                    "type": "function",
                    "function": {
                        "name": t_name,
                        "description": str(tool.get("description") or f"Execute tool {t_name}"),
                        "parameters": schema_obj
                        if isinstance(schema_obj, Mapping) and "type" in schema_obj
                        else {"type": "object", "properties": {}},
                    },
                }

        if not requested_names:
            return list(by_name.values())

        scoped: list[dict[str, Any]] = []
        for name in requested_names:
            if name in by_name:
                scoped.append(by_name[name])
            else:
                scoped.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": f"Flow node tool '{name}'",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                )
        return scoped

    def build_llm_messages(
        self,
        conversation_history: list[dict[str, Any]] | None = None,
        *,
        node: FlowNode | None = None,
    ) -> list[dict[str, Any]]:
        """Build the full LLM message array for the current node."""
        sys_prompt = self.build_llm_system_prompt(node)
        messages: list[dict[str, Any]] = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        if conversation_history:
            for msg in conversation_history:
                if msg.get("role") != "system":
                    messages.append(dict(msg))
        return messages

    def current_model_override(self, node: FlowNode | None = None) -> dict[str, Any] | None:
        target = node or self.current_node
        if target.model_override is not None and not target.model_override.is_empty():
            return target.model_override.to_dict()
        if self.graph.default_model is not None and not self.graph.default_model.is_empty():
            return self.graph.default_model.to_dict()
        return None

    def current_voice_override(self, node: FlowNode | None = None) -> dict[str, Any] | None:
        target = node or self.current_node
        if target.voice_override is not None and not target.voice_override.is_empty():
            return target.voice_override.to_dict()
        if self.graph.default_voice is not None and not self.graph.default_voice.is_empty():
            return self.graph.default_voice.to_dict()
        return None

    async def _call_maybe_async(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        res = fn(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res

    async def _evaluate_prompt_condition(self, prompt_condition: str, utterance: str) -> bool:
        """Evaluate a natural-language edge/global condition using the injected judge with timeout."""
        interpolated_cond = interpolate_template(prompt_condition or "", self.variables).strip()
        if not interpolated_cond:
            return False

        if self.judge is not None:
            try:
                sig = inspect.signature(self.judge)
                param_count = len(sig.parameters)
            except (ValueError, TypeError):
                param_count = 3

            async def _invoke() -> bool:
                if param_count <= 1:
                    out = await self._call_maybe_async(self.judge, interpolated_cond)
                elif param_count == 2:
                    out = await self._call_maybe_async(self.judge, interpolated_cond, utterance)
                else:
                    out = await self._call_maybe_async(
                        self.judge, interpolated_cond, utterance, dict(self.variables)
                    )
                return bool(out)

            try:
                return await asyncio.wait_for(_invoke(), timeout=self.judge_timeout_seconds)
            except asyncio.TimeoutError:
                log.warning(
                    "flow_runner.judge_timeout",
                    node_id=self.current_node_id,
                    timeout_s=self.judge_timeout_seconds,
                )
                return False
            except Exception as exc:
                log.warning(
                    "flow_runner.judge_error",
                    node_id=self.current_node_id,
                    error=str(exc)[:160],
                )
                return False

        # Deterministic keyword fallback when no external judge is provided
        if not utterance.strip():
            return False
        u_low = utterance.lower()
        c_low = interpolated_cond.lower()
        if c_low in u_low:
            return True
        tokens = [
            tok
            for tok in re.findall(r"[a-zA-Z0-9]+", c_low)
            if len(tok) >= 4
            and tok not in {"user", "caller", "wants", "asks", "about", "when", "that", "with", "from"}
        ]
        return bool(tokens) and any(tok in u_low for tok in tokens)

    async def _select_outgoing_edge(
        self, node: FlowNode, utterance: str
    ) -> tuple[FlowEdge | None, str]:
        """Evaluate outgoing edges from ``node``: equations first, then prompt judge, then always/default."""
        edges = self.graph.outgoing_edges(node.node_id)
        if not edges:
            return None, ""

        equation_edges: list[FlowEdge] = []
        prompt_edges: list[FlowEdge] = []
        fallback_edges: list[FlowEdge] = []

        for edge in edges:
            cond = edge.condition or EdgeCondition(kind="always")
            if cond.kind == "equation":
                equation_edges.append(edge)
            elif cond.kind == "prompt":
                prompt_edges.append(edge)
            else:
                fallback_edges.append(edge)

        # 1. Deterministic equations first
        for edge in equation_edges:
            if edge.condition and edge.condition.evaluate_deterministic(self.variables) is True:
                return edge, "equation"

        # 2. Prompt conditions via injected judge
        for edge in prompt_edges:
            prompt_str = (edge.condition.prompt if edge.condition else None) or edge.expression or edge.condition_label
            if prompt_str and await self._evaluate_prompt_condition(prompt_str, utterance):
                return edge, "prompt_judge"

        # 3. Default / always edges
        if fallback_edges:
            chosen = fallback_edges[0]
            reason = "default" if (chosen.condition and chosen.condition.kind == "default") else "always"
            return chosen, reason

        return None, ""

    async def _check_global_preemption(self, utterance: str) -> FlowNode | None:
        """Check if any global node is triggered by the current turn."""
        if not utterance.strip() and self.turn_index == 0:
            return None

        for g_node in self.graph.global_nodes():
            if g_node.node_id == self.current_node_id:
                continue
            gcfg = g_node.global_config
            if gcfg is None or not gcfg.enabled:
                continue
            if gcfg.condition_type == "equation":
                if gcfg.equations and all(eq.evaluate(self.variables) for eq in gcfg.equations):
                    return g_node
            elif gcfg.condition_type == "prompt" and gcfg.prompt:
                if await self._evaluate_prompt_condition(gcfg.prompt, utterance):
                    return g_node
        return None

    async def _extract_node_variables(self, node: FlowNode, utterance: str) -> dict[str, Any]:
        """Extract declared variables for ``extract_variables`` or ``conversation`` nodes."""
        static_updates: dict[str, Any] = {}
        raw_set_vars = node.params.get("set_variables")
        if isinstance(raw_set_vars, Mapping):
            for k, v in raw_set_vars.items():
                static_updates[str(k)] = (
                    interpolate_template(v, self.variables) if isinstance(v, str) else v
                )
            self.variables.update(static_updates)

        raw_vars = node.params.get("variables") or node.params.get("extract_variables") or []
        specs: list[VariableSpec] = []
        if isinstance(raw_vars, list):
            specs = [VariableSpec.from_dict(v) for v in raw_vars if v]
        elif isinstance(raw_vars, Mapping):
            specs = [
                VariableSpec(
                    name=str(k),
                    type=str(v.get("type", "string")) if isinstance(v, Mapping) else "string",
                    description=str(v.get("description", "")) if isinstance(v, Mapping) else str(v),
                )
                for k, v in raw_vars.items()
            ]
        if not specs:
            return static_updates

        if self.variable_extractor is not None:
            try:
                extracted = await self._call_maybe_async(
                    self.variable_extractor, specs, utterance, dict(self.variables)
                )
                if isinstance(extracted, Mapping):
                    merged = {**static_updates, **dict(extracted)}
                    self.variables.update(merged)
                    return merged
            except Exception as exc:
                log.warning("flow_runner.variable_extractor_error", error=str(exc)[:160])

        extracted = _default_extract_variables(specs, utterance, self.variables)
        merged = {**static_updates, **extracted}
        self.variables.update(merged)
        return merged

    async def _execute_node_action(
        self, node: FlowNode, utterance: str
    ) -> tuple[NodeActionResult | None, str | None]:
        """Execute side effects for action/terminal nodes and return `(action_result, speak_text)`."""
        ntype = node.node_type
        speak_text: str | None = None
        if node.params.get("speak_text"):
            speak_text = interpolate_template(str(node.params["speak_text"]), self.variables)

        if ntype in {"extract_variables", "extract_variable"}:
            updates = await self._extract_node_variables(node, utterance)
            res = NodeActionResult(
                action_type="extract_variables",
                node_id=node.node_id,
                ok=True,
                output={"extracted": updates},
            )
            self.executed_actions.append(res)
            return res, speak_text

        if ntype in {"function", "function_call", "webhook"}:
            tool_name = str(
                node.params.get("tool_name")
                or node.params.get("tool_id")
                or node.params.get("url")
                or "http_tool"
            )
            raw_args = node.params.get("arguments") or node.params.get("payload") or {}
            args: dict[str, Any] = {}
            if isinstance(raw_args, Mapping):
                for k, v in raw_args.items():
                    args[str(k)] = (
                        interpolate_template(v, self.variables) if isinstance(v, str) else v
                    )
            else:
                args = dict(self.variables)

            output: dict[str, Any] = {"ok": True, "tool_name": tool_name}
            ok = True
            if self.function_handler is not None:
                try:
                    handler_out = await self._call_maybe_async(
                        self.function_handler, tool_name, args
                    )
                    if isinstance(handler_out, Mapping):
                        output.update(handler_out)
                        ok = bool(output.get("ok", True))
                except Exception as exc:
                    __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
                    ok = False
                    output = {"ok": False, "error": str(exc)}

            self.variables["tool_ok"] = ok
            self.variables["tool_result"] = output
            res_var = node.params.get("result_variable") or node.params.get("output_variable")
            if res_var and str(res_var).strip():
                self.variables[str(res_var).strip()] = output.get("result", output)

            resp_map = node.params.get("response_mapping")
            if isinstance(resp_map, Mapping):
                for target_var, src_key in resp_map.items():
                    if str(src_key) in output:
                        self.variables[str(target_var)] = output[str(src_key)]
                    elif isinstance(output.get("result"), Mapping) and str(src_key) in output["result"]:
                        self.variables[str(target_var)] = output["result"][str(src_key)]

            res = NodeActionResult(
                action_type="function",
                node_id=node.node_id,
                ok=ok,
                output=output,
            )
            self.executed_actions.append(res)
            return res, speak_text

        if ntype == "press_digit":
            digits = interpolate_template(str(node.params.get("digits") or ""), self.variables)
            pause_ms = int(node.params.get("pause_ms", 250))
            output = {"digits": digits, "pause_ms": pause_ms, "ok": True}
            if self.dtmf_handler is not None:
                handler_out = await self._call_maybe_async(self.dtmf_handler, digits, pause_ms)
                if isinstance(handler_out, Mapping):
                    output.update(handler_out)
            res = NodeActionResult(
                action_type="press_digit",
                node_id=node.node_id,
                ok=bool(output.get("ok", True)),
                output=output,
            )
            self.executed_actions.append(res)
            return res, speak_text

        if ntype == "send_sms":
            msg = interpolate_template(
                str(node.params.get("message") or node.params.get("text") or ""),
                self.variables,
            )
            to_num_raw = node.params.get("to_number") or self.variables.get("caller_number") or self.variables.get("from_number")
            to_num = interpolate_template(str(to_num_raw), self.variables) if to_num_raw else None
            output = {"message": msg, "to_number": to_num, "ok": True}
            if self.sms_handler is not None:
                handler_out = await self._call_maybe_async(self.sms_handler, msg, to_num)
                if isinstance(handler_out, Mapping):
                    output.update(handler_out)
            res = NodeActionResult(
                action_type="send_sms",
                node_id=node.node_id,
                ok=bool(output.get("ok", True)),
                output=output,
            )
            self.executed_actions.append(res)
            return res, speak_text

        if ntype in {"transfer", "transfer_human", "transfer_agent"}:
            dest = interpolate_template(
                str(
                    node.params.get("destination")
                    or node.params.get("target_number")
                    or node.params.get("target_agent_id")
                    or ""
                ),
                self.variables,
            )
            t_mode = str(node.params.get("transfer_mode") or node.params.get("mode") or "cold").lower()
            whisper = interpolate_template(
                str(node.params.get("whisper_text") or ""), self.variables
            )
            transfer_payload = {
                "destination": dest,
                "transfer_mode": t_mode,
                "whisper_text": whisper or None,
                "node_id": node.node_id,
            }
            output = {**transfer_payload, "ok": True}
            if self.transfer_handler is not None:
                handler_out = await self._call_maybe_async(
                    self.transfer_handler, transfer_payload
                )
                if isinstance(handler_out, Mapping):
                    output.update(handler_out)
            self.transferred = True
            if bool(node.params.get("terminal", True)):
                self.ended = True
            res = NodeActionResult(
                action_type="transfer",
                node_id=node.node_id,
                ok=bool(output.get("ok", True)),
                output=output,
            )
            self.executed_actions.append(res)
            return res, speak_text

        if ntype == "subagent":
            target_agent = str(
                node.params.get("agent_id") or node.params.get("target_agent_id") or ""
            )
            sub_payload = {
                "agent_id": target_agent,
                "version": node.params.get("version"),
                "variables": dict(self.variables) if node.params.get("pass_context", True) else {},
            }
            output = {**sub_payload, "ok": True}
            if self.subagent_handler is not None:
                handler_out = await self._call_maybe_async(
                    self.subagent_handler, target_agent, sub_payload
                )
                if isinstance(handler_out, Mapping):
                    output.update(handler_out)
            self.variables["active_subagent_id"] = target_agent
            if bool(node.params.get("terminal", True)) and not self.graph.outgoing_edges(node.node_id):
                self.ended = True
            res = NodeActionResult(
                action_type="subagent",
                node_id=node.node_id,
                ok=bool(output.get("ok", True)),
                output=output,
            )
            self.executed_actions.append(res)
            return res, speak_text

        if ntype == "end":
            self.ended = True
            reason = str(node.params.get("reason") or "flow_ended")
            output = {"reason": reason, "ok": True}
            # Fire any bound WorkflowTrigger rows (W-12)
            triggered_ids: list[str] = []
            for trig in self.workflow_triggers:
                if not trig.get("is_enabled", True):
                    continue
                ev_type = str(trig.get("event_type") or "")
                if ev_type in {"flow_completed", "call_ended", "*"}:
                    triggered_ids.append(str(trig.get("workflow_id") or trig.get("id")))
                    if self.workflow_trigger_handler is not None:
                        await self._call_maybe_async(
                            self.workflow_trigger_handler, trig, dict(self.variables)
                        )
            if triggered_ids:
                output["triggered_workflows"] = triggered_ids
            res = NodeActionResult(
                action_type="end",
                node_id=node.node_id,
                ok=True,
                output=output,
            )
            self.executed_actions.append(res)
            return res, speak_text

        return None, speak_text

    async def _advance_auto_nodes(
        self,
        utterance: str,
        turn_transitions: list[NodeTransitionEvent],
        turn_actions: list[NodeActionResult],
    ) -> str | None:
        """Keep stepping through auto-advance nodes (`logic_split`, `function`, etc.) up to `max_auto_steps`."""
        last_speak_text: str | None = None
        steps = 0

        while steps < self.max_auto_steps and not self.ended:
            steps += 1
            curr = self.current_node

            # Execute action if current node is an action or terminal node
            action_res, speak = await self._execute_node_action(curr, utterance)
            if action_res is not None:
                turn_actions.append(action_res)
            if speak:
                last_speak_text = speak

            if self.ended:
                break

            # Interactive nodes (`conversation`, `prompt`, `llm_response`, `wait_for_input`)
            # wait for the user's next turn after being entered.
            if curr.node_type not in AUTO_ADVANCE_NODE_TYPES and curr.node_type != "start":
                break

            edge, reason = await self._select_outgoing_edge(curr, utterance)
            if edge is None or edge.target_id not in self._node_map:
                break

            next_node = self._node_map[edge.target_id]
            event = NodeTransitionEvent(
                from_node_id=curr.node_id,
                to_node_id=next_node.node_id,
                to_node_type=next_node.node_type,
                edge_id=edge.edge_id,
                reason=reason,
                turn_index=self.turn_index,
                variables_snapshot=dict(self.variables),
            )
            self.current_node_id = next_node.node_id
            self.history.append(event)
            turn_transitions.append(event)

            # If we landed on a conversation node that has a greeting/speak_text, capture it
            if next_node.node_type in {"conversation", "prompt", "llm_response"}:
                if next_node.params.get("speak_text") or next_node.params.get("greeting"):
                    last_speak_text = interpolate_template(
                        str(next_node.params.get("speak_text") or next_node.params.get("greeting")),
                        self.variables,
                    )
                break

        return last_speak_text

    async def start(self) -> FlowTurnResult:
        """Initialize the flow at the `start` node and advance to the first active node."""
        self._started = True
        prev_id = self.current_node_id
        turn_transitions: list[NodeTransitionEvent] = []
        turn_actions: list[NodeActionResult] = []

        start_node = self.current_node
        speak_text: str | None = None
        if start_node.params.get("greeting") or start_node.params.get("speak_text"):
            speak_text = interpolate_template(
                str(start_node.params.get("greeting") or start_node.params.get("speak_text")),
                self.variables,
            )

        auto_speak = await self._advance_auto_nodes("", turn_transitions, turn_actions)
        if auto_speak:
            speak_text = auto_speak

        return FlowTurnResult(
            previous_node_id=prev_id,
            current_node_id=self.current_node_id,
            current_node_type=self.current_node.node_type,
            transitioned=len(turn_transitions) > 0,
            transitions=turn_transitions,
            actions=turn_actions,
            variables=dict(self.variables),
            system_prompt=self.build_llm_system_prompt(),
            tools=self.build_llm_tools(),
            model_override=self.current_model_override(),
            voice_override=self.current_voice_override(),
            speak_text=speak_text,
            ended=self.ended,
            transferred=self.transferred,
        )

    async def step(
        self,
        user_utterance: str = "",
        *,
        extracted_updates: Mapping[str, Any] | None = None,
    ) -> FlowTurnResult:
        """Process one user turn, evaluate edges, run action nodes, and return the new state."""
        if not self._started:
            await self.start()

        prev_id = self.current_node_id
        self.turn_index += 1
        self.variables["turn_count"] = self.turn_index
        self.variables["last_user_utterance"] = user_utterance

        if extracted_updates:
            self.variables.update(extracted_updates)

        turn_transitions: list[NodeTransitionEvent] = []
        turn_actions: list[NodeActionResult] = []

        if self.ended:
            return FlowTurnResult(
                previous_node_id=prev_id,
                current_node_id=self.current_node_id,
                current_node_type=self.current_node.node_type,
                transitioned=False,
                transitions=[],
                actions=[],
                variables=dict(self.variables),
                system_prompt=self.build_llm_system_prompt(),
                tools=self.build_llm_tools(),
                model_override=self.current_model_override(),
                voice_override=self.current_voice_override(),
                ended=True,
                transferred=self.transferred,
            )

        curr = self.current_node

        # Extract variables declared directly on the active conversation node
        if curr.node_type in {"conversation", "extract_variables", "extract_variable"}:
            await self._extract_node_variables(curr, user_utterance)

        # 1. Check global node preemption first
        global_target = await self._check_global_preemption(user_utterance)
        if global_target is not None:
            if not curr.is_global:
                self._previous_non_global_node_id = curr.node_id
            ev = NodeTransitionEvent(
                from_node_id=curr.node_id,
                to_node_id=global_target.node_id,
                to_node_type=global_target.node_type,
                edge_id=None,
                reason="global_preempt",
                turn_index=self.turn_index,
                variables_snapshot=dict(self.variables),
            )
            self.current_node_id = global_target.node_id
            self.history.append(ev)
            turn_transitions.append(ev)
        else:
            # 2. Evaluate outgoing edges from current node
            edge, reason = await self._select_outgoing_edge(curr, user_utterance)
            if edge is not None and edge.target_id in self._node_map:
                next_node = self._node_map[edge.target_id]
                ev = NodeTransitionEvent(
                    from_node_id=curr.node_id,
                    to_node_id=next_node.node_id,
                    to_node_type=next_node.node_type,
                    edge_id=edge.edge_id,
                    reason=reason,
                    turn_index=self.turn_index,
                    variables_snapshot=dict(self.variables),
                )
                self.current_node_id = next_node.node_id
                self.history.append(ev)
                turn_transitions.append(ev)
            elif (
                curr.is_global
                and curr.global_config is not None
                and curr.global_config.return_to_previous
                and self._previous_non_global_node_id
                and self._previous_non_global_node_id in self._node_map
            ):
                ret_node = self._node_map[self._previous_non_global_node_id]
                ev = NodeTransitionEvent(
                    from_node_id=curr.node_id,
                    to_node_id=ret_node.node_id,
                    to_node_type=ret_node.node_type,
                    edge_id=None,
                    reason="return_to_previous",
                    turn_index=self.turn_index,
                    variables_snapshot=dict(self.variables),
                )
                self.current_node_id = ret_node.node_id
                self._previous_non_global_node_id = None
                self.history.append(ev)
                turn_transitions.append(ev)

        speak_text: str | None = None
        if turn_transitions:
            speak_text = await self._advance_auto_nodes(
                user_utterance, turn_transitions, turn_actions
            )

        return FlowTurnResult(
            previous_node_id=prev_id,
            current_node_id=self.current_node_id,
            current_node_type=self.current_node.node_type,
            transitioned=len(turn_transitions) > 0,
            transitions=turn_transitions,
            actions=turn_actions,
            variables=dict(self.variables),
            system_prompt=self.build_llm_system_prompt(),
            tools=self.build_llm_tools(),
            model_override=self.current_model_override(),
            voice_override=self.current_voice_override(),
            speak_text=speak_text,
            ended=self.ended,
            transferred=self.transferred,
        )


async def load_agent_flow_bindings(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str | uuid.UUID,
) -> dict[str, list[dict[str, Any]]]:
    """Load durable `AgentTool`, `KnowledgeCollection`, and `WorkflowTrigger` rows (`W-12`)."""
    from app.db.enterprise_models import (
        AgentTool,
        KnowledgeCollection,
        WorkflowTrigger,
    )

    agent_id_str = str(agent_id)

    tool_rows = (
        await session.execute(
            select(AgentTool).where(
                AgentTool.tenant_id == tenant_id,
                AgentTool.agent_id == agent_id_str,
                AgentTool.is_enabled.is_(True),
            )
        )
    ).scalars().all()

    kb_rows = (
        await session.execute(
            select(KnowledgeCollection).where(
                KnowledgeCollection.tenant_id == tenant_id,
                KnowledgeCollection.is_active.is_(True),
            )
        )
    ).scalars().all()
    matched_kbs = [
        kb.as_dict()
        for kb in kb_rows
        if not kb.agent_ids or agent_id_str in [str(x) for x in kb.agent_ids]
    ]

    trigger_rows = (
        await session.execute(
            select(WorkflowTrigger).where(
                WorkflowTrigger.tenant_id == tenant_id,
                WorkflowTrigger.is_enabled.is_(True),
            )
        )
    ).scalars().all()

    return {
        "tools": [
            {
                **t.as_dict(),
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or f"Execute tool {t.name}",
                    "parameters": t.schema
                    if isinstance(t.schema, Mapping) and "type" in t.schema
                    else {"type": "object", "properties": {}},
                },
            }
            for t in tool_rows
        ],
        "knowledge_collections": matched_kbs,
        "workflow_triggers": [tr.as_dict() for tr in trigger_rows],
    }
