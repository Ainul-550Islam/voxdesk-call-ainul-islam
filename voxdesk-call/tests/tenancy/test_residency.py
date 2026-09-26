"""Region policy. A supported label is still not physical residency."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import AuditAction, AuditLog
from app.tenancy.data_residency import evaluate
from app.tenancy.exceptions import ResidencyDenied, ValidationFailed
from app.tenancy.regions import PLACEMENT
from tests.conftest import auth_headers


def test_supported_label_is_not_a_physical_guarantee():
    decision = evaluate("eu-central-1", catalog=frozenset({"eu-central-1"}))
    assert decision.supported is True
    assert decision.applied is False
    assert decision.physical_residency_proven is False
    assert decision.placement == PLACEMENT


def test_unsupported_restricted_and_invalid_labels():
    missing = evaluate("eu-central-1")
    assert missing.supported is False
    assert missing.applied is False
    restricted = evaluate(
        "eu-central-1",
        catalog=frozenset({"eu-central-1"}),
        restricted=frozenset({"eu-central-1"}),
    )
    assert restricted.restricted is True
    assert restricted.supported is False
    with pytest.raises(ValidationFailed):
        evaluate("Not A Region")
    with pytest.raises(ResidencyDenied):
        from app.tenancy.data_residency import require_applicable

        require_applicable(missing)


async def test_http_rejects_unsupported_and_unauthorized(client, db, tenant_a, owner_a, agent_a):
    owner = await auth_headers(client, owner_a)
    unsupported = await client.post(
        f"/api/tenants/{tenant_a.id}/residency",
        json={"region": "eu-central-1"},
        headers=owner,
    )
    assert unsupported.status_code == 422, unsupported.text
    assert unsupported.json()["detail"]["code"] == "residency_denied"

    agent = await auth_headers(client, agent_a)
    denied = await client.post(
        f"/api/tenants/{tenant_a.id}/residency",
        json={"region": "eu-central-1"},
        headers=agent,
    )
    assert denied.status_code == 403, denied.text

    invalid = await client.post(
        f"/api/tenants/{tenant_a.id}/residency",
        json={"region": "!!!"},
        headers=owner,
    )
    assert invalid.status_code == 422, invalid.text

    entries = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                    AuditLog.action == AuditAction.AUTHZ_DENIED,
                )
            )
        )
        .scalars()
        .all()
    )
    reasons = {
        row.detail.get("reason")
        for row in entries
        if row.detail.get("operation") == "residency_change"
    }
    assert "permission_denied" in reasons
    assert entries
    assert all(row.detail.get("physical_residency_proven") is False for row in entries)


@pytest.mark.asyncio
async def test_foreign_region_change_and_placement(client, tenant_a, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)
    foreign = await client.post(
        f"/api/tenants/{tenant_b.id}/residency",
        json={"region": "eu-central-1"},
        headers=headers,
    )
    assert foreign.status_code == 404, foreign.text
    forged = await client.post(
        f"/api/tenants/{tenant_a.id}/residency",
        json={"region": "eu-central-1", "tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert forged.status_code == 404, forged.text
    placement = await client.get(
        f"/api/organizations/{tenant_a.organization_id}/placement",
        headers=headers,
    )
    assert placement.status_code == 200, placement.text
    assert placement.json()["physical_residency_proven"] is False
    assert placement.json()["placement"] == "unconfigured"
