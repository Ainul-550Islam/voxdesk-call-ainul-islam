"""PART 12 / P12-20 — components: versioning, expansion, propagation never touches live."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from app.builder.flow_validation import validate_flow
from app.builder.flow_runner import FlowRunner
from app.builder.components import (
    COMPONENT_SYNC_MODES,
    ComponentBinding,
    ComponentError,
    ComponentRegistry,
    FlowComponent,
    FlowComponentVersion,
    graph_hash,
    load_agent_component_registry,
    summarize_usage,
)

GRAPH = {
    "nodes": [
        {"id": "greet", "type": "conversation", "params": {"prompt": "Hi"}},
        {"id": "bye", "type": "end", "params": {}},
    ],
    "edges": [{"source": "greet", "target": "bye"}],
}


def _component(versions: int = 1) -> FlowComponent:
    component = FlowComponent("comp_greeting", "Greeting", description="hello block")
    for _ in range(versions):
        component = component.add_version(GRAPH, changelog="initial")
    return component.publish()


def test_a_version_requires_a_graph_with_nodes() -> None:
    with pytest.raises(ValueError, match="needs a 'nodes'"):
        FlowComponentVersion(1, {"edges": []})
    with pytest.raises(ValueError, match="no nodes"):
        FlowComponentVersion(1, {"nodes": []})


def test_versions_start_at_one_and_increment() -> None:
    component = _component(versions=2)
    assert [v.version for v in component.versions] == [1, 2]
    assert component.latest().version == 2


def test_duplicate_versions_are_refused() -> None:
    version = FlowComponentVersion(1, GRAPH)
    with pytest.raises(ValueError, match="duplicate component versions"):
        FlowComponent("c", "C", versions=(version, version))


def test_runtime_refuses_component_nodes_without_a_registry() -> None:
    with pytest.raises(ValueError, match="component_registry is required"):
        FlowRunner(
            {
                "nodes": [
                    {
                        "id": "placement",
                        "type": "component",
                        "params": {"component_id": "comp_greeting", "version": 1},
                    }
                ],
                "edges": [],
            }
        )


def test_a_component_cannot_embed_itself() -> None:
    selfish = {
        "nodes": [{"id": "x", "type": "component", "params": {"component_id": "c_loop"}}],
        "edges": [],
    }
    with pytest.raises(ComponentError, match="embeds itself"):
        FlowComponent("c_loop", "Loop", versions=(FlowComponentVersion(1, selfish),))


def test_publish_marks_a_version_and_only_that_version() -> None:
    component = _component(versions=2).publish(1)
    assert component.version(1).published is True
    assert component.version(2).published is False


def test_expansion_prefixes_ids_and_rewrites_edges() -> None:
    registry = ComponentRegistry()
    registry.register(_component())
    expanded = registry.expand(
        {"nodes": [{"id": "place_1", "type": "component", "params": {"component_id": "comp_greeting"}}], "edges": []}
    )
    assert [node["id"] for node in expanded["nodes"]] == ["place_1.greet", "place_1.bye"]
    edge = expanded["edges"][0]
    assert (edge["source_id"], edge["target_id"]) == (
        "place_1.greet",
        "place_1.bye",
    )
    assert edge["id"] == "place_1.e_greet_bye"


def test_parent_edges_are_rewired_through_component_entries_and_exits() -> None:
    component_graph = {
        "nodes": [
            {"id": "greet", "type": "conversation"},
            {"id": "collect", "type": "conversation"},
        ],
        "edges": [
            {
                "id": "inside",
                "source": "greet",
                "target": "collect",
                "source_id": "greet",
                "target_id": "collect",
            }
        ],
    }
    registry = ComponentRegistry()
    registry.register(
        FlowComponent("block", "Block").add_version(component_graph).publish()
    )
    expanded = registry.expand(
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "placement",
                    "type": "component",
                    "params": {"component_id": "block", "version": 1},
                },
                {"id": "done", "type": "end"},
            ],
            "edges": [
                {"id": "into", "source": "start", "target": "placement"},
                {"id": "out", "source": "placement", "target": "done"},
            ],
        }
    )

    connections = {
        (edge["source_id"], edge["target_id"])
        for edge in expanded["edges"]
    }
    assert connections == {
        ("start", "placement.greet"),
        ("placement.greet", "placement.collect"),
        ("placement.collect", "done"),
    }
    assert all(
        "placement" not in {edge["source_id"], edge["target_id"]}
        for edge in expanded["edges"]
    )
    assert len({edge["id"] for edge in expanded["edges"]}) == 3


def test_two_placements_do_not_collide() -> None:
    registry = ComponentRegistry()
    registry.register(_component())
    expanded = registry.expand(
        {
            "nodes": [
                {"id": "a", "type": "component", "params": {"component_id": "comp_greeting"}},
                {"id": "b", "type": "component", "params": {"component_id": "comp_greeting"}},
            ],
            "edges": [],
        }
    )
    ids = [node["id"] for node in expanded["nodes"]]
    assert len(ids) == len(set(ids)) == 4


def test_live_sync_follows_the_newest_published_version() -> None:
    registry = ComponentRegistry()
    registry.register(_component(versions=1))
    registry.bind(ComponentBinding("agent_1", "comp_greeting", 1, "live-sync"))
    component = registry.get("comp_greeting").add_version(
        {"nodes": [{"id": "only", "type": "end"}], "edges": []}
    ).publish(2)
    registry.register(component)

    expanded = registry.expand(
        {
            "nodes": [
                {
                    "id": "p31",
                    "type": "component",
                    "params": {"component_id": "comp_greeting", "version": 1},
                }
            ],
            "edges": [],
        },
        agent_id="agent_1",
    )
    assert [node["id"] for node in expanded["nodes"]] == ["p31.only"]


def test_a_snapshot_binding_ignores_newer_versions() -> None:
    registry = ComponentRegistry()
    registry.register(_component(versions=1))
    registry.bind(ComponentBinding("agent_1", "comp_greeting", 1, "snapshot"))
    registry.register(
        registry.get("comp_greeting")
        .add_version({"nodes": [{"id": "only", "type": "end"}], "edges": []})
        .publish(2)
    )
    expanded = registry.expand(
        {
            "nodes": [
                {
                    "id": "p1",
                    "type": "component",
                    "params": {"component_id": "comp_greeting", "version": 2},
                }
            ],
            "edges": [],
        },
        agent_id="agent_1",
    )
    assert [node["id"] for node in expanded["nodes"]] == ["p1.greet", "p1.bye"]


def test_propagation_creates_a_draft_and_never_touches_live() -> None:
    registry = ComponentRegistry()
    registry.register(_component(versions=1))
    registry.bind(ComponentBinding("agent_live", "comp_greeting", 1, "live-sync"))
    registry.bind(ComponentBinding("agent_frozen", "comp_greeting", 1, "snapshot"))

    plan = registry.propagation_plan("comp_greeting", 1)
    assert {item["agent_id"]: item["action"] for item in plan} == {
        "agent_live": "create_draft",
        "agent_frozen": "notify_only",
    }
    assert all(item["touches_live"] is False for item in plan)


def test_flow_validation_rejects_component_without_a_pinned_version() -> None:
    result = validate_flow(
        {
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "placement",
                    "type": "component",
                    "params": {"component_id": "comp_greeting"},
                },
                {"id": "done", "type": "end"},
            ],
            "edges": [
                {"source": "start", "target": "placement"},
                {"source": "placement", "target": "done"},
            ],
        }
    )
    assert any(issue.code == "COMPONENT_VERSION_MISSING" for issue in result.errors)


def test_binding_a_missing_version_is_refused() -> None:
    registry = ComponentRegistry()
    registry.register(_component())
    with pytest.raises(ComponentError, match="no version 9"):
        registry.bind(ComponentBinding("agent_1", "comp_greeting", 9))


def test_unknown_sync_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="sync_mode"):
        ComponentBinding("a", "c", 1, "auto-magic")
    assert set(COMPONENT_SYNC_MODES) == {"snapshot", "live-sync"}


def test_usage_counts_the_agents_at_risk() -> None:
    bindings = [
        ComponentBinding("a1", "comp_greeting", 1),
        ComponentBinding("a2", "comp_greeting", 1),
        ComponentBinding("a3", "comp_other", 1),
    ]
    assert summarize_usage(bindings) == {"comp_greeting": 2, "comp_other": 1}


def test_graph_hash_is_stable_and_content_sensitive() -> None:
    assert graph_hash(GRAPH) == graph_hash(dict(reversed(list(GRAPH.items()))))
    changed = {"nodes": GRAPH["nodes"], "edges": []}
    assert graph_hash(GRAPH) != graph_hash(changed)


@pytest.mark.asyncio
async def test_live_registry_loader_honors_agent_snapshot_binding() -> None:
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    component_id = uuid.uuid4()
    component_row = SimpleNamespace(
        id=component_id,
        name="Greeting",
        description="Reusable greeting",
    )
    version_rows = [
        SimpleNamespace(
            component_id=component_id,
            version=1,
            graph={"nodes": [{"id": "snapshot", "type": "conversation"}], "edges": []},
            changelog="frozen",
            published=False,
        ),
        SimpleNamespace(
            component_id=component_id,
            version=2,
            graph={"nodes": [{"id": "latest", "type": "conversation"}], "edges": []},
            changelog="new release",
            published=True,
        ),
    ]
    binding_row = SimpleNamespace(
        agent_id=agent_id,
        component_id=component_id,
        version=1,
        sync_mode="snapshot",
    )

    class _Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class _Session:
        def __init__(self):
            self.rows = iter(([component_row], version_rows, [binding_row]))

        async def execute(self, _statement):
            return _Result(next(self.rows))

    registry = await load_agent_component_registry(
        _Session(),
        tenant_id=tenant_id,
        agent_id=agent_id,
    )
    expanded = registry.expand(
        {
            "nodes": [
                {
                    "id": "placement",
                    "type": "component",
                    "params": {"component_id": str(component_id)},
                }
            ],
            "edges": [],
        },
        agent_id=str(agent_id),
    )
    assert [node["id"] for node in expanded["nodes"]] == ["placement.snapshot"]
