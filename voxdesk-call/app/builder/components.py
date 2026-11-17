"""Reusable flow components (PART 12, P12-20).

A component is a named, versioned bundle of nodes and edges that several agents can place in their
flow. The hard question is not storage, it is *propagation*: when a component changes, what happens
to the twenty agents using it?

The rule implemented here, and the reason this module exists at all:

* placing a component records a **binding** to a component *version*;
* ``snapshot`` bindings inline that version's graph and never change again on their own;
* ``live-sync`` bindings follow the newest published version, but a publish of the component only
  creates a **draft** on the dependent agent — it never edits a live flow. A customer's phone line
  must not change character because someone else edited a shared block.

Everything is pure data so the rules can be tested without a database; the route layer persists
what this module decides.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

COMPONENT_SYNC_MODES: tuple[str, ...] = ("snapshot", "live-sync")

#: Guards against a component that contains itself, directly or through another component.
MAX_NESTING_DEPTH = 3


class ComponentError(ValueError):
    """A component definition or binding that cannot be honoured."""


def graph_hash(graph: Mapping[str, Any]) -> str:
    """Stable hash of a component graph — the value a bound agent is compared against."""
    canonical = json.dumps(graph, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class FlowComponentVersion:
    version: int
    graph: dict[str, Any]
    changelog: str = ""
    published: bool = False

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("component versions start at 1")
        if not isinstance(self.graph, dict) or "nodes" not in self.graph:
            raise ValueError("a component graph needs a 'nodes' list")
        if not self.graph["nodes"]:
            raise ValueError("a component with no nodes is not a component")

    @property
    def content_hash(self) -> str:
        return graph_hash(self.graph)

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(str(node.get("id") or node.get("node_id")) for node in self.graph["nodes"])


@dataclass(frozen=True)
class FlowComponent:
    component_id: str
    name: str
    versions: tuple[FlowComponentVersion, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.component_id:
            raise ValueError("a component needs an id")
        numbers = [version.version for version in self.versions]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"duplicate component versions for {self.component_id!r}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        """A component may not embed itself, directly or transitively."""
        for version in self.versions:
            for node in version.graph.get("nodes", []):
                params = node.get("params") or {}
                if str(node.get("type")) == "component":
                    ref = str(params.get("component_id") or "")
                    if ref == self.component_id:
                        raise ComponentError(
                            f"component {self.component_id!r} embeds itself (version {version.version})"
                        )

    def latest(self) -> FlowComponentVersion:
        if not self.versions:
            raise ComponentError(f"component {self.component_id!r} has no versions")
        return max(self.versions, key=lambda version: version.version)

    def version(self, number: int) -> FlowComponentVersion:
        for candidate in self.versions:
            if candidate.version == number:
                return candidate
        raise ComponentError(f"component {self.component_id!r} has no version {number}")

    def add_version(self, graph: dict[str, Any], *, changelog: str = "") -> "FlowComponent":
        next_number = (max((v.version for v in self.versions), default=0)) + 1
        version = FlowComponentVersion(next_number, graph, changelog=changelog)
        return FlowComponent(
            component_id=self.component_id,
            name=self.name,
            versions=self.versions + (version,),
            description=self.description,
        )

    def publish(self, number: int | None = None) -> "FlowComponent":
        target = number if number is not None else self.latest().version
        if not any(v.version == target for v in self.versions):
            raise ComponentError(f"component {self.component_id!r} has no version {target}")
        # Exactly one version is live at a time: publishing an older one is a rollback, so the
        # newer version stops being "published" rather than both being true.
        versions = tuple(
            FlowComponentVersion(
                version=v.version,
                graph=v.graph,
                changelog=v.changelog,
                published=(v.version == target),
            )
            for v in self.versions
        )
        return FlowComponent(
            component_id=self.component_id,
            name=self.name,
            versions=versions,
            description=self.description,
        )


@dataclass(frozen=True)
class ComponentBinding:
    """One agent's use of one component."""

    agent_id: str
    component_id: str
    version: int
    sync_mode: str = "snapshot"

    def __post_init__(self) -> None:
        mode = str(self.sync_mode or "snapshot").strip().lower()
        if mode not in COMPONENT_SYNC_MODES:
            raise ValueError(f"sync_mode must be one of {list(COMPONENT_SYNC_MODES)}")
        object.__setattr__(self, "sync_mode", mode)


