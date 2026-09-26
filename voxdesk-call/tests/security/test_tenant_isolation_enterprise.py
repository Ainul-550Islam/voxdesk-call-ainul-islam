"""Enterprise isolation across organization, tenant, environment and flags."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Environment
from app.tenancy.feature_flags import evaluate
from app.tenancy.repository import environment_in_tenant
from tests.conftest import auth_headers


def test_flag_precedence_and_locked_bypass():
    chosen = evaluate(
        "outbound_calling",
        organization={"outbound_calling": False},
        tenant={"outbound_calling": False},
        environment={"outbound_calling": True},
        trust_overrides=True,
    )
    assert chosen.enabled is True
    assert chosen.source == "environment"
    assert chosen.persisted is False

    locked = evaluate(
        "residency_override",
        environment={"residency_override": True},
        trust_overrides=True,
    )
    assert locked.enabled is False
    assert locked.source == "locked"
    assert locked.persisted is False


@pytest.mark.asyncio
async def test_cross_tenant_reads_and_flag_writes_are_not_found(
    client, db, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    hierarchy = await client.get(f"/api/tenants/{tenant_b.id}/hierarchy", headers=headers)
    assert hierarchy.status_code == 404, hierarchy.text

    environments = await client.get(f"/api/tenants/{tenant_b.id}/environments", headers=headers)
    assert environments.status_code == 404, environments.text

    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_b.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    crossed = await client.get(
        f"/api/tenants/{tenant_a.id}/hierarchy",
        params={"environment_id": str(production.id)},
        headers=headers,
    )
    assert crossed.status_code == 404, crossed.text
    assert await environment_in_tenant(db, tenant_a.id, production.id) is None

    flags = await client.post(
        f"/api/tenants/{tenant_b.id}/feature-flags/evaluate",
        json={"flag": "outbound_calling"},
        headers=headers,
    )
    assert flags.status_code == 404, flags.text
    posture = await client.get(f"/api/tenants/{tenant_a.id}/security/posture", headers=headers)
    assert posture.status_code == 200, posture.text
    assert posture.json()["tenant_id"] == str(tenant_a.id)
    assert posture.json()["client_tenant_override"] is False
    hidden = await client.get(f"/api/tenants/{tenant_b.id}/security/posture", headers=headers)
    assert hidden.status_code == 404, hidden.text


async def test_settings_omit_secrets_and_overrides_are_refused(client, db, tenant_a, owner_a):
    tenant_a.crm_api_key = "super-secret-value"
    await db.commit()
    headers = await auth_headers(client, owner_a)
    settings = await client.get(f"/api/tenants/{tenant_a.id}/settings", headers=headers)
    assert settings.status_code == 200, settings.text
    encoded = settings.text
    assert "super-secret-value" not in encoded
    assert "crm_api_key" not in settings.json()

    override = await client.post(
        f"/api/tenants/{tenant_a.id}/feature-flags/evaluate",
        json={"flag": "outbound_calling", "environment_override": True},
        headers=headers,
    )
    assert override.status_code == 403, override.text
    claimed = await client.get(
        f"/api/tenants/{tenant_a.id}/hierarchy",
        params={"claimed_tenant_id": "00000000-0000-0000-0000-000000000099"},
        headers=headers,
    )
    assert claimed.status_code == 404, claimed.text
