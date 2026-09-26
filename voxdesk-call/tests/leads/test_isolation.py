"""Cross-tenant reads are 404. Exports omit credential-shaped custom fields."""

from __future__ import annotations

import pytest

from app.leads import service
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_or_retarget(client, db, tenant_a, tenant_b, owner_a, owner_b):
    lead, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15557001001", name="Private"
    )
    await db.commit()
    foreign = await auth_headers(client, owner_b)
    missing = await client.get(f"/api/tenants/{tenant_b.id}/leads/{lead.id}", headers=foreign)
    assert missing.status_code == 404
    own = await auth_headers(client, owner_a)
    crossed = await client.post(
        f"/api/tenants/{tenant_a.id}/lead-records",
        headers=own,
        json={"phone": "+15557001002", "name": "Injected", "tenant_id": str(tenant_b.id)},
    )
    assert crossed.status_code == 404
    visible = await client.get(f"/api/tenants/{tenant_a.id}/leads/{lead.id}", headers=own)
    assert visible.status_code == 200
    assert visible.json()["tenant_id"] == str(tenant_a.id)


@pytest.mark.asyncio
async def test_export_drops_secrets(db, tenant_a):
    lead, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15557001003", name="Export"
    )
    lead.custom_fields = {"api_key": "super-secret-value", "city": "Dhaka"}
    await db.commit()
    text = await service.export_leads(db, tenant_id=tenant_a.id)
    assert "super-secret-value" not in text
    assert "api_key" not in text
    assert "Dhaka" in text
