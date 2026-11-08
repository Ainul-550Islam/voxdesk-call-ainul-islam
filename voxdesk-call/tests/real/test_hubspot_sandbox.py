"""Real HubSpot developer sandbox test: upsert Contact, Task, Ticket (Case) -> cleanup."""

from __future__ import annotations

from datetime import datetime, timezone
import time
import uuid

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_hubspot_sandbox_upsert_contact_task_ticket_and_cleanup(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("hubspot", request=request, evidence_writer=evidence_writer)
    token = env["VOXDESK_REAL_HUBSPOT_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    api_base = "https://api.hubapi.com/crm/v3/objects"
    unique_tag = uuid.uuid4().hex[:10]
    email = f"voxdesk.hubspot.{unique_tag}@example.org"

    contact_id: str | None = None
    task_id: str | None = None
    ticket_id: str | None = None
    t0 = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            contact_resp = await client.post(
                f"{api_base}/contacts",
                json={
                    "properties": {
                        "email": email,
                        "firstname": "VoxDeskReal",
                        "lastname": f"HubSpot{unique_tag}",
                        "phone": "+15550195500",
                    }
                },
            )
            assert contact_resp.status_code in (200, 201), contact_resp.text
            contact_id = str(contact_resp.json()["id"])

            now_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            task_resp = await client.post(
                f"{api_base}/tasks",
                json={
                    "properties": {
                        "hs_timestamp": now_ms,
                        "hs_task_subject": f"VoxDesk Voice Call Summary ({unique_tag})",
                        "hs_task_body": "Real integration verification call note.",
                        "hs_task_status": "COMPLETED",
                        "hs_task_priority": "MEDIUM",
                    }
                },
            )
            assert task_resp.status_code in (200, 201), task_resp.text
            task_id = str(task_resp.json()["id"])

            ticket_resp = await client.post(
                f"{api_base}/tickets",
                json={
                    "properties": {
                        "subject": f"VoxDesk Support Case ({unique_tag})",
                        "content": "Created by tests/real/test_hubspot_sandbox.py",
                        "hs_pipeline": "0",
                        "hs_pipeline_stage": "1",
                    }
                },
            )
            assert ticket_resp.status_code in (200, 201), ticket_resp.text
            ticket_id = str(ticket_resp.json()["id"])

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            evidence_writer.record(
                provider="hubspot",
                status="PASS",
                test_node=request.node.nodeid,
                latency_ms=elapsed_ms,
                model="hubspot-crm-v3",
                details={
                    "contact_id": contact_id,
                    "task_id": task_id,
                    "ticket_id": ticket_id,
                },
            )
    except Exception as exc:
        evidence_writer.record(
            provider="hubspot",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
    finally:
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as cleanup:
            for endpoint, obj_id in (
                ("tickets", ticket_id),
                ("tasks", task_id),
                ("contacts", contact_id),
            ):
                if obj_id:
                    await cleanup.delete(f"{api_base}/{endpoint}/{obj_id}")
