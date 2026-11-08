"""Real Salesforce developer sandbox test: refresh token -> upsert Contact, Task, Case -> cleanup."""

from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_salesforce_sandbox_upsert_contact_task_case_and_cleanup(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("salesforce", request=request, evidence_writer=evidence_writer)
    token_host = os.environ.get("SALESFORCE_LOGIN_URL", "https://test.salesforce.com").rstrip("/")
    api_version = os.environ.get("SALESFORCE_API_VERSION", "v59.0").strip()

    contact_id: str | None = None
    task_id: str | None = None
    case_id: str | None = None
    access_token = ""
    instance_url = env["SALESFORCE_INSTANCE_URL"].rstrip("/")
    t0 = time.perf_counter()
    unique_tag = uuid.uuid4().hex[:10]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            tok_resp = await client.post(
                f"{token_host}/services/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": env["SALESFORCE_CLIENT_ID"],
                    "client_secret": env["SALESFORCE_CLIENT_SECRET"],
                    "refresh_token": env["SALESFORCE_REFRESH_TOKEN"],
                },
            )
            assert tok_resp.status_code == 200, tok_resp.text
            tok_data = tok_resp.json()
            access_token = str(tok_data["access_token"])
            if tok_data.get("instance_url"):
                instance_url = str(tok_data["instance_url"]).rstrip("/")

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            sobject_base = f"{instance_url}/services/data/{api_version}/sobjects"

            contact_resp = await client.post(
                f"{sobject_base}/Contact",
                headers=headers,
                json={
                    "FirstName": "VoxDeskReal",
                    "LastName": f"Sandbox{unique_tag}",
                    "Email": f"voxdesk.real.{unique_tag}@example.org",
                    "Phone": "+15550194400",
                },
            )
            assert contact_resp.status_code in (200, 201), contact_resp.text
            contact_id = str(contact_resp.json()["id"])

            task_resp = await client.post(
                f"{sobject_base}/Task",
                headers=headers,
                json={
                    "WhoId": contact_id,
                    "Subject": f"VoxDesk AI Voice Call ({unique_tag})",
                    "Status": "Completed",
                    "Priority": "Normal",
                    "Description": "Automated real-integration sandbox verification task.",
                },
            )
            assert task_resp.status_code in (200, 201), task_resp.text
            task_id = str(task_resp.json()["id"])

            case_resp = await client.post(
                f"{sobject_base}/Case",
                headers=headers,
                json={
                    "ContactId": contact_id,
                    "Subject": f"VoxDesk Escalated Inquiry ({unique_tag})",
                    "Origin": "Phone",
                    "Status": "New",
                },
            )
            assert case_resp.status_code in (200, 201), case_resp.text
            case_id = str(case_resp.json()["id"])

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            evidence_writer.record(
                provider="salesforce",
                status="PASS",
                test_node=request.node.nodeid,
                latency_ms=elapsed_ms,
                model=f"salesforce-rest-{api_version}",
                details={
                    "contact_id": contact_id,
                    "task_id": task_id,
                    "case_id": case_id,
                },
            )
    except Exception as exc:
        evidence_writer.record(
            provider="salesforce",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
    finally:
        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
            sobject_base = f"{instance_url}/services/data/{api_version}/sobjects"
            async with httpx.AsyncClient(timeout=20.0, headers=headers) as cleanup:
                for obj_name, obj_id in (("Case", case_id), ("Task", task_id), ("Contact", contact_id)):
                    if obj_id:
                        await cleanup.delete(f"{sobject_base}/{obj_name}/{obj_id}")
