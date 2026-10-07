from __future__ import annotations

from datetime import time

import pytest

from app.db.models import LeadStatus
from tests.conftest import auth_headers, make_lead


@pytest.mark.asyncio
async def test_campaign_audience_plan_and_controlled_workflow_lead_action(
    client, db, tenant_a, manager_a, owner_a
):
    headers = await auth_headers(client, manager_a)
    owner_headers = await auth_headers(client, owner_a)
    lead = await make_lead(db, tenant_a, phone="+14155550221")
    tenant_a.outbound_window_open = time(0, 0)
    tenant_a.outbound_window_close = time(23, 59)
    await db.commit()

    campaign = await client.post(
        "/api/campaigns",
        headers=headers,
        json={
            "name": "Prompt 8 local campaign",
            "goal": "follow_up",
            "calls_per_minute": 2,
            "lead_ids": [str(lead.id)],
        },
    )
    assert campaign.status_code == 201, campaign.text
    campaign_id = campaign.json()["id"]
    assert campaign.json()["state"] == "draft"

    audience = await client.get(
        f"/api/campaigns/{campaign_id}/audience", headers=headers
    )
    assert audience.status_code == 200, audience.text
    assert audience.json()["lead_count"] == 1
    assert audience.json()["segment_ids"] == []

    plan = await client.post(
        f"/api/campaigns/{campaign_id}/plan", headers=headers
    )
    assert plan.status_code == 200, plan.text
    assert len(plan.json()) == 1
    assert plan.json()[0]["lead_id"] == str(lead.id)
    assert plan.json()[0]["skipped"] is False

    scheduled = await client.post(
        f"/api/campaigns/{campaign_id}/schedule", headers=headers
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["state"] == "scheduled"
    resumed = await client.post(
        f"/api/campaigns/{campaign_id}/resume", headers=headers
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == "running"
    # Scheduling a campaign is not proof of a dial; this test never invokes a
    # carrier or generates a call session.

    workflow = await client.post(
        "/api/workflows",
        headers=owner_headers,
        json={
            "name": "Prompt 8 qualified lead workflow",
            "trigger": "manual",
            "entry_node": "qualify",
            "nodes": [
                {
                    "id": "qualify",
                    "type": "action",
                    "action_name": "update_lead_status",
                    "action_params": {"status": "qualified"},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
        },
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]
    published = await client.post(
        f"/api/workflows/{workflow_id}/publish", headers=owner_headers
    )
    assert published.status_code == 200, published.text

    execution_headers = {**headers, "Idempotency-Key": "prompt8-workflow-lead-001"}
    execution = await client.post(
        f"/api/workflows/{workflow_id}/execute",
        headers=execution_headers,
        json={"payload": {"lead_id": str(lead.id)}},
    )
    assert execution.status_code == 200, execution.text
    assert execution.json()["status"] == "completed"
    replay = await client.post(
        f"/api/workflows/{workflow_id}/execute",
        headers=execution_headers,
        json={"payload": {"lead_id": str(lead.id)}},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == execution.json()["id"]

    await db.refresh(lead)
    assert lead.status is LeadStatus.QUALIFIED
