"""Shared setup for contact-center tests. Not an ACD module."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.contact_center.service import set_state
from app.db.models import Call, CallStatus, Environment


async def production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def live_call(db, tenant, environment, *, status: CallStatus = CallStatus.IN_PROGRESS) -> Call:
    row = Call(
        tenant_id=tenant.id,
        environment_id=environment.id,
        call_sid=f"CA{uuid.uuid4().hex}",
        from_number="+15551230000",
        to_number=tenant.twilio_number,
        status=status,
        started_at=datetime.now(timezone.utc),
        duration_seconds=0.0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def go_available(db, tenant, user):
    row, outcome = await set_state(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        target="available",
        actor_id=user.id,
    )
    await db.commit()
    return row, outcome
