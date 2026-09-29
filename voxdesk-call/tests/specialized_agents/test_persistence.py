"""Shared persistence adapter and parent/child scope behavior."""
from __future__ import annotations


import pytest
from sqlalchemy import select

from app.anomaly.persistence import AnomalyResultRecord, AnomalyRunRecord, persist_anomaly
from app.specialized_agents.persistence import get_execution
from app.specialized_agents.review_bridge import ensure_review_case
from app.specialized_agents.service import SpecializedAgentService
from app.tenancy.isolation import BoundaryDenied
from tests.specialized_agents.test_executor import _admission_rows, _context, _scope_and_user


@pytest.mark.asyncio
async def test_execution_lookup_child_persistence_review_attachment_and_isolation(db):
    tenant, _org, _environment, user, scope = await _scope_and_user(db, "persistence-parent")
    model = await _admission_rows(db, scope, user)
    service = SpecializedAgentService(db, scope)
    context = _context(service, user, model)
    output = {
        "metric": "latency_ms", "detector": "z_score", "quality_state": "verified",
        "configuration": {"threshold": 3.0}, "review_required": True, "review_state": "required",
        "results": [{"metric": "latency_ms", "observation_time": "2026-01-01T00:00:00+00:00", "observed_value": 80.0, "baseline": 20.0, "deviation": 60.0, "detector": "z_score", "threshold": 3.0, "severity": "high", "confidence": 0.9, "quality_state": "verified", "explanation": "Observed deviation exceeds configured threshold.", "anomalous": True, "review_required": True}],
    }
    outcome = await service.execute(context, idempotency_key="persist-parent-01", payload={"metric": "latency_ms"}, handler=lambda *_: output)
    assert await get_execution(db, scope, outcome.record.id, agent_type="anomaly") is not None
    run = await persist_anomaly(db, scope, execution_id=outcome.record.id, output=output)
    assert run.execution_id == outcome.record.id
    result = await db.scalar(select(AnomalyResultRecord).where(AnomalyResultRecord.execution_id == outcome.record.id))
    assert result is not None and result.observed_value == 80.0
    case = await ensure_review_case(db, scope, execution=outcome.record, actor_user_id=user.id)
    assert case is not None and case.execution_id == outcome.record.id
    assert case.environment_id == scope.environment_id

    tenant_b, _org_b, _env_b, _user_b, scope_b = await _scope_and_user(db, "persistence-other")
    assert tenant.id != tenant_b.id
    assert await get_execution(db, scope_b, outcome.record.id, agent_type="anomaly") is None
    with pytest.raises(BoundaryDenied):
        await persist_anomaly(db, scope_b, execution_id=outcome.record.id, output=output)
    assert await db.scalar(select(AnomalyRunRecord).where(AnomalyRunRecord.tenant_id == tenant_b.id)) is None


@pytest.mark.asyncio
async def test_forecast_api_reuses_analytics_and_discloses_unavailable_intervals(client, db):
    from app.db.models import Environment, Organization, UserRole
    from app.forecasting.service import ForecastingService
    from app.governance.models import GovernancePolicy, ModelRegistry, ModelVersion, RiskAssessment
    from tests.conftest import auth_headers, make_tenant, make_user

    tenant = await make_tenant(db, "forecast-api")
    user = await make_user(db, tenant, UserRole.OWNER)
    organization = await db.get(Organization, tenant.organization_id)
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    registry = ModelRegistry(tenant_id=tenant.id, organization_id=organization.id, provider="local-statistical", model_name="forecast-test", display_name="Forecast Test", status="active", risk_tier="high", intended_use="deterministic forecast behavior test", data_classes=["structured"], capabilities=["forecasting"], approved_for_channels=["forecasting"], approved_for_environments=[str(environment.id)], metadata_json={})
    db.add(registry)
    await db.flush()
    model = ModelVersion(registry_id=registry.id, tenant_id=tenant.id, organization_id=organization.id, provider=registry.provider, model_name=registry.model_name, version="1", fingerprint="1"*64, artifact_uri="registry://local/forecast", artifact_digest="2"*64, capabilities=["forecasting"], input_modalities=["structured"], output_modalities=["structured"], status="approved", evaluation_status="passed", evaluation_summary={"authoritative_verifier": "test", "passed": True})
    db.add_all([model, GovernancePolicy(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, name="forecast-policy", policy_type="specialized_agent", status="published", version=1, rules={"decision": "allow"}, rationale="forecast API test", created_by=user.id), RiskAssessment(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, subject_type="specialized_agent", subject_id="forecasting", version=1, tier="high", status="approved", factors={}, required_controls=[], rationale="forecast test", assessed_by=user.id, approved_by=user.id)])
    await db.commit()
    points = [{"period": "2026-01-01", "value": 10}, {"period": "2026-01-02", "value": 12}, {"period": "2026-01-03", "value": 13}]
    response = await client.post("/api/forecast", json={"environment_id": str(environment.id), "model_version_id": str(model.id), "idempotency_key": "forecast-api-001", "series_reference": "daily-usage-series", "series": points, "method": "moving_average", "horizon": 2, "configuration": {"window": 2}}, headers=await auth_headers(client, user))
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["method"] == "moving_average"
    assert all(projection["interval_status"] == "NOT_AVAILABLE" and projection["lower"] is None and projection["upper"] is None for projection in result["projections"])
    assert result["lineage_root_id"] and result["evidence_root_hash"]
    detail = await client.get(f"/api/forecast/{result['execution_id']}?environment_id={environment.id}", headers=await auth_headers(client, user))
    assert detail.status_code == 200
    pure = ForecastingService().analyze(points=[__import__("app.analytics.forecast", fromlist=["UsagePoint"]).UsagePoint(**item) for item in points], method="moving_average", horizon=1, window=2)
    assert pure["interval_method"] == "NOT_AVAILABLE"
