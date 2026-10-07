"""Prompt 5 Integration Tests: Public Website, SEO, Contact Sales, and Auth Boundary Separation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, PublicContactSalesRequest, UserRole
from tests.conftest import TEST_PASSWORD, make_tenant, make_user


@pytest.mark.asyncio
async def test_anonymous_user_can_access_public_site_routes_without_auth(
    client: AsyncClient,
    db: AsyncSession,
):
    # Seed a private tenant + private agent to verify zero leakage on public endpoints
    private_tenant = await make_tenant(db, "TopSecret Financial Corp")
    private_agent = Agent(
        tenant_id=private_tenant.id,
        external_key="agent_secret_vault_999",
        name="Secret Vault Private Agent",
        description="Internal confidential prompt and routing",
        status="published",
        published_version_number=1,
    )
    db.add(private_agent)
    await db.commit()

    # 1. Public manifest
    manifest_resp = await client.get("/api/v1/public/site/manifest")
    assert manifest_resp.status_code == 200, manifest_resp.text
    assert manifest_resp.headers.get("X-VoxDesk-Boundary") == "public"
    manifest = manifest_resp.json()
    assert manifest["site_name"] == "VoxDesk"
    assert "/pricing" in manifest["public_routes"]
    assert "/contact-sales" in manifest["public_routes"]
    assert "/dashboard/" in manifest["protected_route_prefixes"]
    # Verify private tenant/agent names do not leak into public manifest
    raw_manifest_text = manifest_resp.text
    assert "TopSecret Financial Corp" not in raw_manifest_text
    assert "Secret Vault Private Agent" not in raw_manifest_text
    assert "agent_secret_vault_999" not in raw_manifest_text
    assert all(capability.get("status") in {"IMPLEMENTED", "PARTIAL", "MISSING"} for capability in manifest["capabilities"])
    assert all("verified" not in highlight for highlight in manifest["security_highlights"])
    assert all(highlight.get("certification") is False for highlight in manifest["security_highlights"])

    # 2. Public pricing
    pricing_resp = await client.get("/api/v1/public/site/pricing")
    assert pricing_resp.status_code == 200
    tiers = pricing_resp.json()
    assert isinstance(tiers, list) and len(tiers) >= 2
    assert any(t["is_enterprise"] is True and t["cta_href"] == "/contact-sales" for t in tiers)

    # 3. Public status
    status_resp = await client.get("/api/v1/public/site/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["overall_status"] in {"partial", "degraded"}
    comp_ids = {c["id"] for c in status_data["components"]}
    assert {"api_gateway", "database", "public_widget_chat", "public_widget_voice"} <= comp_ids

    # 4. robots.txt and sitemap.xml
    robots_resp = await client.get("/robots.txt")
    assert robots_resp.status_code == 200
    assert "Allow: /product" in robots_resp.text
    assert "Disallow: /dashboard" in robots_resp.text
    assert "Disallow: /api/" in robots_resp.text

    sitemap_resp = await client.get("/sitemap.xml")
    assert sitemap_resp.status_code == 200
    assert "<loc>" in sitemap_resp.text
    assert "/contact-sales</loc>" in sitemap_resp.text
    assert "/dashboard" not in sitemap_resp.text
    assert "Secret Vault Private Agent" not in sitemap_resp.text


@pytest.mark.asyncio
async def test_public_home_and_analytics_expose_evidence_without_synthetic_metrics(
    client: AsyncClient,
):
    home_resp = await client.get("/api/v1/public/home")
    assert home_resp.status_code == 200, home_resp.text
    home = home_resp.json()
    assert home["status"] == "ok"
    assert home["meta"]["registered_api_operations"] > 0
    assert "total_routes" not in home["meta"]
    assert all(
        capability["status"] in {"IMPLEMENTED", "PARTIAL", "MISSING"}
        and isinstance(capability["evidence_routes"], list)
        for capability in home["data"]["capabilities"]
    )
    assert all("verified" not in item for item in home["data"]["security_items"])
    assert all(item["status"] in {"PARTIAL", "MISSING"} for item in home["data"]["security_items"])

    analytics_resp = await client.get(
        "/api/v1/public/analytics/summary",
        params={"tenant_id": "not-a-public-tenant"},
    )
    assert analytics_resp.status_code == 200, analytics_resp.text
    analytics = analytics_resp.json()["data"]
    assert analytics["status"] == "not_configured"
    assert analytics["calls"] is None
    assert analytics["successful_calls"] is None
    assert analytics["average_latency_ms"] is None
    assert analytics["cost"] is None

    health_resp = await client.get("/api/v1/public/health")
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert health["status"] == "ok"
    assert health["database"] == "not_checked"
    assert "total_routes" not in health


@pytest.mark.asyncio
async def test_public_contact_sales_persists_durable_record(
    client: AsyncClient,
    db: AsyncSession,
):
    resp = await client.post(
        "/api/v1/public/contact-sales",
        json={
            "full_name": "Elena Rostova",
            "work_email": "elena@enterprise-health.example.com",
            "company_name": "Enterprise Health Systems",
            "job_title": "VP of Patient Operations",
            "phone_number": "+14155550199",
            "monthly_call_volume": "50k-250k",
            "primary_use_case": "patient_scheduling",
            "message": "Need HIPAA-compliant voice agents and origin-restricted web widget.",
            "source_path": "/contact-sales",
        },
        headers={"Origin": "https://voxdesk.ai"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["work_email"] == "elena@enterprise-health.example.com"
    assert data["company_name"] == "Enterprise Health Systems"
    assert data["status"] == "received"
    assert "Thank you, Elena Rostova" in data["confirmation_message"]

    # Verify durable row in database
    from sqlalchemy import select

    row = (
        await db.execute(
            select(PublicContactSalesRequest).where(
                PublicContactSalesRequest.work_email == "elena@enterprise-health.example.com"
            )
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.company_name == "Enterprise Health Systems"


@pytest.mark.asyncio
async def test_anonymous_user_blocked_from_protected_apis_and_redirected_on_protected_routes(
    client: AsyncClient,
):
    # Protected backend APIs fail closed with 401 when unauthenticated
    for protected_api in (
        "/api/agents",
        "/api/v1/public-keys",
        "/api/v1/conductor/sessions",
        "/auth/me",
    ):
        resp = await client.get(protected_api)
        assert resp.status_code == 401, f"Expected 401 on {protected_api}, got {resp.status_code}"
        assert resp.headers.get("Cache-Control") == "no-store, private"

    # Route boundary check for unauthenticated visit to protected frontend route
    boundary_resp = await client.get(
        "/api/v1/public/site/route-boundary",
        params={"path": "/dashboard/agents", "authenticated": "false"},
    )
    assert boundary_resp.status_code == 200
    decision = boundary_resp.json()
    assert decision["classification"] == "protected"
    assert decision["requires_auth"] is True
    assert decision["redirect_to"] == "/login?next=%2Fdashboard%2Fagents"
    assert decision["cache_control"] == "no-store, private"


@pytest.mark.asyncio
async def test_open_redirect_next_values_rejected_and_safe_local_next_allowed(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Redirect Security Tenant")
    user = await make_user(db, tenant, UserRole.OWNER, email="owner-redirect@example.com")

    malicious_next_values = [
        "http://evil.example.com/phish",
        "https://evil.example.com/login",
        "//evil.example.com/steal",
        "/\\evil.example.com",
        "javascript:alert(document.cookie)",
        "data:text/html,<script>alert(1)</script>",
        "https://user@evil.example.com/",
    ]

    for bad_next in malicious_next_values:
        # 1. Login with malicious `next` fails closed with 422
        login_resp = await client.post(
            "/auth/login",
            json={
                "email": user.email,
                "password": TEST_PASSWORD,
                "next": bad_next,
            },
        )
        assert login_resp.status_code == 422, f"Expected 422 for next={bad_next}, got {login_resp.status_code}"

        # 2. Signup with malicious `next_path` fails closed with 422
        signup_resp = await client.post(
            "/auth/signup",
            json={
                "organization_name": "Test Org",
                "full_name": "Test Owner",
                "email": "new-signup-check@example.com",
                "password": TEST_PASSWORD,
                "next_path": bad_next,
            },
        )
        assert signup_resp.status_code == 422, f"Expected 422 for signup next_path={bad_next}"

        # 3. Non-strict validator endpoint sanitizes to /dashboard and flags is_safe=False
        val_resp = await client.get("/auth/validate-next", params={"next": bad_next})
        assert val_resp.status_code == 200
        val_body = val_resp.json()
        assert val_body["is_safe"] is False
        assert val_body["sanitized_next"] == "/dashboard"

    # Safe local `next` path succeeds on login and signup
    safe_login = await client.post(
        "/auth/login",
        json={
            "email": user.email,
            "password": TEST_PASSWORD,
            "next": "/dashboard/agents",
        },
    )
    assert safe_login.status_code == 200, safe_login.text
    assert safe_login.json()["redirect_to"] == "/dashboard/agents"

    safe_signup = await client.post(
        "/auth/signup",
        json={
            "organization_name": "Quantum Voice Labs",
            "full_name": "Dr. Aria Vance",
            "email": "aria@quantumvoicelabs.example.com",
            "password": TEST_PASSWORD,
            "industry": "healthcare",
            "next_path": "/studio/conductor",
        },
    )
    assert safe_signup.status_code == 201, safe_signup.text
    signup_data = safe_signup.json()
    assert signup_data["organization_name"] == "Quantum Voice Labs"
    assert signup_data["redirect_to"] == "/studio/conductor"
    assert signup_data["access_token"]

    # Verify newly signed-up user can immediately access protected /auth/me
    me_resp = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {signup_data['access_token']}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["user"]["email"] == "aria@quantumvoicelabs.example.com"
