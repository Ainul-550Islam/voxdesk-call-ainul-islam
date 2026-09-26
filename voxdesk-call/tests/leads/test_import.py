"""Bounded, idempotent import."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import Lead
from app.leads import service
from app.leads.exceptions import ImportTooLarge
from app.leads.importers import MAX_BYTES


@pytest.mark.asyncio
async def test_csv_import_is_idempotent(db, tenant_a):
    body = b"name,phone,email\nAda,+1 (555) 500-1001,Ada@Example.com\n"
    first = await service.import_body(
        db,
        tenant_id=tenant_a.id,
        body=body,
        content_type="text/csv",
        idempotency_key="batch-05",
    )
    await db.commit()
    assert first["created"] == 1
    assert first["replayed"] is False
    second = await service.import_body(
        db,
        tenant_id=tenant_a.id,
        body=body,
        content_type="text/csv",
        idempotency_key="batch-05",
    )
    await db.commit()
    assert second["replayed"] is True
    assert second["created"] == 1
    count = (
        await db.execute(select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant_a.id))
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_invalid_row_is_reported_and_oversized_body_is_rejected(db, tenant_a):
    body = b"name,phone\nBad,555\nOk,+15555001002\n"
    result = await service.import_body(
        db, tenant_id=tenant_a.id, body=body, content_type="text/csv", idempotency_key="mixed"
    )
    assert result["created"] == 1
    assert result["errors"]
    with pytest.raises(ImportTooLarge):
        await service.import_body(
            db,
            tenant_id=tenant_a.id,
            body=b"x" * (MAX_BYTES + 1),
            content_type="text/csv",
        )
