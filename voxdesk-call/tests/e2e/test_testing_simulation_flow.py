from __future__ import annotations

import pytest

import app.db.enterprise_models  # noqa: F401 — register simulation models before test schema creation
from tests.conftest import auth_headers
from tests.e2e._support import create_published_voice_agent, publish_builder_revision


@pytest.mark.asyncio
async def test_version_pinned_playground_and_failed_scenario_are_persisted_truthfully(
    client, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    agent, published_v1, version_v1 = await create_published_voice_agent(
        client,
        headers,
        name="Simulation Regression E2E",
        greeting="Welcome from version one.",
        system_prompt="PROMPT8_SIM_V1: Answer billing questions carefully.",
    )
    agent_id = agent["agent_id"]

    v1_run = await client.post(
        "/api/v1/testing/playground/run",
        headers=headers,
        json={
            "agent_id": agent_id,
            "agent_kind": "voice",
            "agent_version_number": published_v1["version"],
            "user_message": "How do you handle a billing question?",
            "evaluation_rules": [
                {
                    "name": "Snapshot contains the v1 signature",
                    "rule_type": "contains",
                    "config": {"substring": "PROMPT8_SIM_V1"},
                }
            ],
        },
    )
    assert v1_run.status_code == 200, v1_run.text
    v1_body = v1_run.json()
    assert v1_body["status"] == "passed"
    assert v1_body["agent_version_number"] == 1
    assert v1_body["pinned_config_snapshot"]["system_prompt"].startswith("PROMPT8_SIM_V1")
    assert version_v1["id"]

    published_v2, version_v2 = await publish_builder_revision(
        client,
        headers,
        agent_id,
        greeting="Welcome from version two.",
        system_prompt="PROMPT8_SIM_V2: Route technical escalations to support.",
    )
    assert published_v2["version"] == 2

    pinned_v1_again = await client.post(
        "/api/v1/testing/playground/run",
        headers=headers,
        json={
            "agent_id": agent_id,
            "agent_kind": "voice",
            "agent_version_number": 1,
            "user_message": "Check the old immutable prompt.",
            "evaluation_rules": [
                {
                    "name": "Pinned historical snapshot remains reproducible",
                    "rule_type": "contains",
                    "config": {"substring": "PROMPT8_SIM_V1"},
                },
                {
                    "name": "Does not silently use the new prompt",
                    "rule_type": "not_contains",
                    "config": {"substring": "PROMPT8_SIM_V2"},
                },
            ],
        },
    )
    assert pinned_v1_again.status_code == 200, pinned_v1_again.text
    pinned_body = pinned_v1_again.json()
    assert pinned_body["status"] == "passed"
    assert pinned_body["agent_version_number"] == 1
    assert pinned_body["pinned_config_snapshot"]["system_prompt"].startswith("PROMPT8_SIM_V1")

    v2_run = await client.post(
        "/api/v1/testing/playground/run",
        headers=headers,
        json={
            "agent_id": agent_id,
            "agent_kind": "voice",
            "agent_version_number": published_v2["version"],
            "user_message": "Check the current prompt.",
            "evaluation_rules": [
                {
                    "name": "Current snapshot contains v2 signature",
                    "rule_type": "contains",
                    "config": {"substring": "PROMPT8_SIM_V2"},
                }
            ],
        },
    )
    assert v2_run.status_code == 200, v2_run.text
    assert v2_run.json()["status"] == "passed"
    assert v2_run.json()["agent_version_number"] == 2
    assert version_v2["config_snapshot"]["system_prompt"].startswith("PROMPT8_SIM_V2")

    scenario = await client.post(
        "/api/simulations",
        headers=headers,
        json={
            "name": "Expected intent must not be fabricated",
            "scenario": {
                "steps": [
                    {
                        "speaker": "user",
                        "text": "Hello, good morning!",
                        "expect_intent": "transfer_to_human",
                    }
                ]
            },
        },
    )
    assert scenario.status_code == 201, scenario.text
    run = await client.post(
        f"/api/simulations/{scenario.json()['id']}/run", headers=headers
    )
    assert run.status_code == 200, run.text
    result = run.json()
    assert result["status"] == "failed"
    assert result["result"]["passed"] is False
    check = result["evidence"]["checks"][0]
    assert check["expected_intent"] == "transfer_to_human"
    assert check["actual_intent"] == "greeting"
    assert check["passed"] is False
