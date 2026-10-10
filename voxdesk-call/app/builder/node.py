# File: app/builder/node.py — Conversation-flow node/edge contracts, global nodes, model/voice overrides, equation evaluator, and JSON-Schema export (Part 5 / Gate G6)
"""Visual conversation-flow and workflow builder node/edge data contracts.

Defines:
- First-class conversation-flow node types (`start`, `conversation`, `logic_split`,
  `function`, `transfer`, `press_digit`, `send_sms`, `extract_variables`, `end`,
  `subagent`) plus legacy workflow aliases (`function_call`, `transfer_agent`, etc.).
- Structured edge conditions (`always`, `equation`, `prompt`, `default`) with
  deterministic equation evaluation (`==`, `!=`, `>`, `>=`, `<`, `<=`, `contains`,
  `not_contains`, `exists`, `not_exists`, `in`, `matches`).
- Global interrupt nodes (`is_global` + `GlobalNodeConfig`).
- Per-node `ModelOverride` and `VoiceOverride` settings.
- Canonical JSON Schema export (`export_flow_json_schema`) mirrored by the
  Next.js React Flow editor (`dashboard-next/lib/flow-schema.ts`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

FLOW_NODE_TYPES: frozenset[str] = frozenset(
    {
        "start",
        "conversation",
        "logic_split",
        "function",
        "transfer",
        "press_digit",
        "send_sms",
        "extract_variables",
        "end",
        "subagent",
    }
)

LEGACY_WORKFLOW_NODE_TYPES: frozenset[str] = frozenset(
    {
        "prompt",
        "llm_response",
        "function_call",
        "condition",
        "transfer_agent",
        "transfer_human",
        "extract_variable",
        "webhook",
        "wait_for_input",
    }
)

SUPPORTED_NODE_TYPES: frozenset[str] = FLOW_NODE_TYPES | LEGACY_WORKFLOW_NODE_TYPES

EQUATION_OPERATORS: frozenset[str] = frozenset(
    {
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        "exists",
        "not_exists",
        "is_set",
        "is_empty",
        "in",
        "not_in",
        "matches",
    }
)

EDGE_CONDITION_KINDS: frozenset[str] = frozenset(
    {"always", "equation", "prompt", "default", "else"}
)

TRANSFER_MODES: frozenset[str] = frozenset({"cold", "warm"})

VARIABLE_TYPES: frozenset[str] = frozenset({"string", "number", "boolean", "enum"})

_SIMPLE_EXPR_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*"
    r"(==|!=|>=|<=|>|<|contains|not_contains|exists|not_exists|matches)\s*"
    r"(.*?)\s*$"
)


def _strip_quotes(raw: str) -> Any:
    val = raw.strip()
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in {"null", "none"}:
        return None
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def _coerce_numeric(lhs: Any, rhs: Any) -> tuple[float, float] | None:
    try:
        if isinstance(lhs, bool) or isinstance(rhs, bool):
            return None
        return float(lhs), float(rhs)
    except (TypeError, ValueError):
        return None


@dataclass
class EquationClause:
    """Single deterministic comparison on a dynamic variable."""

    variable: str
    operator: str = "=="
    value: Any = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.variable or not str(self.variable).strip():
            errors.append("equation variable must not be empty")
        if self.operator not in EQUATION_OPERATORS:
            errors.append(
                f"equation operator '{self.operator}' must be one of {sorted(EQUATION_OPERATORS)}"
            )
        if self.operator not in {"exists", "not_exists", "is_set", "is_empty"} and self.value is None:
            errors.append(
                f"equation on variable '{self.variable}' with operator '{self.operator}' requires a value"
            )
        return errors

    def evaluate(self, variables: Mapping[str, Any]) -> bool:
        """Deterministically evaluate this clause against runtime ``variables``."""
        exists = self.variable in variables and variables[self.variable] is not None and variables[self.variable] != ""
        if self.operator in {"exists", "is_set"}:
            return exists
        if self.operator in {"not_exists", "is_empty"}:
            return not exists

        actual = variables.get(self.variable)
        expected = self.value

        if self.operator == "==":
            num_pair = _coerce_numeric(actual, expected)
            if num_pair is not None:
                return num_pair[0] == num_pair[1]
            if isinstance(actual, bool) or isinstance(expected, bool):
                return bool(actual) is bool(expected) if isinstance(actual, bool) and isinstance(expected, bool) else str(actual).lower() == str(expected).lower()
            return str(actual or "").strip().lower() == str(expected or "").strip().lower()

        if self.operator == "!=":
            return not EquationClause(self.variable, "==", self.value).evaluate(variables)

        if self.operator in {">", ">=", "<", "<="}:
            num_pair = _coerce_numeric(actual, expected)
            if num_pair is None:
                return False
            a_num, b_num = num_pair
            if self.operator == ">":
                return a_num > b_num
            if self.operator == ">=":
                return a_num >= b_num
            if self.operator == "<":
                return a_num < b_num
            return a_num <= b_num

        if self.operator == "contains":
            if actual is None:
                return False
            if isinstance(actual, (list, tuple, set)):
                return expected in actual or str(expected).lower() in {str(x).lower() for x in actual}
            return str(expected or "").lower() in str(actual).lower()

        if self.operator == "not_contains":
            return not EquationClause(self.variable, "contains", self.value).evaluate(variables)

        if self.operator == "starts_with":
            if actual is None:
                return False
            return str(actual).lower().startswith(str(expected or "").lower())

        if self.operator == "ends_with":
            if actual is None:
                return False
            return str(actual).lower().endswith(str(expected or "").lower())

        if self.operator == "in":
            if isinstance(expected, (list, tuple, set)):
                return actual in expected or str(actual).lower() in {str(x).lower() for x in expected}
            if isinstance(expected, str):
                items = [x.strip().lower() for x in expected.split(",") if x.strip()]
                return str(actual or "").strip().lower() in items
            return False

        if self.operator == "not_in":
            return not EquationClause(self.variable, "in", self.value).evaluate(variables)

        if self.operator == "matches":
            if actual is None or expected is None:
                return False
            try:
                return bool(re.search(str(expected), str(actual), re.IGNORECASE))
            except re.error:
                return False

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "operator": self.operator,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EquationClause":
        return cls(
            variable=str(data.get("variable") or "").strip(),
            operator=str(data.get("operator") or "==").strip(),
            value=data.get("value"),
        )

    @classmethod
    def parse_expression(cls, expr: str) -> list["EquationClause"]:
        """Parse a simple expression string like ``intent == 'billing' && amount > 100``."""
        clauses: list[EquationClause] = []
        if not expr or not expr.strip():
            return clauses
        parts = re.split(r"\s+(?:&&|AND|and)\s+", expr.strip())
        for part in parts:
            m = _SIMPLE_EXPR_RE.match(part)
            if not m:
                continue
            var_name, op, rhs_raw = m.group(1), m.group(2), m.group(3)
            val = None if op in {"exists", "not_exists"} else _strip_quotes(rhs_raw)
            clauses.append(cls(variable=var_name, operator=op, value=val))
        return clauses


@dataclass
class EdgeCondition:
    """Structured condition on a FlowEdge (`always`, `equation`, `prompt`, `default`)."""

    kind: str = "always"
    equations: list[EquationClause] = field(default_factory=list)
    match_mode: str = "all"  # "all" (AND) | "any" (OR)
    prompt: str | None = None
    expression: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        self.kind = str(self.kind or "always").strip().lower()
        if self.kind not in EDGE_CONDITION_KINDS:
            self.kind = "always"
        self.match_mode = "any" if str(self.match_mode).lower() == "any" else "all"
        if self.kind == "equation" and not self.equations and self.expression:
            self.equations = EquationClause.parse_expression(self.expression)

    def referenced_variables(self) -> set[str]:
        vars_used = {eq.variable for eq in self.equations if eq.variable}
        if self.expression:
            for eq in EquationClause.parse_expression(self.expression):
                if eq.variable:
                    vars_used.add(eq.variable)
        if self.prompt:
            for match in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", self.prompt):
                vars_used.add(match)
        return vars_used

    def evaluate_deterministic(self, variables: Mapping[str, Any]) -> bool | None:
        """Return True/False if deterministically decidable, or None if LLM judge is required."""
        if self.kind in {"always", "default", "else"}:
            return True
        if self.kind == "equation":
            clauses = list(self.equations)
            if not clauses and self.expression:
                clauses = EquationClause.parse_expression(self.expression)
            if not clauses:
                return False
            results = [c.evaluate(variables) for c in clauses]
            return any(results) if self.match_mode == "any" else all(results)
        if self.kind == "prompt":
            return None
        return False

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.kind not in EDGE_CONDITION_KINDS:
            errors.append(f"edge condition kind '{self.kind}' is invalid")
        if self.kind == "equation":
            clauses = list(self.equations)
            if not clauses and self.expression:
                clauses = EquationClause.parse_expression(self.expression)
            if not clauses:
                errors.append("equation edge condition requires at least one equation clause")
            for clause in clauses:
                errors.extend(clause.validate())
        elif self.kind == "prompt":
            if not self.prompt or not str(self.prompt).strip():
                errors.append("prompt edge condition requires non-empty prompt text")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "equations": [eq.to_dict() for eq in self.equations],
            "match_mode": self.match_mode,
            "prompt": self.prompt,
            "expression": self.expression,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "EdgeCondition":
        if not data or not isinstance(data, Mapping):
            return cls(kind="always")
        raw_eqs = data.get("equations") or []
        eqs = [
            EquationClause.from_dict(item)
            for item in raw_eqs
            if isinstance(item, Mapping)
        ]
        kind = str(data.get("kind") or data.get("type") or "always").strip().lower()
        expr = data.get("expression")
        prompt = data.get("prompt")
        if kind == "always" and expr and not eqs:
            parsed = EquationClause.parse_expression(str(expr))
            if parsed:
                kind = "equation"
                eqs = parsed
        return cls(
            kind=kind,
            equations=eqs,
            match_mode=str(data.get("match_mode") or "all"),
            prompt=str(prompt) if prompt is not None else None,
            expression=str(expr) if expr is not None else None,
            label=str(data["label"]) if data.get("label") is not None else None,
        )


@dataclass
class ModelOverride:
    """Optional per-node LLM provider/model/temperature override."""

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    def is_empty(self) -> bool:
        return (
            not self.provider
            and not self.model
            and self.temperature is None
            and self.max_tokens is None
        )

    def validate(self, node_id: str) -> list[str]:
        errors: list[str] = []
        if self.temperature is not None and not (0.0 <= float(self.temperature) <= 2.0):
            errors.append(f"node '{node_id}' model_override.temperature must be between 0.0 and 2.0")
        if self.max_tokens is not None and not (16 <= int(self.max_tokens) <= 4096):
            errors.append(f"node '{node_id}' model_override.max_tokens must be between 16 and 4096")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ModelOverride | None":
        if not data or not isinstance(data, Mapping):
            return None
        instance = cls(
            provider=str(data["provider"]).strip() if data.get("provider") else None,
            model=str(data["model"]).strip() if data.get("model") else None,
            temperature=float(data["temperature"]) if data.get("temperature") is not None else None,
            max_tokens=int(data["max_tokens"]) if data.get("max_tokens") is not None else None,
        )
        return None if instance.is_empty() else instance


@dataclass
class VoiceOverride:
    """Optional per-node TTS provider/voice/speed override."""

    provider: str | None = None
    voice_id: str | None = None
    speech_speed: float | None = None
    stability: float | None = None

    def is_empty(self) -> bool:
        return (
            not self.provider
            and not self.voice_id
            and self.speech_speed is None
            and self.stability is None
        )

    def validate(self, node_id: str) -> list[str]:
        errors: list[str] = []
        if self.speech_speed is not None and not (0.5 <= float(self.speech_speed) <= 2.0):
            errors.append(f"node '{node_id}' voice_override.speech_speed must be between 0.5 and 2.0")
        if self.stability is not None and not (0.0 <= float(self.stability) <= 1.0):
            errors.append(f"node '{node_id}' voice_override.stability must be between 0.0 and 1.0")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "voice_id": self.voice_id,
            "speech_speed": self.speech_speed,
            "stability": self.stability,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "VoiceOverride | None":
        if not data or not isinstance(data, Mapping):
            return None
        instance = cls(
            provider=str(data["provider"]).strip() if data.get("provider") else None,
            voice_id=str(data["voice_id"]).strip() if data.get("voice_id") else None,
            speech_speed=float(data["speech_speed"]) if data.get("speech_speed") is not None else None,
            stability=float(data["stability"]) if data.get("stability") is not None else None,
        )
        return None if instance.is_empty() else instance


@dataclass
class VariableSpec:
    """Schema specification for a flow variable extracted or initialized in the graph."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    enum_values: list[str] = field(default_factory=list)
    default: Any = None
    pattern: str | None = None

    def validate(self, node_id: str | None = None) -> list[str]:
        prefix = f"node '{node_id}' " if node_id else ""
        errors: list[str] = []
        if not self.name or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", self.name):
            errors.append(f"{prefix}variable name '{self.name}' must be a valid identifier")
        if self.type not in VARIABLE_TYPES:
            errors.append(
                f"{prefix}variable '{self.name}' type '{self.type}' must be one of {sorted(VARIABLE_TYPES)}"
            )
        if self.type == "enum" and not self.enum_values:
            errors.append(f"{prefix}enum variable '{self.name}' requires non-empty enum_values")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
            "enum_values": list(self.enum_values),
            "default": self.default,
            "pattern": self.pattern,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | str) -> "VariableSpec":
        if isinstance(data, str):
            return cls(name=data.strip())
        return cls(
            name=str(data.get("name") or "").strip(),
            type=str(data.get("type") or "string").strip().lower(),
            description=str(data.get("description") or ""),
            required=bool(data.get("required", False)),
            enum_values=[str(x) for x in (data.get("enum_values") or data.get("enum") or [])],
            default=data.get("default"),
            pattern=str(data["pattern"]) if data.get("pattern") else None,
        )