@dataclass
class ComponentRegistry:
    """In-memory registry + the propagation rules, mirrored by the database layer."""

    components: dict[str, FlowComponent] = field(default_factory=dict)
    bindings: list[ComponentBinding] = field(default_factory=list)

    # -- registry ---------------------------------------------------------
    def register(self, component: FlowComponent) -> FlowComponent:
        self.components[component.component_id] = component
        return component

    def get(self, component_id: str) -> FlowComponent:
        try:
            return self.components[component_id]
        except KeyError as exc:
            raise ComponentError(f"unknown component {component_id!r}") from exc

    # -- bindings ---------------------------------------------------------
    def bind(self, binding: ComponentBinding) -> ComponentBinding:
        component = self.get(binding.component_id)
        component.version(binding.version)  # raises if the version does not exist
        self.bindings.append(binding)
        return binding

    def bindings_for_agent(self, agent_id: str) -> list[ComponentBinding]:
        return [binding for binding in self.bindings if binding.agent_id == agent_id]

    def usage(self, component_id: str) -> int:
        """How many agents depend on this component — the number that makes propagation scary."""
        return sum(1 for binding in self.bindings if binding.component_id == component_id)

    # -- expansion --------------------------------------------------------
    def expand(
        self,
        graph: Mapping[str, Any],
        *,
        agent_id: str | None = None,
        depth: int = 0,
    ) -> dict[str, Any]:
        """Inline every component node in `graph` for a specific agent's bindings.

        Node ids are prefixed with the placement id so two placements of the same component do not
        collide, and edges are rewritten to match. `live-sync` bindings resolve to the newest
        *published* version; `snapshot` bindings stick to the version they were bound to.
        """
        if depth > MAX_NESTING_DEPTH:
            raise ComponentError(f"component nesting deeper than {MAX_NESTING_DEPTH} levels")

        result = dict(graph)
        nodes: list[dict[str, Any]] = []
        parent_edges = [dict(edge) for edge in graph.get("edges") or []]
        component_edges: list[dict[str, Any]] = []
        ports: dict[str, tuple[list[str], list[str]]] = {}

        def endpoint(edge: Mapping[str, Any], side: str) -> str:
            return str(edge.get(f"{side}_id") or edge.get(side) or "")

        def set_endpoints(edge: dict[str, Any], source: str, target: str) -> None:
            # FlowEdge.from_dict prefers *_id fields, while editor payloads often use source/target.
            edge["source"] = edge["source_id"] = source
            edge["target"] = edge["target_id"] = target

        for node in graph.get("nodes") or []:
            if str(node.get("type")) != "component":
                nodes.append(dict(node))
                continue

            params = dict(node.get("params") or {})
            component_id = str(params.get("component_id") or "").strip()
            placement = str(node.get("id") or node.get("node_id") or params.get("id") or component_id)
            if not component_id:
                raise ComponentError(f"component placement {placement!r} has no component_id")
            if placement in ports:
                raise ComponentError(f"duplicate component placement id {placement!r}")
            component = self.get(component_id)

            version_number = params.get("version")
            binding = next(
                (
                    item
                    for item in self.bindings
                    if item.agent_id == (agent_id or "")
                    and item.component_id == component_id
                ),
                None,
            )
            if binding is not None and binding.sync_mode == "live-sync":
                # Live-sync means exactly that: a stale version stamped on the placement
                # cannot pin it to an older component release.
                version_number = self._newest_published(component)
            elif binding is not None:
                # A durable snapshot binding is authoritative over an edited placement node.
                version_number = binding.version
            elif version_number is None:
                version_number = component.latest().version
            try:
                selected_version = int(version_number)
                if (
                    isinstance(version_number, bool)
                    or selected_version < 1
                    or str(selected_version) != str(version_number).strip()
                ):
                    raise ValueError
            except (TypeError, ValueError):
                raise ComponentError(
                    f"component placement {placement!r} has invalid version {version_number!r}"
                ) from None
            version = component.version(selected_version)

            inner = self.expand(version.graph, agent_id=agent_id, depth=depth + 1)
            inner_nodes = [dict(item) for item in inner.get("nodes") or []]
            inner_edges = [dict(item) for item in inner.get("edges") or []]
            id_map: dict[str, str] = {}
            for inner_node in inner_nodes:
                original_id = str(inner_node.get("id") or inner_node.get("node_id") or "")
                if not original_id:
                    raise ComponentError(f"component {component_id!r} contains a node with no id")
                id_map[original_id] = f"{placement}.{original_id}"

            incoming = {endpoint(edge, "target") for edge in inner_edges}
            outgoing = {endpoint(edge, "source") for edge in inner_edges}
            explicit_starts = [
                str(item.get("id") or item.get("node_id") or "")
                for item in inner_nodes
                if str(item.get("type") or item.get("node_type") or "") == "start"
            ]
            entries = explicit_starts or [
                str(item.get("id") or item.get("node_id") or "")
                for item in inner_nodes
                if str(item.get("id") or item.get("node_id") or "") not in incoming
            ]
            exits = [
                str(item.get("id") or item.get("node_id") or "")
                for item in inner_nodes
                if str(item.get("id") or item.get("node_id") or "") not in outgoing
            ]
            if not exits:
                exits = [
                    str(item.get("id") or item.get("node_id") or "")
                    for item in inner_nodes
                    if str(item.get("type") or item.get("node_type") or "")
                    in {"end", "transfer", "transfer_human", "transfer_agent", "subagent"}
                ]
            ports[placement] = (
                [id_map[node_id] for node_id in entries if node_id in id_map],
                [id_map[node_id] for node_id in exits if node_id in id_map],
            )

            for inner_node in inner_nodes:
                clone = dict(inner_node)
                original_id = str(clone.get("id") or clone.get("node_id") or "")
                clone["id"] = id_map[original_id]
                if "node_id" in clone:
                    clone["node_id"] = id_map[original_id]
                nodes.append(clone)

            for inner_edge in inner_edges:
                edge = dict(inner_edge)
                source_id = endpoint(edge, "source")
                target_id = endpoint(edge, "target")
                if source_id not in id_map or target_id not in id_map:
                    raise ComponentError(
                        f"component {component_id!r} has an edge to a missing node"
                    )
                set_endpoints(edge, id_map[source_id], id_map[target_id])
                old_edge_id = str(edge.get("id") or edge.get("edge_id") or f"e_{source_id}_{target_id}")
                edge["id"] = edge["edge_id"] = f"{placement}.{old_edge_id}"
                component_edges.append(edge)

        expanded_parent_edges: list[dict[str, Any]] = []
        for parent_edge in parent_edges:
            source_id = endpoint(parent_edge, "source")
            target_id = endpoint(parent_edge, "target")
            source_options = ports.get(source_id, ([], []))[1] if source_id in ports else [source_id]
            target_options = ports.get(target_id, ([], []))[0] if target_id in ports else [target_id]
            if source_id in ports and not source_options:
                raise ComponentError(
                    f"component placement {source_id!r} has no exit for an outgoing edge"
                )
            if target_id in ports and not target_options:
                raise ComponentError(
                    f"component placement {target_id!r} has no entry for an incoming edge"
                )

            pairs = [(source, target) for source in source_options for target in target_options]
            for index, (source, target) in enumerate(pairs):
                edge = dict(parent_edge)
                set_endpoints(edge, source, target)
                if len(pairs) > 1:
                    old_edge_id = str(edge.get("id") or edge.get("edge_id") or "edge")
                    edge["id"] = edge["edge_id"] = f"{old_edge_id}.component{index + 1}"
                expanded_parent_edges.append(edge)

        result["nodes"] = nodes
        result["edges"] = [*expanded_parent_edges, *component_edges]
        return result

    @staticmethod
    def _newest_published(component: FlowComponent) -> int:
        published = [v.version for v in component.versions if v.published]
        if not published:
            raise ComponentError(f"component {component.component_id!r} has no published version")
        return max(published)

    # -- propagation ------------------------------------------------------
    def propagation_plan(
        self, component_id: str, new_version: int
    ) -> list[dict[str, Any]]:
        """What a new component version means for its dependents.

        Never "update the live agent": `live-sync` dependents get a **draft**, `snapshot` dependents
        are told their binding is behind but nothing is created. The plan is returned as data so
        the API can show it before anything is written.
        """
        component = self.get(component_id)
        component.version(new_version)
        plan: list[dict[str, Any]] = []
        for binding in self.bindings:
            if binding.component_id != component_id:
                continue
            if binding.sync_mode == "live-sync":
                plan.append(
                    {
                        "agent_id": binding.agent_id,
                        "action": "create_draft",
                        "from_version": binding.version,
                        "to_version": new_version,
                        "touches_live": False,
                    }
                )
            else:
                plan.append(
                    {
                        "agent_id": binding.agent_id,
                        "action": "notify_only",
                        "from_version": binding.version,
                        "to_version": new_version,
                        "touches_live": False,
                    }
                )
        return plan


