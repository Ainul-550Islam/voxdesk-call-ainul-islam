"""Escalation facade over the authoritative telephony transfer service.

This module never treats an AI request as a completed handoff. A transfer
request needs the persisted call, its tenant, and the transaction/session so the
existing transfer service can validate scope, record intent, invoke the
telephony provider, and distinguish provider acceptance from human connection.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Call, Tenant
from app.telephony.transfer_service import request_transfer


class Escalation:
    async def transfer(
        self,
        call_id: str,
        reason: str,
        *,
        session: AsyncSession | None = None,
        tenant: Tenant | None = None,
        call: Call | None = None,
    ) -> dict[str, str | bool]:
        """Request a real, tenant-scoped transfer; never synthesize success."""
        if session is None or tenant is None or call is None:
            raise ValueError("human transfer requires the persisted call, tenant, and database session")
        if str(call.id) != str(call_id):
            raise ValueError("human transfer call id does not match the persisted call")
        result = await request_transfer(session, tenant, call, reason=reason)
        return result.as_tool_result()