@dataclass
class GlobalNodeConfig:
    """Configuration for a global node that can be entered from any node in the flow."""

    enabled: bool = True
    condition_type: str = "prompt"  # "prompt" | "equation"
    prompt: str = ""
    equations: list[EquationClause] = field(default_factory=list)
    return_to_previous: bool = False

    def validate(self, node_id: str) -> list[str]:
        errors: list[str] = []
        if not self.enabled:
            return errors
        if self.condition_type == "prompt":
            if not self.prompt or not self.prompt.strip():
                errors.append(
                    f"global node '{node_id}' requires a non-empty trigger prompt or equation"
                )
        elif self.condition_type == "equation":
            if not self.equations:
                errors.append(
                    f"global node '{node_id}' with equation trigger requires at least one equation"
                )
            for eq in self.equations:
                errors.extend(eq.validate())
        else:
            errors.append(
                f"global node '{node_id}' condition_type '{self.condition_type}' must be 'prompt' or 'equation'"
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "condition_type": self.condition_type,
            "prompt": self.prompt,
            "equations": [eq.to_dict() for eq in self.equations],
            "return_to_previous": self.return_to_previous,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "GlobalNodeConfig | None":
        if not data or not isinstance(data, Mapping):
            return None
        if not data.get("enabled", True):
            return None
        trig = data.get("trigger_condition")
        if isinstance(trig, Mapping):
            cond_type = str(trig.get("kind") or "prompt").strip().lower()
            if cond_type not in {"prompt", "equation"}:
                cond_type = "prompt"
            prompt_val = str(trig.get("prompt") or "")
            raw_eqs = trig.get("equations") or []
        else:
            cond_type = str(data.get("condition_type") or "prompt").strip().lower()
            prompt_val = str(data.get("prompt") or data.get("trigger_prompt") or "")
            raw_eqs = data.get("equations") or []
        eqs = [
            EquationClause.from_dict(item)
            for item in raw_eqs
            if isinstance(item, Mapping)
        ]
        return cls(
            enabled=bool(data.get("enabled", True)),
            condition_type=cond_type,
            prompt=prompt_val,
            equations=eqs,
            return_to_previous=bool(data.get("return_to_previous", False)),
        )


@dataclass
class FlowEdge:
    """Directed transition between two conversation-flow or workflow nodes."""

    source_id: str
    target_id: str
    condition_label: str = "default"
    expression: str | None = None
    edge_id: str | None = None
    condition: EdgeCondition | None = None
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.edge_id:
            self.edge_id = f"e_{self.source_id}_{self.target_id}"
        if isinstance(self.condition, Mapping):
            self.condition = EdgeCondition.from_dict(self.condition)
        elif self.condition is None:
            if self.expression:
                parsed = EquationClause.parse_expression(self.expression)
                if parsed:
                    self.condition = EdgeCondition(
                        kind="equation",
                        equations=parsed,
                        expression=self.expression,
                        label=self.condition_label,
                    )
                else:
                    self.condition = EdgeCondition(
                        kind="prompt",
                        prompt=self.expression,
                        expression=self.expression,
                        label=self.condition_label,
                    )
            elif self.condition_label and self.condition_label not in {"default", "always", ""}:
                self.condition = EdgeCondition(
                    kind="prompt",
                    prompt=self.condition_label,
                    label=self.condition_label,
                )
            else:
                self.condition = EdgeCondition(kind="always", label=self.condition_label)

    def to_dict(self) -> dict[str, Any]:
        cond_dict = self.condition.to_dict() if self.condition else EdgeCondition().to_dict()
        return {
            "id": self.edge_id,
            "source": self.source_id,
            "target": self.target_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "condition_label": self.condition_label,
            "expression": self.expression,
            "priority": self.priority,
            "condition": cond_dict,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FlowEdge":
        source_id = str(data.get("source_id") or data.get("source") or "").strip()
        target_id = str(data.get("target_id") or data.get("target") or "").strip()
        edge_id = str(data.get("id") or data.get("edge_id") or f"e_{source_id}_{target_id}").strip()
        label = str(
            data.get("condition_label")
            or data.get("label")
            or (data.get("condition") or {}).get("label")
            or "default"
        )
        expr = data.get("expression")
        cond_raw = data.get("condition")
        condition = EdgeCondition.from_dict(cond_raw) if isinstance(cond_raw, Mapping) else None
        priority = int(data.get("priority", 0))
        return cls(
            source_id=source_id,
            target_id=target_id,
            condition_label=label,
            expression=str(expr) if expr is not None else None,
            edge_id=edge_id,
            condition=condition,
            priority=priority,
        )


class FlowNode:
    """Conversation-flow / workflow node definition with parameter validation and serialization."""

    def __init__(
        self,
        node_type: str,
        params: dict[str, Any] | None = None,
        *,
        node_id: str | None = None,
        label: str | None = None,
        position: dict[str, float] | None = None,
        is_global: bool | None = None,
        global_config: GlobalNodeConfig | dict[str, Any] | None = None,
        model_override: ModelOverride | dict[str, Any] | None = None,
        voice_override: VoiceOverride | dict[str, Any] | None = None,
    ) -> None:
        self.node_type = str(node_type).strip()
        self.params: dict[str, Any] = dict(params or {})
        self.node_id = str(
            node_id or self.params.get("id") or f"node_{self.node_type}"
        ).strip()
        self.label = str(
            label
            or self.params.get("label")
            or self.node_type.replace("_", " ").title()
        ).strip()
        raw_pos = position or self.params.get("position") or {"x": 0.0, "y": 0.0}
        self.position: dict[str, float] = {
            "x": float(raw_pos.get("x", 0.0)) if isinstance(raw_pos, Mapping) else 0.0,
            "y": float(raw_pos.get("y", 0.0)) if isinstance(raw_pos, Mapping) else 0.0,
        }

        if is_global is None:
            is_global = bool(self.params.get("is_global", False))
        self.is_global: bool = bool(is_global)

        raw_global = global_config or self.params.get("global_config")
        if isinstance(raw_global, GlobalNodeConfig):
            self.global_config: GlobalNodeConfig | None = raw_global
        elif isinstance(raw_global, Mapping):
            self.global_config = GlobalNodeConfig.from_dict(raw_global)
        elif self.is_global:
            self.global_config = GlobalNodeConfig(
                enabled=True,
                condition_type=str(self.params.get("global_condition_type") or "prompt"),
                prompt=str(self.params.get("global_prompt") or self.params.get("global_condition") or ""),
            )
        else:
            self.global_config = None

        raw_model = model_override or self.params.get("model_override")
        self.model_override: ModelOverride | None = (
            raw_model
            if isinstance(raw_model, ModelOverride)
            else ModelOverride.from_dict(raw_model)
        )

        raw_voice = voice_override or self.params.get("voice_override")
        self.voice_override: VoiceOverride | None = (
            raw_voice
            if isinstance(raw_voice, VoiceOverride)
            else VoiceOverride.from_dict(raw_voice)
        )

    @property
    def prompt(self) -> str:
        return str(self.params.get("prompt") or self.params.get("system_prompt") or self.params.get("text") or "")

    @property
    def tools(self) -> list[str]:
        raw = self.params.get("tools") or self.params.get("node_tools") or []
        return [str(t).strip() for t in raw if str(t).strip()]

    @property
    def knowledge_collection_ids(self) -> list[str]:
        raw = self.params.get("knowledge_collection_ids") or []
        return [str(c).strip() for c in raw if str(c).strip()]

    def is_valid_type(self) -> bool:
        return self.node_type in SUPPORTED_NODE_TYPES

    def is_terminal(self) -> bool:
        return self.node_type in {"end", "transfer", "transfer_human", "transfer_agent"} and bool(
            self.params.get("terminal", self.node_type == "end")
        )

    def extracted_variable_names(self) -> set[str]:
        names: set[str] = set()
        if self.node_type in {"extract_variables", "extract_variable", "conversation", "start"}:
            raw_vars = self.params.get("variables") or self.params.get("extract_variables") or []
            if isinstance(raw_vars, list):
                for item in raw_vars:
                    if isinstance(item, str) and item.strip():
                        names.add(item.strip())
                    elif isinstance(item, Mapping) and item.get("name"):
                        names.add(str(item["name"]).strip())
            elif isinstance(raw_vars, Mapping):
                for k in raw_vars.keys():
                    names.add(str(k).strip())
            single_var = self.params.get("variable_name")
            if single_var and str(single_var).strip():
                names.add(str(single_var).strip())
            set_vars = self.params.get("set_variables")
            if isinstance(set_vars, Mapping):
                for k in set_vars.keys():
                    if str(k).strip():
                        names.add(str(k).strip())
        if self.node_type in {"function", "function_call", "webhook"}:
            res_var = self.params.get("result_variable") or self.params.get("output_variable")
            if res_var and str(res_var).strip():
                names.add(str(res_var).strip())
            resp_map = self.params.get("response_mapping")
            if isinstance(resp_map, Mapping):
                for var_key in resp_map.keys():
                    if str(var_key).strip():
                        names.add(str(var_key).strip())
        return names

    def referenced_variable_names(self) -> set[str]:
        refs: set[str] = set()
        for text_field in (
            self.prompt,
            str(self.params.get("message") or ""),
            str(self.params.get("whisper_text") or ""),
            str(self.params.get("speak_text") or ""),
            str(self.params.get("digits") or ""),
        ):
            for match in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text_field):
                refs.add(match)
        if self.node_type in {"logic_split", "condition"}:
            for raw_eq in self.params.get("equations") or []:
                if isinstance(raw_eq, Mapping) and raw_eq.get("variable"):
                    refs.add(str(raw_eq["variable"]).strip())
            expr = self.params.get("expression")
            if isinstance(expr, str) and expr.strip():
                for eq in EquationClause.parse_expression(expr):
                    if eq.variable:
                        refs.add(eq.variable)
        if self.global_config is not None:
            for eq in self.global_config.equations:
                if eq.variable:
                    refs.add(eq.variable)
        return refs

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.node_type:
            errors.append("node_type must not be empty")
            return errors
        if not self.is_valid_type():
            errors.append(
                f"node '{self.node_id}' has unsupported type '{self.node_type}'"
            )
            return errors

        # Legacy workflow node checks (backward compatibility)
        if self.node_type == "function_call" and not self.params.get("tool_name"):
            errors.append(f"node '{self.node_id}' of type 'function_call' requires 'tool_name'")
        if self.node_type == "transfer_agent" and not self.params.get("target_agent_id"):
            errors.append(f"node '{self.node_id}' of type 'transfer_agent' requires 'target_agent_id'")
        if self.node_type == "webhook" and not self.params.get("url"):
            errors.append(f"node '{self.node_id}' of type 'webhook' requires 'url'")

        # Conversation-flow node checks (Part 5)
        if self.node_type == "conversation":
            if not self.prompt.strip():
                errors.append(
                    f"node '{self.node_id}' of type 'conversation' requires non-empty 'prompt'"
                )
        elif self.node_type == "function":
            has_ref = any(
                self.params.get(k)
                for k in ("tool_id", "tool_name", "url", "endpoint_url", "api_tool_id")
            )
            if not has_ref:
                errors.append(
                    f"node '{self.node_id}' of type 'function' requires 'tool_id', 'tool_name', or 'url'"
                )
        elif self.node_type == "transfer":
            dest = self.params.get("destination") or self.params.get("target_number") or self.params.get("queue")
            if not dest or not str(dest).strip():
                errors.append(
                    f"node '{self.node_id}' of type 'transfer' requires 'destination'"
                )
            mode = str(self.params.get("transfer_mode") or self.params.get("mode") or "cold").lower()
            if mode not in TRANSFER_MODES:
                errors.append(
                    f"node '{self.node_id}' transfer_mode '{mode}' must be 'cold' or 'warm'"
                )
        elif self.node_type == "press_digit":
            digits = str(self.params.get("digits") or "").strip()
            if not digits:
                errors.append(
                    f"node '{self.node_id}' of type 'press_digit' requires 'digits'"
                )
            elif not re.match(r"^[0-9*#wW\{\}a-zA-Z_]+$", digits):
                errors.append(
                    f"node '{self.node_id}' digits '{digits}' contains invalid DTMF characters"
                )
        elif self.node_type == "send_sms":
            msg = str(self.params.get("message") or self.params.get("text") or "").strip()
            if not msg:
                errors.append(
                    f"node '{self.node_id}' of type 'send_sms' requires 'message'"
                )
        elif self.node_type == "extract_variables":
            raw_vars = self.params.get("variables") or []
            set_vars = self.params.get("set_variables") or {}
            if not raw_vars and not set_vars:
                errors.append(
                    f"node '{self.node_id}' of type 'extract_variables' requires non-empty 'variables'"
                )
            elif isinstance(raw_vars, list):
                for raw_v in raw_vars:
                    spec = VariableSpec.from_dict(raw_v)
                    errors.extend(spec.validate(self.node_id))
        elif self.node_type == "subagent":
            target_agent = self.params.get("agent_id") or self.params.get("target_agent_id")
            if not target_agent or not str(target_agent).strip():
                errors.append(
                    f"node '{self.node_id}' of type 'subagent' requires 'agent_id'"
                )

        if self.is_global:
            if self.node_type == "start":
                errors.append(f"start node '{self.node_id}' cannot be marked as a global node")
            if self.global_config is None:
                errors.append(f"global node '{self.node_id}' requires global_config")
            else:
                errors.extend(self.global_config.validate(self.node_id))

        if self.model_override is not None:
            errors.extend(self.model_override.validate(self.node_id))
        if self.voice_override is not None:
            errors.extend(self.voice_override.validate(self.node_id))

        return errors

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.node_id,
            "type": self.node_type,
            "label": self.label,
            "params": dict(self.params),
            "position": dict(self.position),
            "is_global": self.is_global,
        }
        if self.global_config is not None:
            out["global_config"] = self.global_config.to_dict()
        if self.model_override is not None:
            out["model_override"] = self.model_override.to_dict()
        if self.voice_override is not None:
            out["voice_override"] = self.voice_override.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FlowNode":
        node_id = str(data.get("id") or data.get("node_id") or "").strip()
        node_type = str(data.get("type") or data.get("node_type") or "").strip()
        raw_params = data.get("params") if isinstance(data.get("params"), Mapping) else data.get("data")
        params: dict[str, Any] = dict(raw_params) if isinstance(raw_params, Mapping) else {}
        for key in (
            "prompt",
            "system_prompt",
            "tools",
            "node_tools",
            "knowledge_collection_ids",
            "tool_id",
            "tool_name",
            "url",
            "endpoint_url",
            "http_method",
            "result_variable",
            "destination",
            "transfer_mode",
            "whisper_text",
            "digits",
            "pause_ms",
            "message",
            "to_number",
            "variables",
            "agent_id",
            "target_agent_id",
            "speak_text",
            "reason",
            "equations",
            "expression",
            "greeting",
        ):
            if key in data and key not in params:
                params[key] = data[key]
        label = str(data.get("label") or params.get("label") or node_type.replace("_", " ").title())
        pos = data.get("position") if isinstance(data.get("position"), Mapping) else None
        is_global = bool(data.get("is_global", params.get("is_global", False)))
        global_cfg = data.get("global_config") or params.get("global_config")
        model_ov = data.get("model_override") or params.get("model_override")
        voice_ov = data.get("voice_override") or params.get("voice_override")
        return cls(
            node_type=node_type,
            params=params,
            node_id=node_id or None,
            label=label,
            position=dict(pos) if pos else None,
            is_global=is_global,
            global_config=global_cfg if isinstance(global_cfg, (Mapping, GlobalNodeConfig)) else None,
            model_override=model_ov if isinstance(model_ov, (Mapping, ModelOverride)) else None,
            voice_override=voice_ov if isinstance(voice_ov, (Mapping, VoiceOverride)) else None,
        )


