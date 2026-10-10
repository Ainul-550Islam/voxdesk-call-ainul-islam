# File: app/builder/flow_validation.py — Static graph validation for Conversation Flows (reachability, dead ends, unique IDs, undefined variables, unreachable globals) (Part 5 / Gate G6)
"""Static graph validation for VoxDesk Conversation Flows.

Used by:
  1. The React Flow visual editor (`ValidationPanel.tsx` and `/api/agents/{id}/flow/validate`)
  2. The `AgentVersion` publish gate (`app/domain/agent_models.py` and `app/api/agent_flow_routes.py`)

Checks:
  - Non-empty graph with exactly one `start` node
  - Unique node IDs and unique edge IDs
  - Node-type parameter requirements (`conversation`, `function`, `transfer`, `press_digit`,
    `send_sms`, `extract_variables`, `subagent`, `logic_split`, `end`)
  - Valid edge endpoints (`source_id`, `target_id`) and edge conditions
  - Graph reachability from `start` via BFS
  - Dead-end detection on non-terminal nodes (`start`, `conversation`, `logic_split`,
    `function`, `press_digit`, `send_sms`, `extract_variables`)
  - Undefined dynamic variable references in node prompts/equations and edge conditions
  - Unreachable or misconfigured global nodes (`is_global=True`)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from app.builder.node import FlowGraph, FlowNode

TERMINAL_NODE_TYPES: frozenset[str] = frozenset(
    {
        "end",
        "transfer",
        "subagent",
        "transfer_human",
        "transfer_agent",
    }
)


@dataclass(frozen=True)
class FlowValidationIssue:
    """Single structured validation error or warning tied to a specific node/edge."""

    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    field: str | None = None
    severity: Literal["error", "warning"] = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
            "field": self.field or (f"nodes.{self.node_id}" if self.node_id else "flow"),
            "severity": self.severity,
        }


@dataclass
class FlowValidationResult:
    """Aggregate result of validating a FlowGraph."""

    valid: bool
    errors: list[FlowValidationIssue] = field(default_factory=list)
    warnings: list[FlowValidationIssue] = field(default_factory=list)
    reachable_node_ids: list[str] = field(default_factory=list)
    declared_variables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "reachable_node_ids": list(self.reachable_node_ids),
            "declared_variables": list(self.declared_variables),
        }


def _is_node_terminal(node: FlowNode) -> bool:
    if node.node_type == "end":
        return True
    if node.node_type in {"transfer", "subagent", "transfer_human", "transfer_agent"}:
        # Transfer and subagent nodes are terminal unless explicitly configured as non-terminal
        return bool(node.params.get("terminal", True))
    return False


def validate_flow(graph_or_data: FlowGraph | Mapping[str, Any]) -> FlowValidationResult:
    """Validate a conversation flow graph and return structured issues with node IDs."""
    graph = (
        graph_or_data
        if isinstance(graph_or_data, FlowGraph)
        else FlowGraph.from_dict(graph_or_data)
    )

    errors: list[FlowValidationIssue] = []
    warnings: list[FlowValidationIssue] = []

    if not graph.nodes:
        errors.append(
            FlowValidationIssue(
                code="EMPTY_GRAPH",
                message="Conversation flow must contain at least one node.",
                field="flow.nodes",
            )
        )
        return FlowValidationResult(valid=False, errors=errors, warnings=warnings)

    # 1. Unique node IDs & individual node validation
    seen_node_ids: set[str] = set()
    node_map: dict[str, FlowNode] = {}
    for node in graph.nodes:
        if not node.node_id:
            errors.append(
                FlowValidationIssue(
                    code="EMPTY_NODE_ID",
                    message="Every node must have a non-empty id.",
                    field="flow.nodes",
                )
            )
            continue
        if node.node_id in seen_node_ids:
            errors.append(
                FlowValidationIssue(
                    code="DUPLICATE_NODE_ID",
                    message=f"Duplicate node id '{node.node_id}'.",
                    node_id=node.node_id,
                    field=f"nodes.{node.node_id}.id",
                )
            )
        else:
            seen_node_ids.add(node.node_id)
            node_map[node.node_id] = node

        # Global node reachability / trigger validation before general node checks
        if node.is_global:
            if node.node_type == "start":
                errors.append(
                    FlowValidationIssue(
                        code="INVALID_GLOBAL_START",
                        message=f"Start node '{node.node_id}' cannot be a global node.",
                        node_id=node.node_id,
                        field=f"nodes.{node.node_id}.is_global",
                    )
                )
            gcfg = node.global_config
            if gcfg is None or not gcfg.enabled:
                errors.append(
                    FlowValidationIssue(
                        code="UNREACHABLE_GLOBAL_NODE",
                        message=f"Global node '{node.node_id}' is disabled or missing global_config and can never trigger.",
                        node_id=node.node_id,
                        field=f"nodes.{node.node_id}.global_config",
                    )
                )
            elif gcfg.condition_type == "prompt" and not (gcfg.prompt or "").strip():
                errors.append(
                    FlowValidationIssue(
                        code="UNREACHABLE_GLOBAL_NODE",
                        message=f"Global node '{node.node_id}' has an empty trigger prompt and is unreachable.",
                        node_id=node.node_id,
                        field=f"nodes.{node.node_id}.global_config.prompt",
                    )
                )
            elif gcfg.condition_type == "equation" and not gcfg.equations:
                errors.append(
                    FlowValidationIssue(
                        code="UNREACHABLE_GLOBAL_NODE",
                        message=f"Global node '{node.node_id}' has no trigger equations and is unreachable.",
                        node_id=node.node_id,
                        field=f"nodes.{node.node_id}.global_config.equations",
                    )
                )

        for node_err in node.validate():
            # Avoid duplicating the UNREACHABLE_GLOBAL_NODE message if already recorded
            if node.is_global and "global node" in node_err and any(
                e.node_id == node.node_id and e.code == "UNREACHABLE_GLOBAL_NODE"
                for e in errors
            ):
                continue
            errors.append(
                FlowValidationIssue(
                    code="INVALID_NODE",
                    message=node_err,
                    node_id=node.node_id,
                    field=f"nodes.{node.node_id}",
                )
            )

    # 2. Start node existence and uniqueness
    start_nodes = [n for n in graph.nodes if n.node_type == "start"]
    if not start_nodes:
        errors.append(
            FlowValidationIssue(
                code="MISSING_START_NODE",
                message="Conversation flow must contain a 'start' node.",
                field="flow.nodes",
            )
        )
    elif len(start_nodes) > 1:
        for extra in start_nodes[1:]:
            errors.append(
                FlowValidationIssue(
                    code="MULTIPLE_START_NODES",
                    message=f"Flow has multiple 'start' nodes; '{extra.node_id}' is redundant.",
                    node_id=extra.node_id,
                    field=f"nodes.{extra.node_id}.type",
                )
            )

    # 3. Edge validation & adjacency construction
    seen_edge_ids: set[str] = set()
    outgoing: dict[str, list[str]] = {nid: [] for nid in node_map}
    for edge in graph.edges:
        eid = edge.edge_id or f"e_{edge.source_id}_{edge.target_id}"
        if eid in seen_edge_ids:
            errors.append(
                FlowValidationIssue(
                    code="DUPLICATE_EDGE_ID",
                    message=f"Duplicate edge id '{eid}'.",
                    node_id=edge.source_id if edge.source_id in node_map else None,
                    edge_id=eid,
                    field=f"edges.{eid}",
                )
            )
        seen_edge_ids.add(eid)

        src_ok = edge.source_id in node_map
        tgt_ok = edge.target_id in node_map
        if not src_ok:
            errors.append(
                FlowValidationIssue(
                    code="UNKNOWN_EDGE_SOURCE",
                    message=f"Edge '{eid}' references unknown source_id '{edge.source_id}'.",
                    node_id=edge.source_id,
                    edge_id=eid,
                    field=f"edges.{eid}.source_id",
                )
            )
        if not tgt_ok:
            errors.append(
                FlowValidationIssue(
                    code="UNKNOWN_EDGE_TARGET",
                    message=f"Edge '{eid}' references unknown target_id '{edge.target_id}'.",
                    node_id=edge.source_id if src_ok else edge.target_id,
                    edge_id=eid,
                    field=f"edges.{eid}.target_id",
                )
            )
        if src_ok and tgt_ok:
            outgoing[edge.source_id].append(edge.target_id)

        if edge.condition is not None:
            for cond_err in edge.condition.validate():
                errors.append(
                    FlowValidationIssue(
                        code="INVALID_EDGE_CONDITION",
                        message=f"Edge '{eid}': {cond_err}",
                        node_id=edge.source_id if src_ok else None,
                        edge_id=eid,
                        field=f"edges.{eid}.condition",
                    )
                )

    # 4. Reachability from start node(s) + global nodes' subgraphs
    reachable: set[str] = set()
    bfs_queue: deque[str] = deque()
    for s_node in start_nodes:
        if s_node.node_id in node_map:
            reachable.add(s_node.node_id)
            bfs_queue.append(s_node.node_id)

    while bfs_queue:
        curr_id = bfs_queue.popleft()
        for nxt_id in outgoing.get(curr_id, []):
            if nxt_id not in reachable:
                reachable.add(nxt_id)
                bfs_queue.append(nxt_id)

    # Nodes reachable from valid global nodes are also reachable during a call
    global_reachable: set[str] = set()
    global_queue: deque[str] = deque()
    for g_node in graph.global_nodes():
        if g_node.node_id in node_map:
            has_global_err = any(
                e.node_id == g_node.node_id and e.code == "UNREACHABLE_GLOBAL_NODE"
                for e in errors
            )
            if not has_global_err:
                global_reachable.add(g_node.node_id)
                global_queue.append(g_node.node_id)

    while global_queue:
        curr_id = global_queue.popleft()
        for nxt_id in outgoing.get(curr_id, []):
            if nxt_id not in global_reachable:
                global_reachable.add(nxt_id)
                global_queue.append(nxt_id)

    all_reachable = reachable | global_reachable

    if start_nodes:
        for node in graph.nodes:
            if node.node_id not in node_map:
                continue
            if node.is_global:
                continue
            if node.node_id not in all_reachable:
                errors.append(
                    FlowValidationIssue(
                        code="UNREACHABLE_NODE",
                        message=f"Node '{node.node_id}' ({node.label}) is not reachable from the start node.",
                        node_id=node.node_id,
                        field=f"nodes.{node.node_id}",
                    )
                )

    # 5. Dead-end detection on non-terminal nodes
    for node in graph.nodes:
        if node.node_id not in node_map:
            continue
        if _is_node_terminal(node):
            continue
        if (
            node.is_global
            and node.global_config is not None
            and node.global_config.return_to_previous
        ):
            continue
        out_targets = outgoing.get(node.node_id, [])
        if not out_targets:
            errors.append(
                FlowValidationIssue(
                    code="DEAD_END_NODE",
                    message=(
                        f"Non-terminal node '{node.node_id}' ({node.node_type}) has no outgoing edges "
                        "and will stall the conversation."
                    ),
                    node_id=node.node_id,
                    field=f"nodes.{node.node_id}.edges",
                )
            )

    # 6. Undefined dynamic variable references
    declared_vars = graph.declared_variable_names()
    for node in graph.nodes:
        if node.node_id not in node_map:
            continue
        for ref_var in sorted(node.referenced_variable_names()):
            if ref_var not in declared_vars:
                errors.append(
                    FlowValidationIssue(
                        code="UNDEFINED_VARIABLE",
                        message=(
                            f"Node '{node.node_id}' references undefined variable '{{{{{ref_var}}}}}'."
                        ),
                        node_id=node.node_id,
                        field=f"nodes.{node.node_id}.variables.{ref_var}",
                    )
                )

    for edge in graph.edges:
        if edge.condition is None:
            continue
        for ref_var in sorted(edge.condition.referenced_variables()):
            if ref_var not in declared_vars:
                errors.append(
                    FlowValidationIssue(
                        code="UNDEFINED_VARIABLE",
                        message=(
                            f"Edge '{edge.edge_id}' from node '{edge.source_id}' references "
                            f"undefined variable '{ref_var}'."
                        ),
                        node_id=edge.source_id if edge.source_id in node_map else None,
                        edge_id=edge.edge_id,
                        field=f"edges.{edge.edge_id}.condition",
                    )
                )

    return FlowValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        reachable_node_ids=sorted(all_reachable),
        declared_variables=sorted(declared_vars),
    )
