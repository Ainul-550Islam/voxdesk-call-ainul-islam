"""Make.com blueprint generation and parsing."""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Mapping

BLUEPRINT_VERSION = 1
#: `voxdesk` is the custom app name a tenant installs in Make. Modules we emit
#: are all of this form so an imported scenario shows "VoxDesk: post message"
#: rather than a bare HTTP module with no labels.
APP = "voxdesk"


class BlueprintError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def module(name: str, *, parameters: Mapping[str, Any] | None = None, position: int = 0) -> dict:
    return {
        "id": position,
        "module": f"{APP}:{name}",
        "version": 1,
        "parameters": dict(parameters or {}),
        "mapper": {},
        "metadata": {"designer": {"x": position * 300, "y": 0}},
    }


def build(
    *,
    name: str,
    steps: Iterable[Mapping[str, Any]],
    trigger: str = "call.ended",
    schedule_ms: int = 900_000,
) -> dict[str, Any]:
    """Export a workflow as a Make blueprint.

    The first module is always our trigger, because a Make scenario without a
    trigger imports but never runs -- which looks like a working import until
    the first call ends and nothing happens.
    """
    step_list = list(steps)
    if not step_list:
        raise BlueprintError("no_steps", "a workflow needs at least one step")
    modules = [module("trigger", parameters={"event": trigger}, position=1)]
    for index, step in enumerate(step_list, start=2):
        preset = step.get("preset")
        if not preset:
            raise BlueprintError("no_preset", f"step {index - 1} has no preset")
        modules.append(module("action", parameters={"preset": preset, "args": dict(step.get("args") or {})}, position=index))
    return {
        "name": name[:120] or "VoxDesk workflow",
        "flow": [{"id": 1, "type": "flow", "version": 1, "module": "gateway:CustomWebHook" if trigger == "webhook" else f"{APP}:trigger", "parameters": {"hook": "", "event": trigger}, "mapper": {}, "metadata": {"designer": {"x": 0, "y": 0}}}],
        "metadata": {
            "version": BLUEPRINT_VERSION,
            "scenario": {"roundtrips": 1, "maxErrors": 3, "autoCommit": True, "autoCommitTriggerLast": True, "sequential": True, "confidential": False, "dataloss": False, "dlq": False, "freshVariables": False, "designer": {"orphans": 0}, "schedule": {"type": "interval", "interval": schedule_ms}},
            "modules": modules,
        },
    }


def sequence_ids(blueprint: Mapping[str, Any]) -> list[int]:
    """Module ids in flow order, so a blueprint can be diffed deterministically."""
    metadata = blueprint.get("metadata") or {}
    modules = metadata.get("modules") or []
    return [int(m.get("id", 0)) for m in modules]


def parse(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Read a blueprint back into VoxDesk steps.

    Refuses a blueprint whose modules are not ours: importing a scenario made of
    generic HTTP modules would create a workflow that claims to call us and does
    not, and the customer would only find out from a failed call.
    """
    metadata = blueprint.get("metadata") or {}
    modules = metadata.get("modules") or []
    if not modules:
        raise BlueprintError("empty", "the blueprint has no modules")
    trigger: str | None = None
    steps: list[dict[str, Any]] = []
    foreign: list[str] = []
    for entry in modules:
        module_name = str(entry.get("module") or "")
        if not module_name.startswith(f"{APP}:"):
            foreign.append(module_name)
            continue
        kind = module_name.split(":", 1)[1]
        parameters = entry.get("parameters") or {}
        if kind == "trigger":
            trigger = str(parameters.get("event") or "")
            continue
        if kind == "action":
            preset = parameters.get("preset")
            if not preset:
                raise BlueprintError("no_preset", f"module {entry.get('id')} has no preset")
            steps.append({"preset": str(preset), "args": dict(parameters.get("args") or {})})
    if foreign:
        raise BlueprintError(
            "foreign_modules", f"this blueprint uses modules we cannot run: {', '.join(foreign[:5])}"
        )
    if trigger is None:
        raise BlueprintError("no_trigger", "the blueprint has no VoxDesk trigger module")
    if not steps:
        raise BlueprintError("no_actions", "the blueprint has a trigger but no actions")
    return {"name": str(blueprint.get("name") or "")[:120], "trigger": trigger, "steps": steps}


def webhook_payload(*, call_id: str | uuid.UUID, event: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """The envelope we POST to a Make webhook. Stable keys, versioned event."""
    return {
        "event": event,
        "call_id": str(call_id),
        "data": dict(data),
        "source": "voxdesk",
        "schema": f"{APP}.event.v{BLUEPRINT_VERSION}",
    }
