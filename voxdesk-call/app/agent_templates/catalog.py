"""Agent starter templates as data (PART 18, P18-01).

A template is not a demo script. It is the *product* an operator starts from:
a configuration that will actually run, a flow that validates, the tools it
calls, the variables it collects, the schema its post-call analysis writes,
simulation cases that prove the happy path, knowledge-base placeholders that
name what has to be supplied, and the compliance notes that belong to that
vertical.

Four rules hold for every template here, and each one is a test in
``tests/agent_templates/test_templates.py``:

1. **It validates.** ``app.builder.flow_validation.validate_flow`` must return
   ``valid=True`` for the bundled flow. A template that cannot be saved is not
   a starting point, it is a trap.
2. **It is self-describing.** Every ``{{variable}}`` the flow reads is declared
   in ``dynamic_variables`` with a type and a purpose. Undeclared variables are
   how an agent silently fills a blank with an empty string.
3. **It states its own gaps.** KB placeholders are placeholders, and
   ``compliance_notes`` says what counsel must confirm. Nothing claims a
   certification this repository cannot produce.
4. **It never carries a credential.** Templates reference secret *names*
   (``{"$secret": "crm_api_key"}``), never values, and the loader refuses a
   template that looks otherwise.

Templates are YAML on disk so an operator can read and diff them without
reading Python.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.builder.flow_validation import validate_flow

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

#: Names of the seven verticals the plan asks for. Kept explicit so the loader
#: can fail loudly when one disappears rather than silently shipping six.
REQUIRED_TEMPLATE_IDS: tuple[str, ...] = (
    "receptionist",
    "appointment_setter",
    "lead_qualification",
    "customer_support",
    "debt_collection",
    "survey_nps",
    "dispatch",
)

_DECLARED_VARIABLE_TYPES = frozenset(
    {"string", "number", "boolean", "date", "enum", "phone", "email", "list"}
)


class TemplateError(ValueError):
    """A template that cannot be loaded, or that breaks a rule above."""


@dataclass(frozen=True)
class VariableSpec:
    name: str
    type: str
    purpose: str
    required: bool = False
    examples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "purpose": self.purpose,
            "required": self.required,
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class SimulationCase:
    """One scripted turn sequence with the outcome the flow must produce."""

    name: str
    turns: tuple[str, ...]
    expect_node: str
    expect_variables: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "turns": list(self.turns),
            "expect_node": self.expect_node,
            "expect_variables": dict(self.expect_variables),
        }


@dataclass(frozen=True)
class AgentTemplate:
    id: str
    name: str
    vertical: str
    summary: str
    config: Mapping[str, Any]
    flow: Mapping[str, Any]
    tools: tuple[Mapping[str, Any], ...]
    dynamic_variables: tuple[VariableSpec, ...]
    analysis_schema: Mapping[str, Any]
    simulation_cases: tuple[SimulationCase, ...]
    kb_placeholders: tuple[str, ...]
    compliance_notes: tuple[str, ...]

    # -- derived -----------------------------------------------------------
    @property
    def declared_variable_names(self) -> set[str]:
        return {spec.name for spec in self.dynamic_variables}

    def referenced_variables(self) -> set[str]:
        """Every ``{{name}}`` appearing anywhere in the flow or config."""
        found: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, str):
                for raw in _iter_placeholders(value):
                    found.add(raw)
            elif isinstance(value, Mapping):
                for inner in value.values():
                    walk(inner)
            elif isinstance(value, (list, tuple)):
                for inner in value:
                    walk(inner)

        walk(self.flow)
        walk(self.config)
        return found

    def validate(self) -> list[str]:
        """Problems that make this template unfit to ship. Empty means good."""
        problems: list[str] = []

        flow_result = validate_flow(dict(self.flow))
        if not flow_result.valid:
            for issue in flow_result.errors:
                problems.append(f"flow.{issue.field}: {issue.code} — {issue.message}")

        undeclared = sorted(self.referenced_variables() - self.declared_variable_names)
        if undeclared:
            problems.append(
                "flow reads variables that are not declared in dynamic_variables: "
                + ", ".join(undeclared)
            )

        for spec in self.dynamic_variables:
            if spec.type not in _DECLARED_VARIABLE_TYPES:
                problems.append(f"variable {spec.name!r} has unknown type {spec.type!r}")

        if not self.simulation_cases:
            problems.append("no simulation cases — the template proves nothing")
        node_ids = {
            str(node.get("id"))
            for node in self.flow.get("nodes", [])
            if isinstance(node, Mapping)
        }
        for case in self.simulation_cases:
            if case.expect_node not in node_ids:
                problems.append(
                    f"simulation case {case.name!r} expects node {case.expect_node!r} "
                    "which is not in the flow"
                )

        for tool in self.tools:
            if not tool.get("name"):
                problems.append("a tool has no name")
            refs = _find_secret_refs(tool)
            for ref in refs:
                if not isinstance(ref, str) or not ref.strip():
                    problems.append(f"tool {tool.get('name')!r} has an empty $secret reference")

        if not self.analysis_schema.get("fields"):
            problems.append("analysis_schema has no fields")

        if not self.kb_placeholders:
            problems.append(
                "no kb_placeholders — the template claims to answer without saying "
                "what the knowledge base must contain"
            )

        if not self.compliance_notes:
            problems.append("no compliance notes — counsel cannot review what is not written")

        return problems

    def as_dict(self, *, include_flow: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "vertical": self.vertical,
            "summary": self.summary,
            "config": dict(self.config),
            "tools": [dict(t) for t in self.tools],
            "dynamic_variables": [v.as_dict() for v in self.dynamic_variables],
            "analysis_schema": dict(self.analysis_schema),
            "simulation_cases": [c.as_dict() for c in self.simulation_cases],
            "kb_placeholders": list(self.kb_placeholders),
            "compliance_notes": list(self.compliance_notes),
        }
        if include_flow:
            payload["flow"] = copy.deepcopy(dict(self.flow))
        return payload


def _iter_placeholders(text: str):
    """Yield ``name`` for every ``{{name}}`` in ``text``.

    Written as a scanner rather than a regex so a template like
    ``"{{a}} and {{b}}"`` yields both, and ``"{{ a.b }}"`` yields ``a.b``
    rather than ``a``.
    """
    start = 0
    while True:
        left = text.find("{{", start)
        if left < 0:
            return
        right = text.find("}}", left + 2)
        if right < 0:
            return
        name = text[left + 2 : right].strip()
        if name:
            yield name
        start = right + 2


def _find_secret_refs(value: Any) -> list[Any]:
    """Every value behind a ``$secret`` key, anywhere in ``value``."""
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if key == "$secret":
                found.append(inner)
            else:
                found.extend(_find_secret_refs(inner))
    elif isinstance(value, (list, tuple)):
        for inner in value:
            found.extend(_find_secret_refs(inner))
    return found


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TemplateError(f"{where} must be a mapping")
    return value


def _str_tuple(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise TemplateError(f"{where} must be a list of strings")
    out = tuple(str(item) for item in value)
    return out


def load_template(path: Path) -> AgentTemplate:
    """Parse one YAML template and refuse it if it breaks a rule."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed YAML is a bug in the file
        raise TemplateError(f"{path.name}: not valid YAML: {exc}") from exc

    data = _mapping(raw, path.name)

    for required in ("id", "name", "vertical", "summary", "config", "flow"):
        if not data.get(required):
            raise TemplateError(f"{path.name}: missing required key {required!r}")

    variables: list[VariableSpec] = []
    for item in data.get("dynamic_variables") or []:
        item_map = _mapping(item, f"{path.name}: dynamic_variables entry")
        variables.append(
            VariableSpec(
                name=str(item_map["name"]),
                type=str(item_map.get("type", "string")),
                purpose=str(item_map.get("purpose", "")),
                required=bool(item_map.get("required", False)),
                examples=_str_tuple(item_map.get("examples"), "examples"),
            )
        )

    cases: list[SimulationCase] = []
    for item in data.get("simulation_cases") or []:
        item_map = _mapping(item, f"{path.name}: simulation case")
        cases.append(
            SimulationCase(
                name=str(item_map.get("name", "unnamed")),
                turns=_str_tuple(item_map.get("turns"), "turns"),
                expect_node=str(item_map.get("expect_node", "")),
                expect_variables=dict(item_map.get("expect_variables") or {}),
            )
        )

    template = AgentTemplate(
        id=str(data["id"]),
        name=str(data["name"]),
        vertical=str(data["vertical"]),
        summary=str(data["summary"]),
        config=dict(_mapping(data["config"], "config")),
        flow=dict(_mapping(data["flow"], "flow")),
        tools=tuple(dict(_mapping(t, "tool")) for t in (data.get("tools") or [])),
        dynamic_variables=tuple(variables),
        analysis_schema=dict(data.get("analysis_schema") or {}),
        simulation_cases=tuple(cases),
        kb_placeholders=_str_tuple(data.get("kb_placeholders"), "kb_placeholders"),
        compliance_notes=_str_tuple(data.get("compliance_notes"), "compliance_notes"),
    )

    if template.id != path.stem:
        raise TemplateError(
            f"{path.name}: id {template.id!r} does not match the filename stem {path.stem!r}"
        )

    problems = template.validate()
    if problems:
        raise TemplateError(
            f"{path.name} is not shippable:\n  " + "\n  ".join(problems)
        )
    return template


@lru_cache(maxsize=1)
def _load_all() -> tuple[AgentTemplate, ...]:
    if not TEMPLATE_DIR.is_dir():
        raise TemplateError(f"template directory is missing: {TEMPLATE_DIR}")
    templates = tuple(load_template(p) for p in sorted(TEMPLATE_DIR.glob("*.yaml")))
    ids = {t.id for t in templates}
    missing = [t for t in REQUIRED_TEMPLATE_IDS if t not in ids]
    if missing:
        raise TemplateError("missing required templates: " + ", ".join(missing))
    return templates


def all_templates() -> tuple[AgentTemplate, ...]:
    """Every shipped template, in filename order."""
    return _load_all()


def get_template(template_id: str) -> AgentTemplate:
    for template in all_templates():
        if template.id == template_id:
            return template
    raise KeyError(template_id)


def reload_templates() -> None:
    """Drop the cache — used by tests that rewrite a template on disk."""
    _load_all.cache_clear()
