"""Visual flow builder contract (drag-and-drop nodes and directed graph validation).

Defines typed node contracts, edge validation, and DAG cycle/reachability verification
used by the visual workflow builder and durable workflow executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORTED_NODE_TYPES = frozenset(
    {
        "start",
        "prompt",
        "llm_response",
        "function_call",
        "condition",
        "transfer_agent",
        "transfer_human",
        "extract_variable",
        "webhook",
        "wait_for_input",
        "end",
    }
)


@dataclass
class FlowEdge:
    """Directed transition between two workflow nodes."""

    source_id: str
    target_id: str
    condition_label: str = "default"
    expression: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "condition_label": self.condition_label,
            "expression": self.expression,
        }


class FlowNode:
    """Visual workflow node definition with parameter validation and serialization."""

    def __init__(
        self,
        node_type: str,
        params: dict[str, Any] | None = None,
        *,
        node_id: str | None = None,
        label: str | None = None,
        position: dict[str, float] | None = None,
    ) -> None:
        self.node_type = str(node_type).strip()
        self.params: dict[str, Any] = dict(params or {})
        self.node_id = node_id or str(self.params.get("id") or f"node_{self.node_type}")
        self.label = label or str(self.params.get("label") or self.node_type.replace("_", " ").title())
        self.position = position or {"x": 0.0, "y": 0.0}

    def is_valid_type(self) -> bool:
        return self.node_type in SUPPORTED_NODE_TYPES

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.node_type:
            errors.append("node_type must not be empty")
        if self.node_type == "function_call" and not self.params.get("tool_name"):
            errors.append(f"node '{self.node_id}' of type 'function_call' requires 'tool_name'")
        if self.node_type == "transfer_agent" and not self.params.get("target_agent_id"):
            errors.append(f"node '{self.node_id}' of type 'transfer_agent' requires 'target_agent_id'")
        if self.node_type == "webhook" and not self.params.get("url"):
            errors.append(f"node '{self.node_id}' of type 'webhook' requires 'url'")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "type": self.node_type,
            "label": self.label,
            "params": self.params,
            "position": self.position,
        }


@dataclass
class FlowGraph:
    """Directed graph container of FlowNodes and FlowEdges with reachability checks."""

    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)

    def node_map(self) -> dict[str, FlowNode]:
        return {node.node_id: node for node in self.nodes}

    def outgoing_edges(self, node_id: str) -> list[FlowEdge]:
        return [edge for edge in self.edges if edge.source_id == node_id]

    def validate_graph(self) -> list[str]:
        errors: list[str] = []
        by_id = self.node_map()
        if not by_id:
            errors.append("workflow graph must contain at least one node")
            return errors
        for node in self.nodes:
            errors.extend(node.validate())
        for edge in self.edges:
            if edge.source_id not in by_id:
                errors.append(f"edge references unknown source_id '{edge.source_id}'")
            if edge.target_id not in by_id:
                errors.append(f"edge references unknown target_id '{edge.target_id}'")
        return errors
