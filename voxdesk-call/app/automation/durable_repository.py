"""Persist automation runs and action receipts.

The in-process registries are a request cache. These tables are the source of
truth for worker recovery. Existing ``automations`` and ``automation_runs``
rows are reused; action receipts are new.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Automation, AutomationActionReceipt, AutomationRun


async def get_definition(
    session: AsyncSession, *, tenant_id: uuid.UUID, automation_id: str
) -> Automation | None:
    row = await session.get(Automation, automation_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


async def upsert_run(
    session: AsyncSession,
    *,
    run_id: str,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    automation_id: str,
    idempotency_key: str,
    event: str,
    business_event_id: str,
    status: str,
    attempts: int = 0,
    last_error: str = "",
) -> AutomationRun:
    row = await session.get(AutomationRun, run_id)
    if row is None:
        row = AutomationRun(
            id=run_id,
            tenant_id=tenant_id,
            environment_id=environment_id,
            automation_id=automation_id,
            idempotency_key=idempotency_key,
            event=event,
            business_event_id=business_event_id,
            status=status,
            attempts=attempts,
            last_error=last_error[:500],
        )
        session.add(row)
    else:
        if row.tenant_id != tenant_id or row.environment_id != environment_id:
            from app.tenancy.isolation import BoundaryDenied

            raise BoundaryDenied()
        row.status = status
        row.attempts = attempts
        row.last_error = last_error[:500]
    await session.flush()
    return row


async def claim_action(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    automation_id: str,
    business_event_id: str,
    action_id: str,
    job_id: uuid.UUID | None = None,
) -> tuple[AutomationActionReceipt, bool]:
    """Return ``(receipt, created)``. A duplicate key does not create a second side effect."""
    receipt = AutomationActionReceipt(
        organization_id=organization_id,
        tenant_id=tenant_id,
        environment_id=environment_id,
        automation_id=automation_id,
        business_event_id=business_event_id,
        action_id=action_id,
        status="started",
        job_id=job_id,
    )
    try:
        async with session.begin_nested():
            session.add(receipt)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(AutomationActionReceipt).where(
                    AutomationActionReceipt.tenant_id == tenant_id,
                    AutomationActionReceipt.automation_id == automation_id,
                    AutomationActionReceipt.business_event_id == business_event_id,
                    AutomationActionReceipt.action_id == action_id,
                )
            )
        ).scalar_one()
        return found, False
    return receipt, True


async def finish_action(
    session: AsyncSession, receipt: AutomationActionReceipt, *, status: str
) -> None:
    receipt.status = status
    await session.flush()
