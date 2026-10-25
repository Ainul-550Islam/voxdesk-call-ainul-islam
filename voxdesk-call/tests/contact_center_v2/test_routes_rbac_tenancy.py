import uuid
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.assist import upsert_script
from app.db.models import User, UserRole
from tests.conftest import auth_headers, make_user
from tests.contact_center_v2.conftest import OPEN_WINDOWS, SAFE_HARBOR, Cc2Scope


def policy_payload(scope: Cc2Scope) -> dict:
    return {
        "campaign_id": str(scope.campaign.id),
        "mode": "predictive",
        "caller_id_number": "+15550001000",
        "pacing_ratio": "1",
        "abandonment_cap": "0.03",
        "max_inflight": 4,
        "calling_windows": OPEN_WINDOWS,
        "holiday_dates": [],
        "safe_harbor_message": SAFE_HARBOR,
        "consent_required": True,
        "jurisdiction": "US",
    }


async def test_dialer_policy_rbac_and_tenant_isolation(
    client: AsyncClient,
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    cc2_caller,
    manager_a: User,
    agent_a: User,
    viewer_a: User,
    tenant_b,
):
    manager_headers = await auth_headers(client, manager_a)
    agent_headers = await auth_headers(client, agent_a)
    viewer_headers = await auth_headers(client, viewer_a)
    response = await client.post("/api/cc2/dialer/policies", json=policy_payload(cc2_scope), headers=agent_headers)
    assert response.status_code == 403
    response = await client.post("/api/cc2/dialer/policies", json=policy_payload(cc2_scope), headers=manager_headers)
    assert response.status_code == 201, response.text
    policy_id = response.json()["policy"]["id"]
    viewer_response = await client.get(f"/api/cc2/dialer/policies/{policy_id}", headers=viewer_headers)
    assert viewer_response.status_code == 403
    other_manager = await make_user(db, tenant_b, UserRole.MANAGER)
    other_headers = await auth_headers(client, other_manager)
    isolated = await client.get(f"/api/cc2/dialer/policies/{policy_id}", headers=other_headers)
    assert isolated.status_code == 404


async def test_predictive_start_api_refuses_cleanly_without_agent(
    client: AsyncClient,
    policy_factory,
    lead_factory,
    manager_a: User,
):
    await lead_factory()
    policy = await policy_factory()
    headers = await auth_headers(client, manager_a)
    prepare = await client.post(f"/api/cc2/dialer/policies/{policy.id}/prepare", headers=headers)
    assert prepare.status_code == 200, prepare.text
    started = await client.post(f"/api/cc2/dialer/policies/{policy.id}/start", headers=headers)
    assert started.status_code == 409, started.text
    assert started.json()["detail"]["code"] == "no_available_agent"


async def test_supervisor_wallboard_is_not_an_agent_or_viewer_surface(
    client: AsyncClient,
    manager_a: User,
    agent_a: User,
    viewer_a: User,
):
    for user in (agent_a, viewer_a):
        headers = await auth_headers(client, user)
        response = await client.get("/api/cc2/supervisor/wallboard", headers=headers)
        assert response.status_code == 403
    headers = await auth_headers(client, manager_a)
    response = await client.get("/api/cc2/supervisor/wallboard", headers=headers)
    assert response.status_code == 200, response.text
    assert set(response.json()) >= {"queues", "agents", "callbacks", "assist"}


async def test_unknown_cc2_policy_is_not_found_not_redirected(
    client: AsyncClient, manager_a: User,
):
    headers = await auth_headers(client, manager_a)
    response = await client.get(f"/api/cc2/dialer/policies/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_compliance_script_routes_enforce_rbac_and_tenant_isolation(
    client: AsyncClient,
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    admin_a: User,
    agent_a: User,
    viewer_a: User,
    tenant_b,
):
    script = await upsert_script(
        db, tenant_id=cc2_scope.tenant.id, environment_id=cc2_scope.environment.id,
        created_by=admin_a.id, name="Route disclosure", script_text="Disclose the call purpose",
        required_phrases=[], forbidden_phrases=[],
    )
    await db.commit()
    agent_headers = await auth_headers(client, agent_a)
    viewer_headers = await auth_headers(client, viewer_a)
    admin_headers = await auth_headers(client, admin_a)
    assert (await client.get("/api/cc2/assist/compliance-scripts", headers=agent_headers)).status_code == 200
    assert (await client.get("/api/cc2/assist/compliance-scripts", headers=viewer_headers)).status_code == 403
    denied = await client.put(
        f"/api/cc2/assist/compliance-scripts/{script.id}", headers=agent_headers,
        json={"script_text": "Agent cannot edit this"},
    )
    assert denied.status_code == 403
    updated = await client.put(
        f"/api/cc2/assist/compliance-scripts/{script.id}", headers=admin_headers,
        json={"script_text": "Disclose the revised call purpose"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["script"]["version"] == 2
    other_admin = await make_user(db, tenant_b, UserRole.ADMIN)
    other_headers = await auth_headers(client, other_admin)
    isolated = await client.get("/api/cc2/assist/compliance-scripts", headers=other_headers)
    assert isolated.status_code == 200 and isolated.json()["scripts"] == []


async def test_agent_state_and_occupancy_are_current_agent_only(
    client: AsyncClient,
    agent_a: User,
    manager_a: User,
    viewer_a: User,
):
    agent_headers = await auth_headers(client, agent_a)
    viewer_headers = await auth_headers(client, viewer_a)
    current = await client.get("/api/cc2/agent-states/me", headers=agent_headers)
    assert current.status_code == 200, current.text
    assert current.json()["presence"]["user_id"] == str(agent_a.id)
    assert (await client.get("/api/cc2/agent-states/me", headers=viewer_headers)).status_code == 403
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    params = {"start": start.isoformat(), "end": end.isoformat()}
    own = await client.get("/api/cc2/agent-states/occupancy", params=params, headers=agent_headers)
    assert own.status_code == 200, own.text
    other = await client.get(
        "/api/cc2/agent-states/occupancy", params={**params, "user_id": str(manager_a.id)}, headers=agent_headers,
    )
    assert other.status_code == 403
