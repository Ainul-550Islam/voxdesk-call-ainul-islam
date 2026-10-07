"""
app/services/telephony_usage.py
Authoritative, idempotent telephony usage accounting service.
Computes call duration from real timestamps, prevents duplicate usage billing on
terminal webhook replays, and separates simulated calls from production billable calls.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.telephony_models import TelephonyCallSession, TelephonyUsageLedger
from app.telephony.idempotency import get_idempotency_lock


def compute_call_duration(
    *,
    started_at: datetime | None,
    answered_at: datetime | None,
    ended_at: datetime | None,
    provider_duration_seconds: int | None = None,
) -> tuple[int, int, int]:
    """Return `(duration_ms, billable_seconds, billable_minutes)` from real timestamps."""
    if answered_at is not None and ended_at is not None and ended_at >= answered_at:
        delta_ms = int(round((ended_at - answered_at).total_seconds() * 1000))
        duration_ms = max(0, delta_ms)
    elif provider_duration_seconds is not None and provider_duration_seconds >= 0:
        duration_ms = int(provider_duration_seconds) * 1000
    else:
        duration_ms = 0

    billable_seconds = int(math.ceil(duration_ms / 1000.0)) if duration_ms > 0 else 0
    billable_minutes = int(math.ceil(billable_seconds / 60.0)) if billable_seconds > 0 else 0
    return duration_ms, billable_seconds, billable_minutes


class TelephonyUsageService:
    """Authoritative usage accounting service for telephony call sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def finalize_call_usage(
        self,
        *,
        call_session: TelephonyCallSession,
        provider_duration_seconds: int | None = None,
    ) -> TelephonyUsageLedger:
        """Idempotently compute and persist terminal call usage. Never double-bills."""
        idempotency_key = f"telephony_usage_final_{call_session.id}"
        lock_key = f"usage:{call_session.tenant_id}:{call_session.id}"

        async with get_idempotency_lock(lock_key):
            existing = (
                await self.session.execute(
                    select(TelephonyUsageLedger).where(
                        TelephonyUsageLedger.tenant_id == call_session.tenant_id,
                        TelephonyUsageLedger.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                call_session.usage_finalized = True
                call_session.duration_ms = existing.duration_ms
                call_session.billable_seconds = existing.billable_seconds
                await self.session.flush()
                return existing

            duration_ms, billable_seconds, billable_minutes = compute_call_duration(
                started_at=call_session.started_at,
                answered_at=call_session.answered_at,
                ended_at=call_session.ended_at,
                provider_duration_seconds=provider_duration_seconds,
            )

            # Simulated calls are tracked for audit/telemetry but marked non-billable
            effective_billable_seconds = 0 if call_session.is_simulation else billable_seconds
            effective_billable_minutes = 0 if call_session.is_simulation else billable_minutes

            call_session.duration_ms = duration_ms
            call_session.billable_seconds = effective_billable_seconds
            call_session.usage_finalized = True

            ledger_row = TelephonyUsageLedger(
                id=uuid.uuid4(),
                tenant_id=call_session.tenant_id,
                environment_id=call_session.environment_id,
                call_session_id=call_session.id,
                idempotency_key=idempotency_key,
                event_stage="CALL_FINALIZED",
                direction=call_session.direction,
                provider=call_session.provider,
                is_simulation=bool(call_session.is_simulation),
                execution_kind=call_session.execution_kind,
                duration_ms=duration_ms,
                billable_seconds=effective_billable_seconds,
                billable_minutes=effective_billable_minutes,
                metadata_json={
                    "provider_call_id": call_session.provider_call_id,
                    "agent_id": call_session.agent_id,
                    "status": call_session.status,
                    "raw_elapsed_seconds": billable_seconds,
                    "billable": not bool(call_session.is_simulation),
                },
                recorded_at=datetime.utcnow(),
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(ledger_row)
                    await self.session.flush()
                return ledger_row
            except IntegrityError:
                existing = (
                    await self.session.execute(
                        select(TelephonyUsageLedger).where(
                            TelephonyUsageLedger.tenant_id == call_session.tenant_id,
                            TelephonyUsageLedger.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one()
                return existing

    async def get_tenant_usage_summary(
        self,
        *,
        tenant_id: UUID,
        include_simulations: bool = False,
    ) -> dict[str, Any]:
        stmt = select(TelephonyUsageLedger).where(
            TelephonyUsageLedger.tenant_id == tenant_id
        )
        if not include_simulations:
            stmt = stmt.where(TelephonyUsageLedger.is_simulation.is_(False))
        rows = (await self.session.execute(stmt)).scalars().all()
        return {
            "organization_id": str(tenant_id),
            "call_count": len(rows),
            "total_duration_ms": sum(int(r.duration_ms or 0) for r in rows),
            "total_billable_seconds": sum(int(r.billable_seconds or 0) for r in rows),
            "total_billable_minutes": sum(int(r.billable_minutes or 0) for r in rows),
        }
