"""Focused integration coverage for the additive governance foundation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.governance.evidence import verify_chain
from app.governance.hashing import canonical_json, sha256_hex
from app.governance.models import EvidenceEvent, ModelVersion
from tests.conftest import auth_headers


def test_canonical_hash_is_stable_for_key_order_and_utc():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


@pytest.mark.asyncio
async def test_missing_policy_fails_closed_and_leaves_evidence(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/decisions",
        json={"policy_type": "runtime_access", "input_payload": {"operation": "call"}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "deny"
    assert body["reason_code"] == "governance_configuration_missing"

    rows = list(
        (
            await db.execute(
                select(EvidenceEvent).where(EvidenceEvent.tenant_id == tenant_a.id)
            )
        ).scalars()
    )
    assert rows
    assert verify_chain(sorted(rows, key=lambda row: row.sequence)) is True


@pytest.mark.asyncio
async def test_policy_publish_then_decide_and_foreign_tenant_is_hidden(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/policies",
        json={
            "name": "Runtime access",
            "policy_type": "runtime_access",
            "rules": {"decision": "allow", "required_fields": ["operation"]},
            "rationale": "Tenant policy",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    published = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/policies/{policy_id}/publish",
        headers=headers,
    )
    assert published.status_code == 200, published.text
    decision = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/decisions",
        json={"policy_type": "runtime_access", "input_payload": {"operation": "call"}},
        headers=headers,
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["decision"] == "allow"

    foreign = await client.post(
        f"/api/tenants/{tenant_b.id}/governance/decisions",
        json={"policy_type": "runtime_access", "input_payload": {}},
        headers=headers,
    )
    assert foreign.status_code == 404, foreign.text


@pytest.mark.asyncio
async def test_model_approval_requires_authoritative_evaluation_and_is_tenant_scoped(
    client, db, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    registry = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/models",
        json={
            "provider": "test-provider",
            "model_name": "test-model",
            "display_name": "Test Model",
            "risk_tier": "moderate",
            "intended_use": "Test-only evaluation",
        },
        headers=headers,
    )
    assert registry.status_code == 201, registry.text
    version = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/models/{registry.json()['id']}/versions",
        json={
            "version": "2026-09-27",
            "artifact_uri": "registry://test-model/2026-09-27",
            "artifact_digest": "a" * 64,
        },
        headers=headers,
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]
    premature = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/models/versions/{version_id}/approve",
        params={"approval_reference": "review-1"},
        headers=headers,
    )
    assert premature.status_code == 403, premature.text
    assert premature.json()["detail"]["code"] == "authoritative_verification_required"

    evaluated = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/models/versions/{version_id}/evaluate",
        json={
            "verifier": "external-evaluator",
            "verification_reference": "attestation://review-1",
            "passed": True,
        },
        headers=headers,
    )
    assert evaluated.status_code == 200, evaluated.text
    approved = await client.post(
        f"/api/tenants/{tenant_a.id}/governance/models/versions/{version_id}/approve",
        params={"approval_reference": "review-1"},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    foreign = await client.get(
        f"/api/tenants/{tenant_b.id}/governance/models", headers=headers
    )
    assert foreign.status_code == 404, foreign.text
    persisted = await db.get(ModelVersion, uuid.UUID(version_id))
    assert persisted is not None
    assert persisted.tenant_id == tenant_a.id
