"""Safe lineage capture and tenant-scoped traversal."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .models import LineageRecord
from .service import capture_lineage


async def record_lineage(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    trace_id: str,
    request_id: str | None,
    input_payload: Any,
    output_payload: Any | None = None,
    model_version_id: uuid.UUID | None = None,
    tool_name: str | None = None,
    parent_lineage_id: uuid.UUID | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> LineageRecord:
    return await capture_lineage(
        session,
        scope,
        correlation_id=trace_id,
        input_payload=input_payload,
        output_payload=output_payload,
        tool_fingerprints=[],
        source_references=[],
        model_registry_id=None,
        model_version_id=model_version_id,
        decision_id=None,
        metadata=metadata or {},
        actor_user_id=actor_user_id,
        trace_id=trace_id,
        request_id=request_id,
        source_type=source_type,
        source_id=source_id,
        parent_lineage_id=parent_lineage_id,
        tool_name=tool_name,
    )


async def traverse_lineage(
    session: AsyncSession,
    scope: GovernanceScope,
    lineage_id: uuid.UUID,
    *,
    limit: int = 100,
) -> list[LineageRecord]:
    """Return a tenant/org-bound parent-to-child lineage chain."""
    rows: list[LineageRecord] = []
    current_id: uuid.UUID | None = lineage_id
    seen: set[uuid.UUID] = set()
    while current_id is not None and len(rows) < limit:
        if current_id in seen:
            break
        seen.add(current_id)
        row = await session.scalar(
            select(LineageRecord).where(
                LineageRecord.id == current_id,
                LineageRecord.tenant_id == scope.tenant_id,
                LineageRecord.organization_id == scope.organization_id,
            )
        )
        if row is None:
            break
        rows.append(row)
        current_id = row.parent_lineage_id
    rows.reverse()
    return rows
