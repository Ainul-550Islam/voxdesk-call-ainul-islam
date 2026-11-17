"""PART 12 / P12-21 — three-way merge of agent versions and flow graphs."""

from __future__ import annotations

import pytest

from app.services.agent_version_merge_service import (
    MergeError,
    apply_resolutions,
    merge_snapshots,
    merge_three_way,
)

BASE = {
    "name": "Agent",
    "greeting": "Hi",
    "voice": {"voice_id": "v1", "speed": 1.0},
}


def test_a_change_on_one_side_is_taken() -> None:
    result = merge_three_way(BASE, {**BASE, "greeting": "Hello"}, BASE)
    assert result.clean is True
    assert result.merged["greeting"] == "Hello"


def test_the_same_change_on_both_sides_is_not_a_conflict() -> None:
    result = merge_three_way(BASE, {**BASE, "greeting": "Hello"}, {**BASE, "greeting": "Hello"})
    assert result.clean is True
    assert result.merged["greeting"] == "Hello"


def test_different_changes_to_the_same_key_conflict() -> None:
    result = merge_three_way(BASE, {**BASE, "greeting": "Hello"}, {**BASE, "greeting": "Hey"})
    assert result.clean is False
    assert result.conflicts[0].path == "greeting"
    assert result.conflicts[0].ours == "Hello"
    assert result.conflicts[0].theirs == "Hey"


def test_nested_keys_merge_independently() -> None:
    ours = {**BASE, "voice": {"voice_id": "v2", "speed": 1.0}}
    theirs = {**BASE, "voice": {"voice_id": "v1", "speed": 1.2}}
    result = merge_three_way(BASE, ours, theirs)
    assert result.clean is True
    assert result.merged["voice"] == {"voice_id": "v2", "speed": 1.2}


def test_a_deletion_on_one_side_and_a_change_on_the_other_conflicts() -> None:
    ours = {"name": "Agent", "voice": BASE["voice"]}  # greeting deleted
    theirs = {**BASE, "greeting": "Hello"}
    result = merge_three_way(BASE, ours, theirs)
    assert any(conflict.path == "greeting" for conflict in result.conflicts)


def test_the_strategy_only_decides_the_fallback() -> None:
    ours = {**BASE, "greeting": "Hello"}
    theirs = {**BASE, "greeting": "Hey"}
    assert merge_three_way(BASE, ours, theirs, strategy="ours").merged["greeting"] == "Hello"
    assert merge_three_way(BASE, ours, theirs, strategy="theirs").merged["greeting"] == "Hey"
    assert merge_three_way(BASE, ours, theirs, strategy="manual").merged["greeting"] == "Hi"


def test_a_human_resolution_clears_the_conflict() -> None:
    ours = {**BASE, "greeting": "Hello"}
    theirs = {**BASE, "greeting": "Hey"}
    result = merge_three_way(BASE, ours, theirs)
    resolved = apply_resolutions(result, {"greeting": "theirs"})
    assert resolved.clean is True
    assert resolved.merged["greeting"] == "Hey"


def test_an_unresolved_path_stays_a_conflict() -> None:
    ours = {**BASE, "greeting": "Hello"}
    theirs = {**BASE, "greeting": "Hey"}
    result = apply_resolutions(merge_three_way(BASE, ours, theirs), {"name": "ours"})
    assert len(result.conflicts) == 1


def test_the_base_document_is_not_mutated() -> None:
    snapshot = dict(BASE)
    merge_three_way(BASE, {**BASE, "greeting": "Hello"}, BASE)
    assert snapshot == BASE


def test_an_unknown_strategy_is_refused() -> None:
    with pytest.raises(MergeError, match="strategy must be one of"):
        merge_three_way(BASE, BASE, BASE, strategy="mine")


def test_a_snapshot_merge_covers_config_and_flow_together() -> None:
    base = {"config": BASE, "flow": {"nodes": [{"id": "a"}]}}
    ours = {"config": {**BASE, "greeting": "Hello"}, "flow": {"nodes": [{"id": "a"}, {"id": "b"}]}}
    theirs = {"config": BASE, "flow": base["flow"]}
    result = merge_snapshots(base_snapshot=base, ours_snapshot=ours, theirs_snapshot=theirs)
    assert result["clean"] is True
    assert result["merged"]["config"]["greeting"] == "Hello"
    assert len(result["merged"]["flow"]["nodes"]) == 2


def test_a_flow_conflict_is_named_with_the_flow_prefix() -> None:
    base = {"config": {}, "flow": {"mode": "rigid"}}
    ours = {"config": {}, "flow": {"mode": "flex"}}
    theirs = {"config": {}, "flow": {"mode": "rigid", "extra": 1}}
    result = merge_snapshots(base_snapshot=base, ours_snapshot=ours, theirs_snapshot=theirs)
    assert result["clean"] is True, "different keys are not a conflict"


def test_conflicting_flow_values_are_reported() -> None:
    base = {"config": {}, "flow": {"mode": "rigid"}}
    ours = {"config": {}, "flow": {"mode": "flex"}}
    theirs = {"config": {}, "flow": {"mode": "rigid", "mode_extra": "flex"}}
    result = merge_snapshots(base_snapshot=base, ours_snapshot=ours, theirs_snapshot=theirs)
    assert result["clean"] is True
    conflicting = merge_snapshots(
        base_snapshot=base,
        ours_snapshot={"config": {}, "flow": {"mode": "flex"}},
        theirs_snapshot={"config": {}, "flow": {"mode": "rigid2"}},
    )
    assert conflicting["clean"] is False
    assert conflicting["conflicts"][0]["path"].startswith("flow.")
