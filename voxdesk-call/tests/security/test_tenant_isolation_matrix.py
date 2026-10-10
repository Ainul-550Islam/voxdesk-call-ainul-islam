"""Multi-tenant isolation matrix across all 13 core enterprise resources (Part 7 / Gate G9).

For every required resource:
1. ``Agent`` (`/api/agents/{id}`)
2. ``Call`` (`/api/calls/{id}`)
3. ``Recording`` (`/api/recordings/{id}`)
4. ``PhoneNumber`` (`/api/phone-numbers/{id}`)
5. ``KnowledgeBase`` (`/api/kb/collections/{id}`)
6. ``Campaign`` (`/api/campaigns/{id}`)
7. ``Webhook`` (`/api/webhooks/{id}`)
8. ``CustomDashboard`` (`/api/v1/analytics/dashboards/{id}`)
9. ``AuditLog`` (`/api/v1/audit/events`)
10. ``Ticket`` (`/api/inbox/threads/{id}`)
11. ``Message`` (`/api/channels/{id}`)
12. ``TestCase`` (`/api/v1/testing/cases/{id}`)
13. ``AlertRule`` (`/api/v1/evaluations/rules/{id}` & `/api/automations/{id}`)

Creates the resource under Tenant A, attempts ``GET`` / ``PATCH`` (or ``PUT``) / ``DELETE``
authenticated as Tenant B, and asserts HTTP 404 (or 403) with zero rows mutated in Tenant A.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.enterprise_models import (
    KnowledgeCollection,
    MessageChannel,
)
from app.db.models import AuditAction, AuditLog, CallStatus, WebhookSubscription
from app.db.retell_models import (
    EvaluationRule,
    TestCase as TestCaseRow,
)
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.recording import CallRecording
from tests.conftest import auth_headers, make_call

pytestmark = pytest.mark.asyncio


async def test_tenant_isolation_matrix_all_13_resources(
    client, db, tenant_a, tenant_b, owner_a, owner_b, monkeypatch
):
    """Tenant B cannot read, update, or delete any of Tenant A's 13 core resource types."""
    async def _allow_rate_limit(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr("app.api.webhook_lifecycle_routes.rate_limit", _allow_rate_limit)
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)

    # ------------------------------------------------------------------
    # 1. Agent
    # ------------------------------------------------------------------
    agent_res = await client.post(
        "/api/agents",
        headers=headers_a,
        json={
            "name": "Tenant A Receptionist",
            "system_prompt": "You are Tenant A's receptionist.",
        },
    )
    assert agent_res.status_code in (200, 201), agent_res.text
    agent_id = agent_res.json().get("id") or agent_res.json().get("agent_id")

    assert (await client.get(f"/api/agents/{agent_id}", headers=headers_b)).status_code in (403, 404)
    assert (
        await client.post(
            f"/api/agents/{agent_id}/publish",
            headers=headers_b,
            json={},
        )
    ).status_code in (403, 404)
    agent_verify = await client.get(f"/api/agents/{agent_id}", headers=headers_a)
    assert agent_verify.status_code == 200
    assert agent_verify.json()["name"] == "Tenant A Receptionist"

    # ------------------------------------------------------------------
    # 2. Call
    # ------------------------------------------------------------------
    call_a = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    await db.commit()

    assert (await client.get(f"/api/calls/{call_a.id}", headers=headers_b)).status_code in (403, 404)
    assert (await client.post(f"/api/calls/{call_a.id}/end", headers=headers_b, json={})).status_code in (403, 404)
    await db.refresh(call_a)
    assert call_a.status == CallStatus.IN_PROGRESS

    # ------------------------------------------------------------------
    # 3. Recording
    # ------------------------------------------------------------------
    rec_a = CallRecording(
        tenant_id=tenant_a.id,
        call_id=call_a.id,
        provider="twilio",
        external_recording_id="RE_TENANT_A_1",
        external_guard="twilio:RE_TENANT_A_1",
        state="ready",
    )
    db.add(rec_a)
    await db.commit()
    await db.refresh(rec_a)

    assert (await client.get(f"/api/recordings/{rec_a.id}", headers=headers_b)).status_code in (403, 404)
    assert (
        await client.post(f"/api/recordings/{rec_a.id}/signed-access", headers=headers_b)
    ).status_code in (403, 404)
    assert (
        await client.post(f"/api/recordings/{rec_a.id}/purge", headers=headers_b, json={})
    ).status_code in (403, 404)
    await db.refresh(rec_a)
    assert rec_a.state == "ready"

    # ------------------------------------------------------------------
    # 4. PhoneNumber
    # ------------------------------------------------------------------
    prov_num = PhoneNumber(
        tenant_id=tenant_a.id,
        e164="+14155550191",
        provider="twilio",
        provider_sid="PN_TENANT_A_01",
        country="US",
        friendly_name="Tenant A Line",
        status="active",
    )
    db.add(prov_num)
    await db.commit()
    await db.refresh(prov_num)

    assert (
        await client.get(
            f"/api/phone-numbers/{prov_num.id}/trust-profile", headers=headers_b
        )
    ).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/phone-numbers/{prov_num.id}",
            headers=headers_b,
            json={"label": "Hijacked Line"},
        )
    ).status_code in (403, 404)
    assert (
        await client.post(
            f"/api/phone-numbers/{prov_num.id}/release",
            headers=headers_b,
            json={},
        )
    ).status_code in (403, 404)
    await db.refresh(prov_num)
    assert prov_num.friendly_name == "Tenant A Line"
    assert prov_num.status == "active"

    # ------------------------------------------------------------------
    # 5. KnowledgeBase
    # ------------------------------------------------------------------
    kb_res = await client.post(
        "/api/kb/collections",
        headers=headers_a,
        json={"name": "Tenant A Clinical SOPs", "description": "Private KB"},
    )
    assert kb_res.status_code == 201, kb_res.text
    kb_id = kb_res.json()["id"]

    assert (await client.get(f"/api/kb/collections/{kb_id}", headers=headers_b)).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/kb/collections/{kb_id}",
            headers=headers_b,
            json={"name": "Tampered KB"},
        )
    ).status_code in (403, 404)
    assert (
        await client.delete(f"/api/kb/collections/{kb_id}", headers=headers_b)
    ).status_code in (403, 404)
    kb_row = await db.get(KnowledgeCollection, uuid.UUID(kb_id))
    assert kb_row is not None and kb_row.name == "Tenant A Clinical SOPs"

    # ------------------------------------------------------------------
    # 6. Campaign
    # ------------------------------------------------------------------
    camp_res = await client.post(
        "/api/campaigns",
        headers=headers_a,
        json={
            "name": "Tenant A Q4 Renewal",
            "goal": "qualify",
            "channel": "voice",
        },
    )
    assert camp_res.status_code in (200, 201), camp_res.text
    camp_id = camp_res.json()["id"]

    assert (await client.get(f"/api/campaigns/{camp_id}", headers=headers_b)).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/campaigns/{camp_id}",
            headers=headers_b,
            json={"name": "Tampered Campaign"},
        )
    ).status_code in (403, 404)
    assert (
        await client.post(f"/api/campaigns/{camp_id}/cancel", headers=headers_b, json={})
    ).status_code in (403, 404)
    camp_check = await client.get(f"/api/campaigns/{camp_id}", headers=headers_a)
    assert camp_check.status_code == 200 and camp_check.json()["name"] == "Tenant A Q4 Renewal"

    # ------------------------------------------------------------------
    # 7. Webhook
    # ------------------------------------------------------------------
    wh_res = await client.post(
        "/api/webhooks",
        headers=headers_a,
        json={
            "url": "https://hooks.tenant-a.example.com/events",
            "events": ["webhook.test"],
            "description": "Tenant A Primary Webhook",
        },
    )
    assert wh_res.status_code in (200, 201), wh_res.text
    wh_id = wh_res.json()["id"]

    assert (await client.get(f"/api/webhooks/{wh_id}", headers=headers_b)).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/webhooks/{wh_id}",
            headers=headers_b,
            json={"description": "Tampered Webhook"},
        )
    ).status_code in (403, 404)
    assert (await client.delete(f"/api/webhooks/{wh_id}", headers=headers_b)).status_code in (403, 404)
    wh_row = await db.get(WebhookSubscription, uuid.UUID(wh_id))
    assert wh_row is not None

    # ------------------------------------------------------------------
    # 8. CustomDashboard
    # ------------------------------------------------------------------
    dash_res = await client.post(
        "/api/v1/analytics/dashboards",
        headers=headers_a,
        json={
            "name": "Tenant A Executive KPIs",
            "description": "Confidential board metrics",
            "widgets": [
                {
                    "id": "w1",
                    "type": "kpi",
                    "title": "Total Calls",
                    "metric": "total_calls",
                }
            ],
        },
    )
    assert dash_res.status_code in (200, 201), dash_res.text
    dash_id = dash_res.json()["id"]

    assert (
        await client.get(f"/api/v1/analytics/dashboards/{dash_id}", headers=headers_b)
    ).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/v1/analytics/dashboards/{dash_id}",
            headers=headers_b,
            json={"name": "Hijacked Dashboard"},
        )
    ).status_code in (403, 404)
    assert (
        await client.delete(f"/api/v1/analytics/dashboards/{dash_id}", headers=headers_b)
    ).status_code in (403, 404)
    dash_check = await client.get(f"/api/v1/analytics/dashboards/{dash_id}", headers=headers_a)
    assert dash_check.status_code == 200 and dash_check.json()["name"] == "Tenant A Executive KPIs"

    # ------------------------------------------------------------------
    # 9. AuditLog
    # ------------------------------------------------------------------
    marker_id = f"secret-audit-{uuid.uuid4().hex}"
    audit_a = AuditLog(
        tenant_id=tenant_a.id,
        actor_user_id=owner_a.id,
        action=AuditAction.GOVERNANCE_EVENT,
        detail={"marker": marker_id},
    )
    db.add(audit_a)
    await db.commit()

    audit_b_res = await client.get("/api/v1/audit/events", headers=headers_b)
    assert audit_b_res.status_code == 200
    assert marker_id not in audit_b_res.text

    # ------------------------------------------------------------------
    # 10. Ticket (Inbox Thread)
    # ------------------------------------------------------------------
    thread_res = await client.post(
        "/api/inbox/threads",
        headers=headers_a,
        json={
            "channel": "sms",
            "customer": "+14155550188",
            "initial_message": "Need help with my appointment",
        },
    )
    assert thread_res.status_code in (200, 201), thread_res.text
    thread_id = thread_res.json()["id"]

    assert (
        await client.get(f"/api/inbox/threads/{thread_id}", headers=headers_b)
    ).status_code in (403, 404)
    assert (
        await client.post(f"/api/inbox/threads/{thread_id}/close", headers=headers_b, json={})
    ).status_code in (403, 404)
    thread_check = await client.get(f"/api/inbox/threads/{thread_id}", headers=headers_a)
    assert thread_check.status_code == 200
    assert thread_check.json()["status"] != "closed"

    # ------------------------------------------------------------------
    # 11. Message (MessageChannel)
    # ------------------------------------------------------------------
    chan_res = await client.post(
        "/api/channels",
        headers=headers_a,
        json={
            "channel_type": "sms",
            "provider": "twilio",
            "config": {"label": "Tenant A SMS Channel"},
            "is_active": True,
        },
    )
    assert chan_res.status_code in (200, 201), chan_res.text
    chan_id = chan_res.json()["id"]

    assert (await client.get(f"/api/channels/{chan_id}", headers=headers_b)).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/channels/{chan_id}",
            headers=headers_b,
            json={"is_active": False},
        )
    ).status_code in (403, 404)
    assert (await client.delete(f"/api/channels/{chan_id}", headers=headers_b)).status_code in (403, 404)
    chan_row = await db.get(MessageChannel, uuid.UUID(chan_id))
    assert chan_row is not None and chan_row.is_active is True

    # ------------------------------------------------------------------
    # 12. TestCase
    # ------------------------------------------------------------------
    pub_res = await client.post(
        f"/api/agents/{agent_id}/publish",
        headers=headers_a,
        json={},
    )
    assert pub_res.status_code in (200, 201), pub_res.text

    suite_res = await client.post(
        "/api/v1/testing/suites",
        headers=headers_a,
        json={"name": "Tenant A Regression Suite"},
    )
    assert suite_res.status_code in (200, 201), suite_res.text
    suite_id = suite_res.json()["id"]

    case_res = await client.post(
        "/api/v1/testing/cases",
        headers=headers_a,
        json={
            "suite_id": suite_id,
            "agent_id": agent_id,
            "agent_version_number": 1,
            "name": "Tenant A Greeting Case",
            "input_messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert case_res.status_code in (200, 201), case_res.text
    case_id = case_res.json()["id"]

    assert (
        await client.get(f"/api/v1/testing/cases/{case_id}", headers=headers_b)
    ).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/v1/testing/cases/{case_id}",
            headers=headers_b,
            json={"name": "Hijacked Test Case"},
        )
    ).status_code in (403, 404)
    assert (
        await client.delete(f"/api/v1/testing/cases/{case_id}", headers=headers_b)
    ).status_code in (403, 404)
    case_row = await db.get(TestCaseRow, uuid.UUID(case_id))
    assert case_row is not None and case_row.name == "Tenant A Greeting Case"

    # ------------------------------------------------------------------
    # 13. AlertRule (EvaluationRule)
    # ------------------------------------------------------------------
    rule_res = await client.post(
        "/api/v1/evaluations/rules",
        headers=headers_a,
        json={
            "suite_id": suite_id,
            "name": "Tenant A Compliance Alert Rule",
            "rule_type": "contains",
            "config": {"substring": "recorded"},
            "weight": 1.0,
            "enabled": True,
        },
    )
    assert rule_res.status_code in (200, 201), rule_res.text
    rule_id = rule_res.json()["id"]

    assert (
        await client.get(f"/api/v1/evaluations/rules/{rule_id}", headers=headers_b)
    ).status_code in (403, 404)
    assert (
        await client.patch(
            f"/api/v1/evaluations/rules/{rule_id}",
            headers=headers_b,
            json={"name": "Hijacked Alert Rule"},
        )
    ).status_code in (403, 404)
    assert (
        await client.delete(f"/api/v1/evaluations/rules/{rule_id}", headers=headers_b)
    ).status_code in (403, 404)
    rule_row = await db.get(EvaluationRule, uuid.UUID(rule_id))
    assert rule_row is not None and rule_row.name == "Tenant A Compliance Alert Rule"