def summarize_usage(bindings: Iterable[ComponentBinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for binding in bindings:
        counts[binding.component_id] = counts.get(binding.component_id, 0) + 1
    return counts


async def load_agent_component_registry(
    session: Any,
    *,
    tenant_id: Any,
    agent_id: Any,
    environment_id: Any | None = None,
) -> ComponentRegistry:
    """Load the tenant's component versions and one agent's bindings for live execution."""
    from sqlalchemy import or_, select

    from app.db.component_models import (
        AgentComponentBinding as BindingRow,
        FlowComponent as ComponentRow,
        FlowComponentVersion as VersionRow,
    )

    components = (
        await session.execute(
            select(ComponentRow).where(ComponentRow.tenant_id == tenant_id)
        )
    ).scalars().all()
    versions = (
        await session.execute(
            select(VersionRow).where(VersionRow.tenant_id == tenant_id)
        )
    ).scalars().all()

    binding_query = select(BindingRow).where(
        BindingRow.tenant_id == tenant_id,
        BindingRow.agent_id == agent_id,
    )
    if environment_id is not None:
        binding_query = binding_query.where(
            or_(
                BindingRow.environment_id.is_(None),
                BindingRow.environment_id == environment_id,
            )
        )
    bindings = (await session.execute(binding_query)).scalars().all()

    versions_by_component: dict[str, list[FlowComponentVersion]] = {}
    for row in versions:
        versions_by_component.setdefault(str(row.component_id), []).append(
            FlowComponentVersion(
                version=int(row.version),
                graph=dict(row.graph or {}),
                changelog=str(row.changelog or ""),
                published=bool(row.published),
            )
        )

    registry = ComponentRegistry()
    for row in components:
        component_id = str(row.id)
        registry.register(
            FlowComponent(
                component_id=component_id,
                name=str(row.name),
                versions=tuple(
                    sorted(
                        versions_by_component.get(component_id, []),
                        key=lambda version: version.version,
                    )
                ),
                description=str(row.description or ""),
            )
        )

    for row in bindings:
        registry.bind(
            ComponentBinding(
                agent_id=str(row.agent_id),
                component_id=str(row.component_id),
                version=int(row.version),
                sync_mode=str(row.sync_mode or "snapshot"),
            )
        )
    return registry
