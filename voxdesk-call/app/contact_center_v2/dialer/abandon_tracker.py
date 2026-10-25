"""Rolling 30-day abandonment ledger with a rebuildable Redis projection.

Postgres is authoritative for compliance decisions. Redis is a query projection;
a mismatch is reported and can be rebuilt, never hidden behind a success value.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.pacing import AbandonmentMeasurement
from app.contact_center_v2.repository import as_utc, utcnow
from app.db.contact_center_v2_models import AbandonEvent, DialerAttempt, DialerPolicy

WINDOW = timedelta(days=30)
AGENT_CONNECT_DEADLINE_MS = 2000


def _redis_keys(tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> tuple[str, str]:
    prefix = f"cc2:abandon:{tenant_id}:{campaign_id}"
    return prefix + ":times", prefix + ":events"


class AbandonTracker:
    def __init__(self, session: AsyncSession, redis_client: Any = None) -> None:
        self.session = session
        self.redis = redis_client
        self.last_redis_error: str | None = None

    async def _project(self, row: AbandonEvent) -> None:
        if self.redis is None:
            return
        times, events = _redis_keys(row.tenant_id, row.campaign_id)
        member = str(row.id)
        payload = json.dumps({
            "attempt_id": str(row.attempt_id), "event_type": row.event_type,
            "numerator": row.numerator, "denominator": row.denominator,
        }, separators=(",", ":"))
        cutoff = (as_utc(row.occurred_at) - WINDOW).timestamp()
        try:
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.zadd(times, {member: as_utc(row.occurred_at).timestamp()})
            pipeline.hset(events, member, payload)
            pipeline.zremrangebyscore(times, 0, cutoff)
            pipeline.expire(times, int(WINDOW.total_seconds()) + 86400)
            pipeline.expire(events, int(WINDOW.total_seconds()) + 86400)
            await pipeline.execute()
            self.last_redis_error = None
        except Exception as exc:  # Redis remains a rebuildable projection.
            self.last_redis_error = type(exc).__name__

    async def record(
        self,
        attempt: DialerAttempt,
        event_type: str,
        *,
        occurred_at: datetime,
        numerator: bool = False,
        denominator: bool = False,
        connected_latency_ms: int | None = None,
        reason: str = "",
    ) -> tuple[AbandonEvent, bool]:
        existing = (
            await self.session.execute(
                select(AbandonEvent).where(
                    AbandonEvent.attempt_id == attempt.id,
                    AbandonEvent.event_type == event_type,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        policy = await self.session.get(DialerPolicy, attempt.policy_id)
        if policy is None:
            raise LookupError("dialer policy missing for abandonment event")
        row = AbandonEvent(
            tenant_id=attempt.tenant_id,
            environment_id=attempt.environment_id,
            campaign_id=policy.campaign_id,
            policy_id=policy.id,
            attempt_id=attempt.id,
            call_id=attempt.call_id,
            event_type=event_type,
            numerator=numerator,
            denominator=denominator,
            connected_latency_ms=connected_latency_ms,
            reason=reason[:80],
            occurred_at=occurred_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self._project(row)
        return row, True

    async def reconcile_attempt(self, attempt: DialerAttempt) -> int:
        """Materialize exact ledger events from the durable attempt transition."""
        created = 0
        answered_by = (attempt.answered_by or "").lower()
        machine = answered_by.startswith("machine")
        if machine and attempt.answered_at is not None:
            created += int((await self.record(
                attempt, "machine_answered", occurred_at=as_utc(attempt.answered_at)
            ))[1])
        elif attempt.answered_at is not None:
            created += int((await self.record(
                attempt, "human_answered", occurred_at=as_utc(attempt.answered_at), denominator=True
            ))[1])
        if attempt.agent_connected_at is not None and attempt.answered_at is not None:
            latency = max(0, int((as_utc(attempt.agent_connected_at) - as_utc(attempt.answered_at)).total_seconds() * 1000))
            created += int((await self.record(
                attempt, "agent_connected", occurred_at=as_utc(attempt.agent_connected_at),
                connected_latency_ms=latency,
            ))[1])
            if latency > AGENT_CONNECT_DEADLINE_MS and not machine:
                created += int((await self.record(
                    attempt, "abandoned", occurred_at=as_utc(attempt.agent_connected_at),
                    numerator=True, connected_latency_ms=latency, reason="agent_connect_over_2s",
                ))[1])
        if attempt.abandonment_counted or attempt.outcome == "abandoned" or attempt.abandoned_at is not None:
            occurred = attempt.abandoned_at or attempt.ended_at or attempt.updated_at
            latency = None
            if attempt.answered_at is not None:
                latency = max(0, int((as_utc(occurred) - as_utc(attempt.answered_at)).total_seconds() * 1000))
            created += int((await self.record(
                attempt, "abandoned", occurred_at=as_utc(occurred), numerator=True,
                connected_latency_ms=latency, reason=attempt.failure_category,
            ))[1])
        if attempt.safe_harbor_requested_at is not None:
            created += int((await self.record(
                attempt, "safe_harbor", occurred_at=as_utc(attempt.safe_harbor_requested_at),
                reason="safe_harbor_requested",
            ))[1])
        return created

    async def rolling_counts(
        self, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID, now: datetime | None = None
    ) -> dict[str, Any]:
        moment = now or utcnow()
        start = moment - WINDOW
        denominator = await self.session.scalar(
            select(func.count(func.distinct(AbandonEvent.attempt_id))).where(
                AbandonEvent.tenant_id == tenant_id,
                AbandonEvent.campaign_id == campaign_id,
                AbandonEvent.denominator.is_(True),
                AbandonEvent.occurred_at >= start,
                AbandonEvent.occurred_at <= moment,
            )
        )
        numerator = await self.session.scalar(
            select(func.count(func.distinct(AbandonEvent.attempt_id))).where(
                AbandonEvent.tenant_id == tenant_id,
                AbandonEvent.campaign_id == campaign_id,
                AbandonEvent.numerator.is_(True),
                AbandonEvent.occurred_at >= start,
                AbandonEvent.occurred_at <= moment,
            )
        )
        db_denominator, db_numerator = int(denominator or 0), int(numerator or 0)
        redis_counts = await self._redis_counts(tenant_id, campaign_id, moment)
        return {
            "window_start": start.isoformat(),
            "human_answered": db_denominator,
            "abandoned": db_numerator,
            "rate": str(Decimal(db_numerator) / Decimal(db_denominator)) if db_denominator else "0",
            "redis": redis_counts,
            "consistent": redis_counts is None or redis_counts == {
                "human_answered": db_denominator, "abandoned": db_numerator
            },
            "redis_error": self.last_redis_error,
        }

    async def _redis_counts(
        self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, now: datetime
    ) -> dict[str, int] | None:
        if self.redis is None:
            return None
        times, events = _redis_keys(tenant_id, campaign_id)
        try:
            await self.redis.zremrangebyscore(times, 0, (now - WINDOW).timestamp())
            members = await self.redis.zrangebyscore(times, (now - WINDOW).timestamp(), now.timestamp())
            if not members:
                return {"human_answered": 0, "abandoned": 0}
            payloads = await self.redis.hmget(events, *members)
            attempts: dict[str, set[str]] = {}
            for raw in payloads:
                if raw is None:
                    return {"human_answered": -1, "abandoned": -1}
                item = json.loads(raw)
                attempt = item["attempt_id"]
                attempts.setdefault(attempt, set())
                if item["denominator"]:
                    attempts[attempt].add("denominator")
                if item["numerator"]:
                    attempts[attempt].add("numerator")
            return {
                "human_answered": sum("denominator" in flags for flags in attempts.values()),
                "abandoned": sum("numerator" in flags for flags in attempts.values()),
            }
        except Exception as exc:
            self.last_redis_error = type(exc).__name__
            return {"human_answered": -1, "abandoned": -1}

    async def rebuild_redis(self, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> int:
        if self.redis is None:
            raise RuntimeError("Redis abandonment projection is not configured")
        rows = (
            (
                await self.session.execute(
                    select(AbandonEvent).where(
                        AbandonEvent.tenant_id == tenant_id,
                        AbandonEvent.campaign_id == campaign_id,
                        AbandonEvent.occurred_at >= utcnow() - WINDOW,
                    )
                )
            )
            .scalars()
            .all()
        )
        times, events = _redis_keys(tenant_id, campaign_id)
        await self.redis.delete(times, events)
        for row in rows:
            await self._project(row)
        if self.last_redis_error:
            raise RuntimeError("Redis abandonment projection rebuild failed")
        return len(rows)

    async def measurement(
        self, policy: DialerPolicy, *, cap: Decimal, minimum_sample: int, now: datetime | None = None
    ) -> AbandonmentMeasurement:
        # Reconcile from attempts before every decision. This also upgrades
        # rows written by the pre-ledger Batch 04 implementation without ever
        # treating a Redis projection as the compliance source of truth.
        attempts = (
            (
                await self.session.execute(
                    select(DialerAttempt).where(
                        DialerAttempt.tenant_id == policy.tenant_id,
                        DialerAttempt.policy_id == policy.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for attempt in attempts:
            await self.reconcile_attempt(attempt)
        counts = await self.rolling_counts(tenant_id=policy.tenant_id, campaign_id=policy.campaign_id, now=now)
        if not counts["consistent"]:
            raise RuntimeError("Redis and Postgres abandonment ledgers are inconsistent")
        return AbandonmentMeasurement(
            window_start=datetime.fromisoformat(counts["window_start"]),
            human_answered=counts["human_answered"],
            abandoned=counts["abandoned"],
            cap=cap,
            minimum_sample=minimum_sample,
        )


__all__ = [
    "AGENT_CONNECT_DEADLINE_MS",
    "AbandonTracker",
    "WINDOW",
]
