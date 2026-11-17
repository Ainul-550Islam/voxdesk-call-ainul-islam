"""Three-way merge of agent versions (PART 12, P12-21).

Two people edit the same agent, both save, both publish. Retell's answer is a three-way merge with
a conflict report, and the important half is the *report*: a merge that silently picks a winner is
worse than one that refuses, because the loser finds out from a customer.

So this module merges what it can prove is safe and lists everything else:

* a key changed on only one side takes that side's value;
* a key changed to the same value on both sides is not a conflict;
* a key changed differently on both sides is a conflict, and the base value is what we fall back to
  so the merged document stays valid rather than half-applied;
* a key deleted on one side and untouched on the other stays deleted;
* a key deleted on one side and modified on the other is a conflict.

It is pure: no database, no HTTP. The route loads the three snapshots and persists the result.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

MERGE_STRATEGIES: tuple[str, ...] = ("ours", "theirs", "manual")

_MISSING = object()
#: A key that exists in the base and was *removed* on one side. Kept distinct from `_MISSING`
#: ("this side never touched the key"), because delete-vs-modify is a conflict while
#: untouched-vs-modify is an automatic merge.
_DELETED = object()


class MergeError(ValueError):
    """The merge cannot be attempted at all (not a conflict — a malformed request)."""


@dataclass(frozen=True)
class MergeConflict:
    path: str
    base: Any = None
    ours: Any = None
    theirs: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "base": self.base,
            "ours": self.ours,
            "theirs": self.theirs,
        }


@dataclass
class MergeResult:
    merged: dict[str, Any]
    conflicts: list[MergeConflict] = field(default_factory=list)
    auto_resolved: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.conflicts

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged": self.merged,
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "auto_resolved": list(self.auto_resolved),
            "clean": self.clean,
        }


def _diff_paths(base: Any, other: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten `other` into `path -> value` for every leaf that differs from `base`."""
    changes: dict[str, Any] = {}
    if isinstance(base, Mapping) and isinstance(other, Mapping):
        for key in set(base) | set(other):
            path = f"{prefix}.{key}" if prefix else str(key)
            left = base.get(key, _MISSING)
            right = other.get(key, _MISSING)
            if right is _MISSING:
                changes[path] = _DELETED
            elif left is _MISSING:
                changes[path] = right
            else:
                changes.update(_diff_paths(left, right, path))
        return changes
    if base is not _MISSING and other is _MISSING:
        changes[prefix] = _DELETED
    elif base != other:
        changes[prefix] = other
    return changes


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        node = cursor.get(part)
        if not isinstance(node, dict):
            node = {}
            cursor[part] = node
        cursor = node
    if value is _MISSING:
        cursor.pop(parts[-1], None)
    else:
        cursor[parts[-1]] = copy.deepcopy(value)


def _get_path(source: Mapping[str, Any], path: str) -> Any:
    cursor: Any = source
    for part in path.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            return _MISSING
        cursor = cursor[part]
    return cursor


def _apply(target: dict[str, Any], path: str, value: Any) -> None:
    """Write a diff value into the merged document, honouring deletions."""
    if value is _DELETED:
        _set_path(target, path, _MISSING)
    else:
        _set_path(target, path, value)


def _same(left: Any, right: Any) -> bool:
    if left is _DELETED or right is _DELETED:
        return left is _DELETED and right is _DELETED
    return _equal(left, right)


def _equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)


def merge_three_way(
    base: Mapping[str, Any] | None,
    ours: Mapping[str, Any] | None,
    theirs: Mapping[str, Any] | None,
    *,
    strategy: str = "ours",
) -> MergeResult:
    """Merge `ours` and `theirs` on top of `base`, reporting every real conflict."""
    if strategy not in MERGE_STRATEGIES:
        raise MergeError(f"strategy must be one of {list(MERGE_STRATEGIES)}")
    base = dict(base or {})
    ours = dict(ours or {})
    theirs = dict(theirs or {})

    merged: dict[str, Any] = copy.deepcopy(base)
    ours_changes = _diff_paths(base, ours)
    theirs_changes = _diff_paths(base, theirs)
    conflicts: list[MergeConflict] = []
    auto: list[str] = []

    for path in sorted(set(ours_changes) | set(theirs_changes)):
        left = ours_changes.get(path, _MISSING)
        right = theirs_changes.get(path, _MISSING)
        base_value = _get_path(base, path) if path else _MISSING

        if left is _MISSING and right is _MISSING:
            continue

        # Only one side touched the key.
        if left is _MISSING:
            _apply(merged, path, right)
            auto.append(path)
            continue
        if right is _MISSING:
            _apply(merged, path, left)
            auto.append(path)
            continue

        # Both sides touched it: identical values (including identical deletions) are fine.
        if _same(left, right):
            _apply(merged, path, left)
            auto.append(path)
            continue

        conflicts.append(
            MergeConflict(
                path=path,
                base=None if base_value is _MISSING else base_value,
                ours=None if left is _DELETED else left,
                theirs=None if right is _DELETED else right,
            )
        )
        chosen = left if strategy == "ours" else right if strategy == "theirs" else base_value
        if chosen is not _MISSING:
            _apply(merged, path, chosen)

    return MergeResult(merged=merged, conflicts=conflicts, auto_resolved=auto)


def apply_resolutions(result: MergeResult, resolutions: Mapping[str, str]) -> MergeResult:
    """Apply a human's per-path choice (`ours` / `theirs`) to a merge result."""
    merged = copy.deepcopy(result.merged)
    remaining: list[MergeConflict] = []
    for conflict in result.conflicts:
        choice = str(resolutions.get(conflict.path, "")).strip().lower()
        if choice == "ours":
            _set_path(merged, conflict.path, conflict.ours)
        elif choice == "theirs":
            _set_path(merged, conflict.path, conflict.theirs)
        else:
            remaining.append(conflict)
    return MergeResult(merged=merged, conflicts=remaining, auto_resolved=result.auto_resolved)


def merge_snapshots(
    *,
    base_snapshot: Mapping[str, Any],
    ours_snapshot: Mapping[str, Any],
    theirs_snapshot: Mapping[str, Any],
    strategy: str = "ours",
) -> dict[str, Any]:
    """Convenience wrapper that merges an agent config *and* its flow graph in one call.

    The two documents are merged independently and reported together, because that is how a
    reviewer sees them: one dialog, one conflict list.
    """
    config = merge_three_way(
        base_snapshot.get("config") or {},
        ours_snapshot.get("config") or {},
        theirs_snapshot.get("config") or {},
        strategy=strategy,
    )
    flow = merge_three_way(
        base_snapshot.get("flow") or {},
        ours_snapshot.get("flow") or {},
        theirs_snapshot.get("flow") or {},
        strategy=strategy,
    )
    return {
        "merged": {"config": config.merged, "flow": flow.merged},
        "conflicts": [c.as_dict() for c in config.conflicts]
        + [dict(c.as_dict(), path=f"flow.{c.path}") for c in flow.conflicts],
        "clean": config.clean and flow.clean,
    }
