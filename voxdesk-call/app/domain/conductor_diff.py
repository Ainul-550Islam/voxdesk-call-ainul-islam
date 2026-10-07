"""Deterministic structured diff and candidate materialization engine for Conductor.

Guarantees:
1. Two identical configurations and change sets always produce identical diff ordering and SHA-256 hashes.
2. Never asks an LLM to decide whether its own output is the authoritative diff.
3. Supports path resolution (`voice_config.speed`, `tools`, `guardrails.max_turns`),
   structured JSON diffs, grouped-by-section diffs, and unified line diffs for text fields.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from typing import Any

from app.core.errors import BadRequestError
from app.domain.conductor_models import ConductorFieldDiffItem, ConductorProposalDiffResponse
from app.security.policy import (
    classify_change_risk,
    classify_path_section,
    validate_mutation_operation,
    validate_mutation_path,
    validate_value_safety,
)

_INDEX_SEGMENT_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\[(\d+)\]$")


def canonical_config_hash(snapshot: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash over a normalized configuration dict."""
    serialized = json.dumps(snapshot or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def path_to_json_pointer(path: str) -> str:
    """Convert dot/bracket path (`voice_config.speed` or `tools[0]`) to RFC 6901 JSON Pointer."""
    tokens: list[str] = []
    for raw_seg in str(path or "").strip().split("."):
        if not raw_seg:
            continue
        m = _INDEX_SEGMENT_RE.match(raw_seg)
        if m:
            tokens.append(m.group(1))
            tokens.append(m.group(2))
        else:
            tokens.append(raw_seg)
    escaped = [t.replace("~", "~0").replace("/", "~1") for t in tokens]
    return "/" + "/".join(escaped)


def get_value_at_path(snapshot: dict[str, Any], path: str) -> Any:
    """Read the current value at ``path`` inside ``snapshot`` (returns ``None`` if absent)."""
    current: Any = snapshot
    for seg in str(path or "").strip().split("."):
        if current is None:
            return None
        m = _INDEX_SEGMENT_RE.match(seg)
        if m:
            key, idx_str = m.group(1), int(m.group(2))
            if not isinstance(current, dict) or key not in current:
                return None
            arr = current[key]
            if not isinstance(arr, list) or idx_str < 0 or idx_str >= len(arr):
                return None
            current = arr[idx_str]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(seg)
    return copy.deepcopy(current)


def apply_single_operation(
    snapshot: dict[str, Any],
    *,
    path: str,
    operation: str,
    new_value: Any = None,
    from_path: str | None = None,
) -> dict[str, Any]:
    """Deterministically apply a single allowlisted operation to a deep copy of ``snapshot``."""
    clean_path = validate_mutation_path(path)
    op = validate_mutation_operation(operation)
    if op not in {"remove", "delete"}:
        validate_value_safety(new_value, path=clean_path)

    out = copy.deepcopy(snapshot or {})
    segments = [s for s in clean_path.split(".") if s]
    if not segments:
        raise BadRequestError("Empty mutation path.")

    # Navigate/create parent containers
    parent: Any = out
    for seg in segments[:-1]:
        m = _INDEX_SEGMENT_RE.match(seg)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(parent, dict):
                raise BadRequestError(f"Cannot traverse non-object at {seg!r}.")
            arr = parent.setdefault(key, [])
            if not isinstance(arr, list) or idx >= len(arr):
                raise BadRequestError(f"Array index out of bounds at {seg!r}.")
            parent = arr[idx]
        else:
            if not isinstance(parent, dict):
                raise BadRequestError(f"Cannot traverse non-object at {seg!r}.")
            if seg not in parent or not isinstance(parent[seg], dict):
                parent[seg] = {}
            parent = parent[seg]

    leaf = segments[-1]
    leaf_match = _INDEX_SEGMENT_RE.match(leaf)

    if op == "move":
        src_path = from_path or (
            str(new_value.get("from_path")) if isinstance(new_value, dict) and "from_path" in new_value else None
        )
        if not src_path:
            raise BadRequestError("Operation 'move' requires 'from_path'.")
        validate_mutation_path(src_path)
        moved_val = get_value_at_path(out, src_path)
        out = apply_single_operation(out, path=src_path, operation="delete")
        target_val = (
            new_value.get("value", moved_val)
            if isinstance(new_value, dict) and "value" in new_value
            else moved_val
        )
        return apply_single_operation(out, path=clean_path, operation="set", new_value=target_val)

    if leaf_match:
        key, idx = leaf_match.group(1), int(leaf_match.group(2))
        arr = parent.setdefault(key, [])
        if not isinstance(arr, list):
            raise BadRequestError(f"Target {key!r} is not a list.")
        if op in {"set", "replace"}:
            if idx < 0 or idx >= len(arr):
                raise BadRequestError(f"List index {idx} out of range for {key!r}.")
            arr[idx] = copy.deepcopy(new_value)
        elif op in {"remove", "delete"}:
            if 0 <= idx < len(arr):
                arr.pop(idx)
        elif op in {"add", "append"}:
            arr.insert(min(idx, len(arr)), copy.deepcopy(new_value))
        return out

    if not isinstance(parent, dict):
        raise BadRequestError(f"Parent container for {leaf!r} is not an object.")

    if op in {"set", "replace"}:
        parent[leaf] = copy.deepcopy(new_value)
    elif op in {"add", "append"}:
        current_leaf = parent.get(leaf)
        if isinstance(current_leaf, list):
            items_to_add = new_value if isinstance(new_value, list) else [new_value]
            for item in items_to_add:
                if item not in current_leaf:
                    current_leaf.append(copy.deepcopy(item))
        elif current_leaf is None and leaf in {
            "tools",
            "knowledge_base_ids",
            "workflow_ids",
            "boosted_keywords",
            "backchannel_words",
        }:
            parent[leaf] = (
                copy.deepcopy(new_value)
                if isinstance(new_value, list)
                else [copy.deepcopy(new_value)]
            )
        elif isinstance(current_leaf, dict) and isinstance(new_value, dict):
            current_leaf.update(copy.deepcopy(new_value))
        else:
            parent[leaf] = copy.deepcopy(new_value)
    elif op in {"remove", "delete"}:
        current_leaf = parent.get(leaf)
        if isinstance(current_leaf, list) and new_value is not None:
            targets = new_value if isinstance(new_value, list) else [new_value]
            parent[leaf] = [x for x in current_leaf if x not in targets]
        else:
            parent.pop(leaf, None)

    return out


def apply_operations_to_snapshot(
    base_snapshot: dict[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Materialize a candidate configuration snapshot by applying ordered operations."""
    candidate = copy.deepcopy(base_snapshot or {})
    ordered = sorted(
        operations,
        key=lambda item: (int(item.get("sequence", 0)), str(item.get("path", ""))),
    )
    for op_spec in ordered:
        candidate = apply_single_operation(
            candidate,
            path=str(op_spec["path"]),
            operation=str(op_spec["operation"]),
            new_value=op_spec.get("new_value"),
            from_path=op_spec.get("from_path"),
        )
    return candidate


def compute_line_diff(old_value: Any, new_value: Any) -> tuple[bool, list[str]]:
    """Compute a readable line-oriented unified diff when values are multiline or text strings."""
    if isinstance(old_value, str) or isinstance(new_value, str):
        old_str = "" if old_value is None else str(old_value)
        new_str = "" if new_value is None else str(new_value)
        old_lines = old_str.splitlines() or [old_str]
        new_lines = new_str.splitlines() or [new_str]
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile="base",
                tofile="proposed",
                lineterm="",
            )
        )
        if not diff_lines and old_str != new_str:
            diff_lines = [f"- {old_str}", f"+ {new_str}"]
        return True, diff_lines

    old_json = json.dumps(old_value, indent=2, sort_keys=True, default=str).splitlines()
    new_json = json.dumps(new_value, indent=2, sort_keys=True, default=str).splitlines()
    diff_lines = list(
        difflib.unified_diff(
            old_json,
            new_json,
            fromfile="base",
            tofile="proposed",
            lineterm="",
        )
    )
    return False, diff_lines


def stable_change_id(sequence: int, path: str, operation: str, old_value: Any, new_value: Any) -> str:
    """Compute a deterministic identifier for a single diff item."""
    payload = json.dumps(
        {
            "sequence": int(sequence),
            "path": str(path),
            "operation": str(operation),
            "old": old_value,
            "new": new_value,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "chg_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_proposal_diff(
    *,
    proposal_id: Any,
    agent_id: str,
    base_version_number: int,
    base_snapshot: dict[str, Any],
    changes: list[dict[str, Any]],
) -> ConductorProposalDiffResponse:
    """Build a deterministic structured, grouped, and side-by-side diff for a ConductorProposal."""
    ordered_changes = sorted(
        changes,
        key=lambda c: (int(c.get("sequence", 0)), str(c.get("path", "")), str(c.get("id", ""))),
    )

    all_ops: list[dict[str, Any]] = []
    approved_ops: list[dict[str, Any]] = []
    diff_items: list[ConductorFieldDiffItem] = []
    grouped: dict[str, list[ConductorFieldDiffItem]] = {}

    approved_count = 0
    rejected_count = 0
    pending_count = 0

    for idx, chg in enumerate(ordered_changes):
        seq = int(chg.get("sequence", idx))
        path = str(chg.get("path", ""))
        op = str(chg.get("operation", "set")).lower()
        old_val = chg.get("old_value")
        new_val = chg.get("new_value")
        section = str(chg.get("section") or classify_path_section(path))
        risk = str(chg.get("risk_level") or classify_change_risk(path, op, new_val))
        approval = str(chg.get("approval_state") or "pending").lower()
        val_state = str(chg.get("validation_state") or "pending")
        sim_state = str(chg.get("simulation_state") or "not_run")
        reason = str(chg.get("reason") or "")

        all_ops.append(
            {
                "sequence": seq,
                "path": path,
                "operation": op,
                "new_value": new_val,
            }
        )
        if approval == "approved":
            approved_count += 1
            approved_ops.append(
                {
                    "sequence": seq,
                    "path": path,
                    "operation": op,
                    "new_value": new_val,
                }
            )
        elif approval == "rejected":
            rejected_count += 1
        else:
            pending_count += 1

        is_text, line_diff = compute_line_diff(old_val, new_val)
        cid = str(chg.get("id") or stable_change_id(seq, path, op, old_val, new_val))
        item = ConductorFieldDiffItem(
            change_id=cid,
            sequence=seq,
            section=section,
            json_pointer=path_to_json_pointer(path),
            path=path,
            operation=op,
            old_value=old_val,
            new_value=new_val,
            reason=reason,
            risk_level=risk,
            approval_state=approval,
            validation_state=val_state,
            simulation_state=sim_state,
            is_text_diff=is_text,
            line_diff=line_diff,
        )
        diff_items.append(item)
        grouped.setdefault(section, []).append(item)

    candidate_full = apply_operations_to_snapshot(base_snapshot, all_ops)
    candidate_approved = (
        apply_operations_to_snapshot(base_snapshot, approved_ops)
        if approved_ops
        else copy.deepcopy(base_snapshot or {})
    )

    base_hash = canonical_config_hash(base_snapshot)
    candidate_hash = canonical_config_hash(candidate_full)
    approved_hash = canonical_config_hash(candidate_approved)

    diff_fingerprint_payload = json.dumps(
        [item.model_dump(mode="json") for item in diff_items],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    diff_hash = hashlib.sha256(diff_fingerprint_payload.encode("utf-8")).hexdigest()

    return ConductorProposalDiffResponse(
        proposal_id=proposal_id,
        agent_id=agent_id,
        base_version_number=int(base_version_number),
        base_config_hash=base_hash,
        candidate_config_hash=candidate_hash,
        approved_candidate_config_hash=approved_hash,
        diff_hash=diff_hash,
        total_changes=len(diff_items),
        approved_changes=approved_count,
        rejected_changes=rejected_count,
        pending_changes=pending_count,
        grouped_by_section=grouped,
        changes=diff_items,
        side_by_side={
            "base_config": base_snapshot,
            "candidate_config": candidate_full,
            "approved_candidate_config": candidate_approved,
        },
    )
