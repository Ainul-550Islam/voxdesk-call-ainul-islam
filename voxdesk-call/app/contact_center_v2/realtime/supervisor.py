"""Tenant-scoped supervisor projection shared by HTTP and WebSocket delivery."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.acd.wallboard import wallboard_snapshot
from app.contact_center_v2.repository import utcnow


async def supervisor_snapshot(session: AsyncSession, *, tenant_id: uuid.UUID) -> dict[str, Any]:
    snapshot = await wallboard_snapshot(session, tenant_id=tenant_id)
    snapshot["streamed_at"] = utcnow().isoformat()
    snapshot["source"] = "durable_contact_center_state"
    return snapshot


__all__ = ["supervisor_snapshot"]