@dataclass
class FlowGraph:
    """Directed graph container of FlowNodes and FlowEdges with global nodes and variables."""

    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    initial_variables: dict[str, Any] = field(default_factory=dict)
    variables: list[VariableSpec] = field(default_factory=list)
    default_model: ModelOverride | None = None
    default_voice: VoiceOverride | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def node_map(self) -> dict[str, FlowNode]:
        return {node.node_id: node for node in self.nodes}

    def start_nodes(self) -> list[FlowNode]:
        return [node for node in self.nodes if node.node_type == "start"]

    def global_nodes(self) -> list[FlowNode]:
        return [node for node in self.nodes if node.is_global and node.global_config is not None and node.global_config.enabled]

    def outgoing_edges(self, node_id: str) -> list[FlowEdge]:
        matched = [edge for edge in self.edges if edge.source_id == node_id]
        return sorted(matched, key=lambda e: e.priority)

    def incoming_edges(self, node_id: str) -> list[FlowEdge]:
        return [edge for edge in self.edges if edge.target_id == node_id]

    def declared_variable_names(self) -> set[str]:
        declared: set[str] = set(self.initial_variables.keys())
        for spec in self.variables:
            if spec.name:
                declared.add(spec.name)
        for node in self.nodes:
            declared.update(node.extracted_variable_names())
        # Built-in call context variables always available at runtime
        declared.update(
            {
                "caller_number",
                "from_number",
                "to_number",
                "call_id",
                "call_sid",
                "tenant_id",
                "agent_id",
                "last_user_utterance",
                "turn_count",
                "tool_ok",
                "tool_result",
            }
        )
        return declared

    def validate_graph(self) -> list[str]:
        errors: list[str] = []
        if not self.nodes:
            errors.append("workflow graph must contain at least one node")
            return errors
        seen_ids: set[str] = set()
        for node in self.nodes:
            if node.node_id in seen_ids:
                errors.append(f"duplicate node id '{node.node_id}'")
            seen_ids.add(node.node_id)
            errors.extend(node.validate())
        by_id = self.node_map()
        for edge in self.edges:
            if edge.source_id not in by_id:
                errors.append(f"edge references unknown source_id '{edge.source_id}'")
            if edge.target_id not in by_id:
                errors.append(f"edge references unknown target_id '{edge.target_id}'")
            if edge.condition is not None:
                errors.extend(edge.condition.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "initial_variables": dict(self.initial_variables),
            "variables": [v.to_dict() for v in self.variables],
            "metadata": dict(self.metadata),
        }
        if self.default_model is not None:
            out["default_model"] = self.default_model.to_dict()
        if self.default_voice is not None:
            out["default_voice"] = self.default_voice.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | "FlowGraph") -> "FlowGraph":
        if isinstance(data, FlowGraph):
            return data
        if not isinstance(data, Mapping):
            return cls()
        raw_nodes = data.get("nodes") or []
        raw_edges = data.get("edges") or []
        nodes = [
            item if isinstance(item, FlowNode) else FlowNode.from_dict(item)
            for item in raw_nodes
            if isinstance(item, (Mapping, FlowNode))
        ]
        edges = [
            item if isinstance(item, FlowEdge) else FlowEdge.from_dict(item)
            for item in raw_edges
            if isinstance(item, (Mapping, FlowEdge))
        ]
        init_vars = data.get("initial_variables")
        raw_var_specs = data.get("variables") or []
        var_specs = [
            VariableSpec.from_dict(v)
            for v in raw_var_specs
            if isinstance(v, (Mapping, str))
        ]
        return cls(
            nodes=nodes,
            edges=edges,
            initial_variables=dict(init_vars) if isinstance(init_vars, Mapping) else {},
            variables=var_specs,
            default_model=ModelOverride.from_dict(data.get("default_model")),
            default_voice=VoiceOverride.from_dict(data.get("default_voice")),
            metadata=dict(data.get("metadata") or {}),
            version=int(data.get("version", 1)),
        )


