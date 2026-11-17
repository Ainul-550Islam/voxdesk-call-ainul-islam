"""PART 14 / P14-04 (P14-10) — the Make.com blueprint round-trips.

`integrations/make/` is a published package that sits outside `app/`, so it is
loaded here by path. That is deliberate: the test proves the folder can be
vendored and imported without the server, which is what "publish-ready" means.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "integrations" / "make"


def load_blueprint():
    spec = importlib.util.spec_from_file_location(
        "voxdesk_make_blueprint", PACKAGE / "blueprint.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blueprint = load_blueprint()


def test_a_workflow_exports_with_a_trigger_first() -> None:
    exported = blueprint.build(
        name="Refill follow-up",
        steps=[{"preset": "send_sms", "args": {"to": "+8801712345678", "body": "hi"}}],
    )
    assert exported["flow"][0]["parameters"]["event"] == "call.ended"
    modules = exported["metadata"]["modules"]
    assert modules[0]["module"] == "voxdesk:trigger"
    assert modules[1]["parameters"]["preset"] == "send_sms"
    assert blueprint.sequence_ids(exported) == [1, 2]


def test_a_round_trip_preserves_the_steps() -> None:
    exported = blueprint.build(
        name="Two-step",
        steps=[
            {"preset": "post_slack", "args": {"channel": "#ops", "text": "call ended"}},
            {"preset": "create_ticket", "args": {"subject": "Follow up", "description": "…"}},
        ],
    )
    parsed = blueprint.parse(exported)
    assert parsed["trigger"] == "call.ended"
    assert [step["preset"] for step in parsed["steps"]] == ["post_slack", "create_ticket"]
    assert parsed["steps"][0]["args"]["channel"] == "#ops"


def test_an_empty_workflow_is_refused_on_export() -> None:
    with pytest.raises(blueprint.BlueprintError) as exc:
        blueprint.build(name="nothing", steps=[])
    assert exc.value.code == "no_steps"


def test_a_step_without_a_preset_is_refused_on_export() -> None:
    with pytest.raises(blueprint.BlueprintError) as exc:
        blueprint.build(name="broken", steps=[{"args": {"a": 1}}])
    assert exc.value.code == "no_preset"


def test_importing_a_foreign_blueprint_is_refused_with_the_module_names() -> None:
    foreign = {
        "name": "Someone else's scenario",
        "metadata": {
            "modules": [
                {"id": 1, "module": "gateway:CustomWebHook", "parameters": {}},
                {"id": 2, "module": "http:ActionSendData", "parameters": {}},
            ]
        },
    }
    with pytest.raises(blueprint.BlueprintError) as exc:
        blueprint.parse(foreign)
    assert exc.value.code == "foreign_modules"
    assert "http:ActionSendData" in str(exc.value)


def test_a_blueprint_without_a_trigger_is_refused() -> None:
    with pytest.raises(blueprint.BlueprintError) as exc:
        blueprint.parse(
            {
                "metadata": {
                    "modules": [
                        {"id": 1, "module": "voxdesk:action", "parameters": {"preset": "send_sms"}}
                    ]
                }
            }
        )
    assert exc.value.code == "no_trigger"


def test_a_blueprint_with_a_trigger_but_no_actions_is_refused() -> None:
    with pytest.raises(blueprint.BlueprintError) as exc:
        blueprint.parse(
            {"metadata": {"modules": [{"id": 1, "module": "voxdesk:trigger", "parameters": {"event": "call.ended"}}]}}
        )
    assert exc.value.code == "no_actions"


def test_the_webhook_envelope_has_stable_keys() -> None:
    payload = blueprint.webhook_payload(
        call_id="11111111-1111-1111-1111-111111111111", event="call.ended", data={"duration": 42}
    )
    assert payload["event"] == "call.ended"
    assert payload["source"] == "voxdesk"
    assert payload["schema"] == "voxdesk.event.v1"
    assert payload["data"]["duration"] == 42
