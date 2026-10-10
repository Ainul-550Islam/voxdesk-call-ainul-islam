"""Tests for analytics aggregation, custom dashboards, tenant scoping, limits, QA sampling policies, and A/B experiments (Part 6 / Gate G7)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.models import (
    Agent,
    AgentLifecycleStatus,
    AgentVersion,
    AgentVersionStatus,
    Call,
    CallDirection,
    CallStatus,
    TransferState,
    UserRole,
)
from app.db.telephony_models import CallLatencyStat
from app.qa import service as qa_service
from app.qa.models import SentimentResult
from app.services import analytics_service, custom_dashboard_service
from tests.conftest import auth_headers, make_tenant, make_user


async def _seed_analytics_calls(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[Agent, AgentVersion, list[Call]]:
    now = datetime.now(timezone.utc)
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        external_key=f"agent-{uuid.uuid4().hex[:8]}",
        name="Support Voice Agent",
        status=AgentLifecycleStatus.PUBLISHED,
        current_draft_config={"name": "Support Voice Agent"},
        published_version_number=1,
        created_at=now,
        updated_at=now,
    )
    db.add(agent)
    await db.flush()

    ver = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=1,
        status=AgentVersionStatus.PUBLISHED,
        config_snapshot={"name": "Support Voice Agent"},
        config_hash="hash-1",
        created_at=now,
    )
    db.add(ver)
    agent.published_version_id = ver.id
    await db.flush()

    # Call 1: Completed + Booked, 120s, positive sentiment, 320ms e2e p50 / 480ms p95
    c1 = Call(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        agent_version_id=ver.id,
        call_sid=f"CA{uuid.uuid4().hex[:28]}",
        direction=CallDirection.INBOUND,
        from_number="+14155550101",
        to_number="+14155550100",
        status=CallStatus.COMPLETED,
        duration_seconds=120.0,
        booked=True,
        escalated=False,
        llm_used="openai/gpt-4o-mini",
        started_at=now - timedelta(hours=2),
        ended_at=now - timedelta(hours=2) + timedelta(seconds=120),
    )
    # Call 2: Transferred, 360s (long call), negative sentiment, 600ms e2e p50 / 900ms p95
    c2 = Call(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        agent_version_id=ver.id,
        call_sid=f"CA{uuid.uuid4().hex[:28]}",
        direction=CallDirection.INBOUND,
        from_number="+14155550102",
        to_number="+14155550100",
        status=CallStatus.TRANSFERRED,
        transfer_state=TransferState.CONNECTED,
        duration_seconds=360.0,
        booked=False,
        escalated=True,
        llm_used="openai/gpt-4o-mini",
        started_at=now - timedelta(hours=1),
        ended_at=now - timedelta(hours=1) + timedelta(seconds=360),
    )
    # Call 3: Voicemail (failed/machine), 30s, neutral sentiment
    c3 = Call(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        agent_version_id=ver.id,
        call_sid=f"CA{uuid.uuid4().hex[:28]}",
        direction=CallDirection.OUTBOUND,
        from_number="+14155550100",
        to_number="+14155550103",
        status=CallStatus.NO_ANSWER,
        failure_reason="voicemail_machine_detected",
        duration_seconds=30.0,
        booked=False,
        escalated=False,
        llm_used="openai/gpt-4o-mini",
        started_at=now - timedelta(minutes=30),
        ended_at=now - timedelta(minutes=29, seconds=30),
    )
    db.add_all([c1, c2, c3])
    await db.flush()

    db.add_all(
        [
            CallLatencyStat(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                call_id=c1.id,
                turns=4,
                e2e_ms=320.0,
                e2e_p50_ms=320.0,
                e2e_p95_ms=480.0,
                llm_ttfb_p50_ms=180.0,
                llm_ttfb_p95_ms=260.0,
            ),
            CallLatencyStat(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                call_id=c2.id,
                turns=6,
                e2e_ms=600.0,
                e2e_p50_ms=600.0,
                e2e_p95_ms=900.0,
                llm_ttfb_p50_ms=310.0,
                llm_ttfb_p95_ms=450.0,
            ),
            SentimentResult(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                call_id=c1.id,
                scope="call",
                scope_key=f"call:{c1.id}",
                label="positive",
                confidence=88,
            ),
            SentimentResult(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                call_id=c2.id,
                scope="call",
                scope_key=f"call:{c2.id}",
                label="negative",
                confidence=82,
            ),
            SentimentResult(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                call_id=c3.id,
                scope="call",
                scope_key=f"call:{c3.id}",
                label="neutral",
                confidence=75,
            ),
        ]
    )
    await db.flush()
    return agent, ver, [c1, c2, c3]


@pytest.mark.asyncio
async def test_analytics_aggregation_correctness_and_group_by_dimensions(
    db: AsyncSession,
):
    """compute_metrics_and_breakdown computes accurate rates, latency p50/p95, cost, sentiment, and group breakdowns."""
    tenant_a = await make_tenant(db, name="Analytics Tenant A")
    tenant_b = await make_tenant(db, name="Analytics Tenant B")
    agent_a, ver_a, _ = await _seed_analytics_calls(db, tenant_a.id)
    # Seed calls in tenant_b to verify strict tenant isolation
    await _seed_analytics_calls(db, tenant_b.id)

    by_agent = await analytics_service.compute_metrics_and_breakdown(
        db, tenant_a.id, group_by="agent"
    )
    summary = by_agent["summary"]
    assert summary["total_calls"] == 3
    assert summary["successful_calls"] == 1
    assert summary["success_rate"] == pytest.approx(33.33, abs=0.05)
    assert summary["avg_duration_seconds"] == pytest.approx(170.0, abs=0.05)
    assert summary["p95_duration_seconds"] > 300.0
    assert summary["total_cost_usd"] > 0.0
    assert summary["avg_cost_per_call_usd"] > 0.0
    assert summary["latency_p50_ms"] == pytest.approx(460.0, abs=0.1)
    assert summary["latency_p95_ms"] >= 850.0
    assert summary["transfer_rate"] == pytest.approx(33.33, abs=0.05)
    assert summary["voicemail_rate"] == pytest.approx(33.33, abs=0.05)
    assert summary["disconnect_reasons"]["completed"] == 1
    assert summary["disconnect_reasons"]["transferred"] == 1
    assert summary["disconnect_reasons"]["voicemail"] == 1
    assert summary["sentiment"]["positive"] == 1
    assert summary["sentiment"]["negative"] == 1
    assert summary["sentiment"]["neutral"] == 1

    assert len(by_agent["groups"]) == 1
    assert by_agent["groups"][0]["key"] == str(agent_a.id)
    assert by_agent["groups"][0]["label"] == "Support Voice Agent"

    by_ver = await analytics_service.compute_metrics_and_breakdown(
        db, tenant_a.id, group_by="version"
    )
    assert len(by_ver["groups"]) == 1
    assert by_ver["groups"][0]["key"] == str(ver_a.id)
    assert by_ver["groups"][0]["label"] == "v1"

    by_num = await analytics_service.compute_metrics_and_breakdown(
        db, tenant_a.id, group_by="number"
    )
    assert len(by_num["groups"]) == 1
    assert by_num["groups"][0]["key"] == "+14155550100"


@pytest.mark.asyncio
async def test_custom_dashboards_tenant_scoping_limits_share_and_csv(
    db: AsyncSession,
):
    """Saved custom dashboards enforce widget/size limits, tenant isolation, shareable links, and CSV export."""
    tenant_a = await make_tenant(db, name="Dashboard Tenant A")
    tenant_b = await make_tenant(db, name="Dashboard Tenant B")
    await _seed_analytics_calls(db, tenant_a.id)

    dash = await custom_dashboard_service.create_dashboard(
        db,
        tenant_a.id,
        name="Executive Voice QA Dashboard",
        description="Tracks success rate, latency, and cost",
        widgets=[
            {
                "title": "Success Rate by Agent",
                "metric": "success_rate",
                "dimension": "agent",
                "chart_type": "bar",
                "filters": {},
            },
            {
                "title": "P95 Latency by Day",
                "metric": "latency_p95_ms",
                "dimension": "day",
                "chart_type": "line",
                "filters": {},
            },
        ],
        share_enabled=True,
    )
    dash_id = uuid.UUID(dash["id"])
    assert dash["share_enabled"] is True
    assert dash["share_token"] is not None

    # Tenant B cannot read, update, or delete Tenant A's dashboard
    with pytest.raises(NotFoundError):
        await custom_dashboard_service.get_dashboard(db, tenant_b.id, dash_id)
    with pytest.raises(NotFoundError):
        await custom_dashboard_service.delete_dashboard(db, tenant_b.id, dash_id)

    # Evaluate widgets via shareable token
    shared = await custom_dashboard_service.get_shared_dashboard(
        db, dash["share_token"]
    )
    assert shared["summary"]["total_calls"] == 3
    assert len(shared["widgets"]) == 2
    assert shared["widgets"][0]["value"] == pytest.approx(33.33, abs=0.05)

    # Export CSV
    csv_text = await custom_dashboard_service.export_dashboard_csv(
        db, tenant_a.id, dash_id
    )
    assert "widget_id,widget_title,metric,dimension" in csv_text
    assert "Success Rate by Agent" in csv_text
    assert "P95 Latency by Day" in csv_text

    # Enforces MAX_WIDGETS_PER_DASHBOARD limit (24)
    too_many_widgets = [
        {
            "title": f"Widget {i}",
            "metric": "total_calls",
            "dimension": "day",
            "chart_type": "bar",
        }
        for i in range(custom_dashboard_service.MAX_WIDGETS_PER_DASHBOARD + 1)
    ]
    with pytest.raises(BadRequestError, match="exceeds maximum"):
        await custom_dashboard_service.create_dashboard(
            db,
            tenant_a.id,
            name="Oversized Dashboard",
            widgets=too_many_widgets,
        )


@pytest.mark.asyncio
async def test_qa_post_call_sampling_policies_and_ab_experiment_live_split(
    client: AsyncClient, db: AsyncSession
):
    """Functional verification: QA sampling policies (negative sentiment, transfers, long calls) and A/B traffic split."""
    tenant = await make_tenant(db, name="QA and AB Tenant")
    admin = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent, _, calls = await _seed_analytics_calls(db, tenant.id)
    c1, c2, _ = calls

    # 1. Verify QA sampling policies (percentage, negative_sentiment, transfers, long_calls)
    matched_neg, _ = await qa_service.evaluate_post_call_sampling_policy(
        db, call=c2, policy={"kind": "negative_sentiment"}
    )
    assert matched_neg is True

    matched_transfer, _ = await qa_service.evaluate_post_call_sampling_policy(
        db, call=c2, policy={"kind": "transfers"}
    )
    assert matched_transfer is True

    matched_long, _ = await qa_service.evaluate_post_call_sampling_policy(
        db, call=c2, policy={"kind": "long_calls", "min_duration_seconds": 300}
    )
    assert matched_long is True

    matched_short, _ = await qa_service.evaluate_post_call_sampling_policy(
        db, call=c1, policy={"kind": "long_calls", "min_duration_seconds": 300}
    )
    assert matched_short is False

    # 2. Create an A/B experiment, split live traffic across variants, and compute metrics
    headers = await auth_headers(client, admin)
    exp_res = await client.post(
        "/api/experiments",
        headers=headers,
        json={
            "name": "Greeting v1 vs v2",
            "description": "Test concise greeting",
            "agent_id": str(agent.id),
            "variants": [
                {
                    "name": "Control v1",
                    "weight": 50,
                    "is_control": True,
                    "config": {"version": 1},
                },
                {
                    "name": "Challenger v2",
                    "weight": 50,
                    "is_control": False,
                    "config": {"version": 2},
                },
            ],
        },
    )
    assert exp_res.status_code == 201, exp_res.text
    exp_data = exp_res.json()
    exp_id = exp_data["id"]

    start_res = await client.post(
        f"/api/experiments/{exp_id}/start", headers=headers
    )
    assert start_res.status_code == 200, start_res.text

    # Route 12 callers deterministically and record outcomes
    routed_variants = set()
    for idx in range(12):
        call_uuid = uuid.uuid4()
        call_sid = f"CA{uuid.uuid4().hex[:28]}"
        route_res = await client.get(
            f"/api/experiments/{exp_id}/assignment",
            headers=headers,
            params={"call_id": str(call_uuid), "call_sid": f"caller-{idx}-{call_sid}"},
        )
        assert route_res.status_code == 200, route_res.text
        r_body = route_res.json()
        routed_variants.add(r_body["variant_name"])

        rec_res = await client.post(
            f"/api/experiments/{exp_id}/outcomes",
            headers=headers,
            json={
                "call_id": str(call_uuid),
                "call_sid": f"caller-{idx}-{call_sid}",
                "success": not r_body["is_control"],
                "duration": 85.0,
                "csat": 4.8,
                "cost": 0.04,
            },
        )
        assert rec_res.status_code == 201, rec_res.text

    assert len(routed_variants) == 2

    metrics_res = await client.get(
        f"/api/experiments/{exp_id}/metrics", headers=headers
    )
    assert metrics_res.status_code == 200
    m_body = metrics_res.json()
    assert m_body["experiment_id"] == exp_id
    assert len(m_body["variants"]) == 2
