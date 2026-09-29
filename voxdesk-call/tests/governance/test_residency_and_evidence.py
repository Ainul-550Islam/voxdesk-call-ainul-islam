"""Residency fail-closed and evidence package integration coverage."""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_residency_intent_is_durable_but_never_proof_without_verifier(
    client, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    requested = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/residency/intents",
        json={"region": "eu-central-1"},
        headers=headers,
    )
    assert requested.status_code == 201, requested.text
    intent = requested.json()
    assert intent["status"] == "rejected"
    assert intent["physical_residency_proven"] is False

    verification = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/residency/intents/{intent['id']}/verify",
        json={
            "verifier": "operator",
            "verification_reference": "external-review-1",
            "verification_metadata": {},
        },
        headers=headers,
    )
    assert verification.status_code == 403, verification.text
    assert verification.json()["detail"]["code"] == "residency_verification_required"


@pytest.mark.asyncio
async def test_evidence_package_has_reproducible_root_and_foreign_scope_is_hidden(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    decision = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/decisions",
        json={"policy_type": "data_usage", "input_payload": {}},
        headers=headers,
    )
    assert decision.status_code == 200, decision.text
    package = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/evidence/packages",
        json={},
        headers=headers,
    )
    assert package.status_code == 201, package.text
    body = package.json()
    assert body["event_count"] >= 1
    assert body["evidence_root"]
    assert body["package_hash"]
    assert body["manifest"]["evidence_root"] == body["evidence_root"]

    foreign = await client.get(
        f"/api/tenants/{tenant_b.id}/governance/evidence/packages/{body['id']}",
        headers=headers,
    )
    assert foreign.status_code == 404, foreign.text
