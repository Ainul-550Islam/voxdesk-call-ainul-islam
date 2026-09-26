"""Prompt lifecycle, rollout, cost, safety and redaction."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.costs import estimate
from app.ai.guardrails.pii import redact
from app.ai.guardrails.safety import evaluate
from app.ai.models import AIPromptVersion
from app.ai.prompts.rollout import bucket
from app.ai.prompts.versioning import checksum, refuse_mutation
from app.db.models import AuditAction, AuditLog, UserRole
from app.tenancy.isolation import LifecycleDenied
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_published_version_is_immutable_and_rollback_restores_it(
    client, db, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    first = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts",
        json={"prompt_key": "closing", "body": "Goodbye one"},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    published = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts/closing/publish",
        json={"version": 1, "environment_kind": "production"},
        headers=headers,
    )
    assert published.status_code == 200, published.text
    body = published.json()["body"]
    second = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts",
        json={"prompt_key": "closing", "body": "Goodbye two"},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts/closing/publish",
        json={"version": 2, "environment_kind": "production"},
        headers=headers,
    )
    rolled = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts/closing/rollback",
        json={"version": 1, "environment_kind": "production"},
        headers=headers,
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["current_version"] == 1
    stored = await db.scalar(
        select(AIPromptVersion).where(AIPromptVersion.checksum == checksum("Goodbye one"))
    )
    assert stored is not None
    assert stored.body == body
    with pytest.raises(LifecycleDenied):
        refuse_mutation(stored)
    entries = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                    AuditLog.action == AuditAction.SECURITY_SETTINGS_CHANGED,
                )
            )
        )
        .scalars()
        .all()
    )
    operations = {row.detail.get("operation") for row in entries}
    assert "prompt_published" in operations
    assert "prompt_rolled_back" in operations
    assert all("Goodbye" not in str(row.detail) for row in entries)


async def test_rollout_is_deterministic(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    for body in ("stable text", "canary text"):
        await client.post(
            f"/api/tenants/{tenant_a.id}/ai/prompts",
            json={"prompt_key": "canary", "body": body},
            headers=headers,
        )
    await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts/canary/publish",
        json={"version": 1, "environment_kind": "development"},
        headers=headers,
    )
    await client.post(
        f"/api/tenants/{tenant_a.id}/ai/prompts/canary/publish",
        json={"version": 2, "environment_kind": "development"},
        headers=headers,
    )
    rollout = await client.put(
        f"/api/tenants/{tenant_a.id}/ai/prompts/canary/rollout",
        json={
            "stable_version": 1,
            "canary_version": 2,
            "percent": 40,
            "salt": "fixed-salt",
            "environment_kind": "development",
        },
        headers=headers,
    )
    assert rollout.status_code == 200, rollout.text
    first = rollout.json()["assigned_version"]
    again = await client.put(
        f"/api/tenants/{tenant_a.id}/ai/prompts/canary/rollout",
        json={
            "stable_version": 1,
            "canary_version": 2,
            "percent": 40,
            "salt": "fixed-salt",
            "environment_kind": "development",
        },
        headers=headers,
    )
    assert again.json()["assigned_version"] == first
    assert bucket(tenant_a.id, "canary", "fixed-salt") == bucket(
        tenant_a.id, "canary", "fixed-salt"
    )


async def test_eval_does_not_invent_a_score_or_leak_another_tenant(
    client, tenant_a, tenant_b, owner_a, owner_b
):
    owner = await auth_headers(client, owner_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/evals/datasets",
        json={
            "name": "greeting",
            "cases": [
                {
                    "name": "hi",
                    "prompt": "say hi",
                    "checks": [{"name": "exact", "kind": "exact", "reference": "hi"}],
                }
            ],
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]
    missing = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/evals/runs",
        json={"dataset_id": dataset_id},
        headers=owner,
    )
    assert missing.status_code == 201, missing.text
    assert missing.json()["result"]["score_invented"] is False
    assert missing.json()["status"] == "failed"
    scored = await client.post(
        f"/api/tenants/{tenant_a.id}/ai/evals/runs",
        json={"dataset_id": dataset_id, "outputs": {"hi": "hi"}},
        headers=owner,
    )
    assert scored.status_code == 201, scored.text
    assert scored.json()["result"]["passed"] is True
    assert "say hi" not in scored.text
    other = await auth_headers(client, owner_b)
    hidden = await client.get(
        f"/api/tenants/{tenant_b.id}/ai/evals/runs/{scored.json()['id']}",
        headers=other,
    )
    assert hidden.status_code == 404, hidden.text


def test_safety_and_pii_are_explicit():
    denied = evaluate("refund_payment", role=UserRole.OWNER, fresh_mfa=False)
    assert denied.decision == "require_fresh_mfa"
    confirmed = evaluate("refund_payment", role=UserRole.OWNER, fresh_mfa=True)
    assert confirmed.decision == "require_privileged_actor"
    redacted = redact("mail me at ada@example.com or 555-123-4567")
    assert "ada@example.com" not in redacted
    assert "555-123-4567" not in redacted
    unknown = estimate(provider="openai", tokens=3)
    assert unknown["known"] is False
    assert unknown["provider_cost_usd"] is None
