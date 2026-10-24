"""Tests enforcing strict honesty in catalog, capability matrix, and existing connector registry."""

from __future__ import annotations

import json
import uuid

import pytest

from app.integrations.connector import ConnectorContext, registry
from app.integrations_v2.catalog import build_provider_catalog
from app.integrations_v2.core.connection_store import upsert_connection
from app.integrations_v2.matrix import build_capability_matrix
from app.integrations_v2.register_all import register_v2_into_connector_registry
from app.integrations_v2.settings import reset_int2_settings_cache
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_no_provider_shows_live_verified_without_evidence_artifact_and_credentials(
    db, tmp_path, monkeypatch
):
    for var in (
        "ZENDESK_CLIENT_ID",
        "ZENDESK_CLIENT_SECRET",
        "SERVICENOW_CLIENT_ID",
        "ZOHO_CLIENT_ID",
        "FRESHSALES_CLIENT_ID",
        "DYNAMICS_CLIENT_ID",
        "PIPEDRIVE_CLIENT_ID",
        "TEAMS_CLIENT_ID",
        "ZOOM_CLIENT_ID",
        "SLACK_CLIENT_ID",
        "FRESHDESK_DOMAIN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("INT2_LIVE_EVIDENCE_DIR", str(tmp_path))
    reset_int2_settings_cache()

    tenant = await make_tenant(db, "Honesty Matrix Tenant")

    # 1. With no env credentials, no DB connections, and no evidence artifact:
    # every single provider MUST be NOT_CONFIGURED, and zero are LIVE_VERIFIED.
    matrix_initial = await build_capability_matrix(db, tenant_id=tenant.id)
    assert len(matrix_initial) == 11
    for item in matrix_initial:
        assert item["verification_status"] == "NOT_CONFIGURED"
        assert item["verification_status"] != "LIVE_VERIFIED"

    catalog_initial = await build_provider_catalog(db, tenant_id=tenant.id)
    for card in catalog_initial:
        assert card["ui_status"] == "NOT_CONFIGURED"

    # 2. Even if an evidence file exists on disk, without credentials it MUST NOT claim LIVE_VERIFIED!
    evidence_json = tmp_path / "zendesk.json"
    evidence_json.write_text(
        json.dumps(
            {
                "provider": "zendesk",
                "passed": True,
                "timestamp": "2026-10-24T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    matrix_unconfigured = await build_capability_matrix(db, tenant_id=tenant.id)
    zd_unconfigured = next(p for p in matrix_unconfigured if p["provider"] == "zendesk")
    assert zd_unconfigured["verification_status"] == "NOT_CONFIGURED"

    # 3. With a tenant connection BUT no passing live evidence artifact -> PROTOCOL_TESTED (never LIVE_VERIFIED)
    await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="freshdesk",
        category="helpdesk",
        auth_kind="api_key",
        credentials={"api_key": "fd-key"},
        config={"domain": "acme"},
    )
    # Also connect Zendesk (which DOES have passing evidence + credentials now)
    await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zendesk",
        category="helpdesk",
        auth_kind="oauth2",
        credentials={"access_token": "zd-tok"},
        config={"subdomain": "acme"},
    )
    await db.commit()

    matrix_after = await build_capability_matrix(db, tenant_id=tenant.id)
    by_slug = {p["provider"]: p for p in matrix_after}
    assert by_slug["freshdesk"]["verification_status"] == "PROTOCOL_TESTED"
    assert by_slug["zendesk"]["verification_status"] == "LIVE_VERIFIED"
    assert by_slug["zendesk"]["last_verified_at"] == "2026-10-24T10:00:00+00:00"

    # 4. Existing connector registry reports NOT_CONFIGURED when credentials are absent
    register_v2_into_connector_registry()
    for slug in (
        "zendesk",
        "servicenow",
        "freshdesk",
        "zoho_desk",
        "zoho_crm",
        "freshsales",
        "dynamics365",
        "pipedrive",
        "teams",
        "zoom",
        "slack",
    ):
        reg = registry.get(slug)
        assert reg is not None
        empty_ctx = ConnectorContext(
            tenant_id=tenant.id,
            connector_id=uuid.uuid4(),
            provider=slug,
            config={},
            credentials={},
        )
        adapter = reg.factory(empty_ctx)
        health = await adapter.health(empty_ctx)
        assert health["status"] == "NOT_CONFIGURED"
        assert health["connected"] is False