def export_flow_json_schema() -> dict[str, Any]:
    """Return the canonical JSON Schema for a VoxDesk Conversation Flow graph."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://voxdesk.ai/schemas/conversation-flow.v1.json",
        "title": "VoxDeskConversationFlow",
        "type": "object",
        "required": ["nodes", "edges"],
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "minimum": 1, "default": 1},
            "nodes": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/$defs/FlowNode"},
            },
            "edges": {
                "type": "array",
                "items": {"$ref": "#/$defs/FlowEdge"},
            },
            "initial_variables": {
                "type": "object",
                "additionalProperties": True,
                "default": {},
            },
            "variables": {
                "type": "array",
                "items": {"$ref": "#/$defs/VariableSpec"},
                "default": [],
            },
            "default_model": {"$ref": "#/$defs/ModelOverride"},
            "default_voice": {"$ref": "#/$defs/VoiceOverride"},
            "metadata": {
                "type": "object",
                "additionalProperties": True,
                "default": {},
            },
        },
        "$defs": {
            "EquationClause": {
                "type": "object",
                "required": ["variable", "operator"],
                "additionalProperties": False,
                "properties": {
                    "variable": {"type": "string", "minLength": 1},
                    "operator": {
                        "type": "string",
                        "enum": sorted(EQUATION_OPERATORS),
                    },
                    "value": {},
                },
            },
            "EdgeCondition": {
                "type": "object",
                "required": ["kind"],
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": sorted(EDGE_CONDITION_KINDS),
                    },
                    "equations": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/EquationClause"},
                    },
                    "match_mode": {
                        "type": "string",
                        "enum": ["all", "any"],
                        "default": "all",
                    },
                    "prompt": {"type": ["string", "null"]},
                    "expression": {"type": ["string", "null"]},
                    "label": {"type": ["string", "null"]},
                },
            },
            "ModelOverride": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "provider": {"type": ["string", "null"]},
                    "model": {"type": ["string", "null"]},
                    "temperature": {
                        "type": ["number", "null"],
                        "minimum": 0.0,
                        "maximum": 2.0,
                    },
                    "max_tokens": {
                        "type": ["integer", "null"],
                        "minimum": 16,
                        "maximum": 4096,
                    },
                },
            },
            "VoiceOverride": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "provider": {"type": ["string", "null"]},
                    "voice_id": {"type": ["string", "null"]},
                    "speech_speed": {
                        "type": ["number", "null"],
                        "minimum": 0.5,
                        "maximum": 2.0,
                    },
                    "stability": {
                        "type": ["number", "null"],
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
            },
            "VariableSpec": {
                "type": "object",
                "required": ["name"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[a-zA-Z_][a-zA-Z0-9_]*$",
                    },
                    "type": {
                        "type": "string",
                        "enum": sorted(VARIABLE_TYPES),
                        "default": "string",
                    },
                    "description": {"type": "string", "default": ""},
                    "required": {"type": "boolean", "default": False},
                    "enum_values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "default": {},
                    "pattern": {"type": ["string", "null"]},
                },
            },
            "GlobalNodeConfig": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "condition_type": {
                        "type": "string",
                        "enum": ["prompt", "equation"],
                        "default": "prompt",
                    },
                    "prompt": {"type": "string", "default": ""},
                    "equations": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/EquationClause"},
                        "default": [],
                    },
                    "return_to_previous": {"type": "boolean", "default": False},
                },
            },
            "FlowNode": {
                "type": "object",
                "required": ["id", "type", "label", "params", "position"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {
                        "type": "string",
                        "enum": sorted(FLOW_NODE_TYPES),
                    },
                    "label": {"type": "string", "minLength": 1},
                    "params": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "position": {
                        "type": "object",
                        "required": ["x", "y"],
                        "additionalProperties": False,
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                    },
                    "is_global": {"type": "boolean", "default": False},
                    "global_config": {"$ref": "#/$defs/GlobalNodeConfig"},
                    "model_override": {"$ref": "#/$defs/ModelOverride"},
                    "voice_override": {"$ref": "#/$defs/VoiceOverride"},
                },
            },
            "FlowEdge": {
                "type": "object",
                "required": ["source_id", "target_id"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "source_id": {"type": "string", "minLength": 1},
                    "target_id": {"type": "string", "minLength": 1},
                    "condition_label": {"type": "string", "default": "default"},
                    "expression": {"type": ["string", "null"]},
                    "priority": {"type": "integer", "default": 0},
                    "condition": {"$ref": "#/$defs/EdgeCondition"},
                },
            },
        },
    }
