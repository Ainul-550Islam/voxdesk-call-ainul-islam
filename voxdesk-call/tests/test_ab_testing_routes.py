from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.ab_testing_routes import (
    ExperimentUpdate,
    PromoteRequest,
    _validate_control_flags,
    _validate_variant_weights,
    get_assignment,
    get_metrics,
    pause_experiment,
    promote_variant,
    rollback_experiment,
    start_experiment,
)
from app.db.models import Agent
from tests.conftest import auth_headers


def test_experiment_weights_and_control_variant_are_validated() -> None:
    _validate_variant_weights([50, 50])
    _validate_control_flags([True, False])

    with pytest.raises(HTTPException) as weight_error:
        _validate_variant_weights([60, 30])
    assert weight_error.value.status_code == 422
    assert "sum to 100" in str(weight_error.value.detail)

    with pytest.raises(HTTPException) as control_error:
        _validate_control_flags([False, False])
    assert control_error.value.status_code == 422
    assert "exactly one" in str(control_error.value.detail)


def test_experiment_status_cannot_be_marked_running_by_metadata_patch() -> None:
    with pytest.raises(ValidationError):
        ExperimentUpdate(status="running")


@pytest.mark.asyncio
async def test_unintegrated_ab_testing_actions_fail_closed_without_mutation() -> None:
    experiment_id = uuid.uuid4()
    variant_id = uuid.uuid4()
    actions = [
        start_experiment(experiment_id, ctx=None, session=None),
        pause_experiment(experiment_id, ctx=None, session=None),
        get_assignment(
            experiment_id,
            call_id=uuid.uuid4(),
            ctx=None,
            session=None,
        ),
        get_metrics(experiment_id, ctx=None, session=None),
        promote_variant(
            experiment_id,
            payload=PromoteRequest(variant_id=variant_id),
            ctx=None,
            session=None,
        ),
        rollback_experiment(experiment_id, ctx=None, session=None),
    ]

    for action in actions:
        with pytest.raises(HTTPException) as error:
            await action
        assert error.value.status_code == 501
        assert error.value.detail["code"] == "AB_TESTING_CAPABILITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_experiment_create_list_and_read_use_persisted_tenant_scope(
    client,
    db,
    tenant_a,
    owner_a,
    owner_b,
) -> None:
    agent = Agent(
        tenant_id=tenant_a.id,
        external_key=f"ab_test_{uuid.uuid4().hex}",
        name="A/B Persistence Test Agent",
        description="Persisted fixture for experiment CRUD",
        agent_type="voice",
        status="draft",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    owner_a_headers = await auth_headers(client, owner_a)
    owner_b_headers = await auth_headers(client, owner_b)
    create_response = await client.post(
        "/api/experiments",
        headers=owner_a_headers,
        json={
            "agent_id": str(agent.id),
            "name": "Inbound greeting experiment",
            "description": "Persisted weighted draft",
            "variants": [
                {
                    "name": "Control",
                    "weight": 50,
                    "config": {"greeting": "Hello"},
                    "prompt": "Use the current greeting.",
                    "is_control": True,
                },
                {
                    "name": "Variant B",
                    "weight": 50,
                    "config": {"greeting": "Welcome"},
                    "prompt": "Use the alternate greeting.",
                    "is_control": False,
                },
            ],
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["status"] == "draft"
    assert sum(created["traffic_split"].values()) == 100
    assert len(created["variants"]) == 2

    start_response = await client.post(
        f"/api/experiments/{created['id']}/start",
        headers=owner_a_headers,
    )
    assert start_response.status_code == 501

    assignment_response = await client.get(
        f"/api/experiments/{created['id']}/assignment",
        headers=owner_a_headers,
        params={"call_id": str(uuid.uuid4())},
    )
    assert assignment_response.status_code == 501

    unchanged_experiment = await client.get(
        f"/api/experiments/{created['id']}",
        headers=owner_a_headers,
    )
    assert unchanged_experiment.status_code == 200
    assert unchanged_experiment.json()["status"] == "draft"

    list_response = await client.get(
        "/api/experiments",
        headers=owner_a_headers,
        params={"agent_id": str(agent.id)},
    )
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["total"] == 1
    assert list_response.json()["experiments"][0]["id"] == created["id"]

    other_tenant_read = await client.get(
        f"/api/experiments/{created['id']}",
        headers=owner_b_headers,
    )
    assert other_tenant_read.status_code == 404
