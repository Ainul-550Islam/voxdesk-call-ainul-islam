"""Agent export / import (PART 12, P12-24).

Moving an agent between tenants — a staging copy, a customer's account, a support reproduction —
needs three things to be true, and each one is a test:

* **No secrets leave.** Any key that looks like a credential is replaced by a reference
  (``{"$secret": "twilio_auth_token"}``), never by the value, and never by an empty string that
  silently breaks the import.
* **Ids are remapped, not reused.** Exported IDs are template ids; the importer mints new ones, so
  importing twice creates two agents instead of overwriting someone else's.
* **Import is a decision, not a surprise.** ``mode=create`` always makes a new agent;
  ``mode=merge`` updates an existing one and reports what would change first (dry run).

The format name is versioned (`voxdesk.agent/1`) so a future change can be detected instead of
being mis-parsed as the current one.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

EXPORT_FORMAT = "voxdesk.agent/1"
IMPORT_MODES: tuple[str, ...] = ("create", "merge")

#: Anything matching this is a credential. Erring towards redaction is the only safe direction:
#: a missed export of a real key cannot be undone.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|private[_-]?key|client[_-]?secret|auth[_-]?token)",
    re.IGNORECASE,
)


#: Keys that *look* like credentials but describe them ("password_hint" is UI copy, not a secret).
_NOT_A_SECRET_SUFFIXES: tuple[str, ...] = (
    "_hint",
    "_label",
    "_placeholder",
    "_field",
    "_prompt",
    "_policy",
    "_length",
    "_url",
)


class PortabilityError(ValueError):
    """An export or import that cannot be honoured."""


def _redact(value: Any, path: str = "") -> Any:
    """Recursively replace credential-looking *values* with references."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            key_str = str(key)
            looks_secret = bool(_SECRET_KEY_RE.search(key_str)) and not key_str.lower().endswith(
                _NOT_A_SECRET_SUFFIXES
            )
            if looks_secret and isinstance(inner, (str, int, float)) and str(inner):
                out[key_str] = {"$secret": f"{path}.{key_str}".strip(".")}
            else:
                out[key_str] = _redact(inner, f"{path}.{key_str}".strip("."))
        return out
    if isinstance(value, list):
        return [_redact(item, f"{path}[]") for item in value]
    return value


def find_secrets(payload: Any) -> list[str]:
    """Every `$secret` reference in an export — the checklist an operator must fill in."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, inner in node.items():
                if key == "$secret" and isinstance(inner, str):
                    found.append(inner)
                else:
                    walk(inner)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(set(found))


@dataclass(frozen=True)
class AgentBundle:
    """The exported document."""

    format: str
    agent: dict[str, Any]
    flow: dict[str, Any]
    components: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "agent": self.agent,
            "flow": self.flow,
            "components": list(self.components),
            "metadata": dict(self.metadata),
            "secrets_required": find_secrets({"agent": self.agent, "flow": self.flow}),
        }


def export_agent(
    *,
    agent_id: str,
    config: Mapping[str, Any],
    flow: Mapping[str, Any] | None = None,
    components: tuple[Mapping[str, Any], ...] = (),
    exported_by: str = "",
) -> dict[str, Any]:
    """Export one agent as `voxdesk.agent/1` JSON with secrets stripped."""
    # Paths carry the export location so the checklist reads "agent.config.voice.api_key"
    # instead of a bare "api_key" the operator cannot place.
    safe_config = _redact({"config": dict(config)}, "agent")["config"]
    safe_flow = _redact({"flow": dict(flow or {})}, "agent")["flow"]
    # Internal identifiers are replaced by stable placeholders so the importer can map them.
    safe_config.pop("tenant_id", None)
    bundle = AgentBundle(
        format=EXPORT_FORMAT,
        agent={
            "template_id": str(agent_id),
            "name": safe_config.get("name") or "Imported agent",
            "config": safe_config,
        },
        flow=safe_flow,
        components=tuple(_redact(dict(item)) for item in components),
        metadata={"exported_by": exported_by} if exported_by else {},
    )
    return bundle.to_dict()


def validate_bundle(bundle: Mapping[str, Any]) -> list[str]:
    """Structural problems that make a bundle unimportable."""
    problems: list[str] = []
    if not isinstance(bundle, Mapping):
        return ["bundle is not an object"]
    if bundle.get("format") != EXPORT_FORMAT:
        problems.append(f"format {bundle.get('format')!r} is not {EXPORT_FORMAT!r}")
    agent = bundle.get("agent")
    if not isinstance(agent, Mapping):
        problems.append("bundle has no agent block")
    elif not isinstance(agent.get("config"), Mapping):
        problems.append("agent block has no config")
    flow = bundle.get("flow")
    if flow is not None and not isinstance(flow, Mapping):
        problems.append("flow must be an object")
    if isinstance(bundle.get("components"), (str, int)):
        problems.append("components must be a list")
    return problems


@dataclass(frozen=True)
class ImportPlan:
    mode: str
    agent_id: str
    name: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    unresolved_secrets: list[str] = field(default_factory=list)
    dry_run: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "agent_id": self.agent_id,
            "name": self.name,
            "changes": self.changes,
            "unresolved_secrets": self.unresolved_secrets,
            "dry_run": self.dry_run,
        }


def _flatten(payload: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                flat.update(_flatten(value, path))
            else:
                flat[path] = value
    else:
        flat[prefix] = payload
    return flat


def import_agent(
    bundle: Mapping[str, Any],
    *,
    mode: str = "create",
    existing_config: Mapping[str, Any] | None = None,
    supplied_secrets: Mapping[str, str] | None = None,
    new_agent_id: str | None = None,
    dry_run: bool = True,
) -> ImportPlan:
    """Plan (and, with ``dry_run=False``, describe) an import."""
    if mode not in IMPORT_MODES:
        raise PortabilityError(f"mode must be one of {list(IMPORT_MODES)}")
    problems = validate_bundle(bundle)
    if problems:
        raise PortabilityError("; ".join(problems))
    if mode == "merge" and existing_config is None:
        raise PortabilityError("merge needs an existing agent to merge into")

    agent_block = bundle.get("agent") or {}
    config = copy.deepcopy(dict(agent_block.get("config") or {}))
    required = set(find_secrets(bundle))
    supplied = set((supplied_secrets or {}).keys())

    changes: list[dict[str, Any]] = []
    if mode == "merge":
        before = _flatten(existing_config or {})
        after = _flatten(config)
        for path in sorted(set(before) | set(after)):
            old = before.get(path, None)
            new = after.get(path, None)
            if old != new:
                changes.append({"path": path, "from": old, "to": new})
    else:
        changes = [{"path": path, "from": None, "to": value} for path, value in sorted(_flatten(config).items())]

    return ImportPlan(
        mode=mode,
        agent_id=new_agent_id or (str(uuid.uuid4()) if not dry_run else "pending"),
        name=str(agent_block.get("name") or "Imported agent"),
        changes=changes,
        unresolved_secrets=sorted(required - supplied),
        dry_run=dry_run,
    )
