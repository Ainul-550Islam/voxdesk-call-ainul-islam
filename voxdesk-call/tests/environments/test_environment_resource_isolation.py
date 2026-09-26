"""Cross-tenant and cross-environment isolation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, KnowledgeChunk, KnowledgeDocument, Lead
from app.knowledge.vectorstore import _base_query
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _production(db, tenant) -> Environment:
    return (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production",
        )
    )).scalar_one()


async def _staging(db, tenant) -> Environment:
    row = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(row)
    await db.flush()
    return row


async def test_production_does_not_list_staging_or_foreign_rows(
    client, db, tenant_a, tenant_b, owner_a
):
    production = await _production(db, tenant_a)
    staging = await _staging(db, tenant_a)
    foreign = await _production(db, tenant_b)
    db.add(Lead(
        tenant_id=tenant_a.id, environment_id=staging.id, name="Stage", phone="+15552000001",
    ))
    db.add(Lead(
        tenant_id=tenant_b.id, environment_id=foreign.id, name="Other", phone="+15552000002",
    ))
    await db.commit()
    headers = await auth_headers(client, owner_a)
    page = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources/lead",
        headers=headers,
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 0
    swapped = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{foreign.id}/resources/lead",
        headers=headers,
    )
    assert swapped.status_code == 404, swapped.text


async def test_a_foreign_resource_id_is_not_readable_in_this_environment(
    client, db, tenant_a, tenant_b, owner_a
):
    production = await _production(db, tenant_a)
    foreign_env = await _production(db, tenant_b)
    foreign = Lead(
        tenant_id=tenant_b.id, environment_id=foreign_env.id, name="Hidden", phone="+15552000003",
    )
    db.add(foreign)
    await db.commit()
    headers = await auth_headers(client, owner_a)
    response = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources/lead/{foreign.id}",
        headers=headers,
    )
    assert response.status_code == 404, response.text


async def test_retrieval_does_not_cross_environments(db, tenant_a):
    production = await _production(db, tenant_a)
    staging = await _staging(db, tenant_a)
    from app.db.models import DocumentStatus
    staging_doc = KnowledgeDocument(
        tenant_id=tenant_a.id, environment_id=staging.id, title="Staging only",
        content_hash="a" * 64, status=DocumentStatus.READY, version=1,
    )
    production_doc = KnowledgeDocument(
        tenant_id=tenant_a.id, environment_id=production.id, title="Production",
        content_hash="b" * 64, status=DocumentStatus.READY, version=1,
    )
    db.add_all([staging_doc, production_doc])
    await db.flush()
    db.add(KnowledgeChunk(
        document_id=staging_doc.id, tenant_id=tenant_a.id, environment_id=staging.id,
        chunk_index=0, version=1, text="staging secret", content_hash="c" * 64,
    ))
    db.add(KnowledgeChunk(
        document_id=production_doc.id, tenant_id=tenant_a.id, environment_id=production.id,
        chunk_index=0, version=1, text="production fact", content_hash="d" * 64,
    ))
    await db.flush()
    from app.db.models import DocumentStatus
    staging_doc.status = DocumentStatus.READY
    production_doc.status = DocumentStatus.READY
    await db.flush()
    production_rows = (await db.execute(
        _base_query(tenant_a.id, environment_id=production.id)
    )).all()
    staging_rows = (await db.execute(
        _base_query(tenant_a.id, environment_id=staging.id)
    )).all()
    assert [row[0].text for row in production_rows] == ["production fact"]
    assert [row[0].text for row in staging_rows] == ["staging secret"]
    assert uuid.UUID(str(production_rows[0][0].environment_id)) == production.id
