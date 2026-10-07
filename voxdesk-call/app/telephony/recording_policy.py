"""Recording enablement and retention.

The numbers come from the tenant row and ``settings.call_retention_days``.
They are configuration, not a statement of what the law requires. A legal
hold is a local flag that blocks deletion. It is not a legal-hold product.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.data_policy import retention_cutoff
from app.db.models import Base
from app.tenancy.isolation import NotFound, ValidationFailed

_MAX_DAYS = 3650


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecordingPolicy(Base):
    __tablename__ = "recording_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_scope", name="uq_recording_policies_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_scope: Mapped[str] = mapped_column(String(64), default="tenant", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_scope": self.environment_scope,
            "enabled": self.enabled,
            "retention_days": self.retention_days,
            "legal_hold": self.legal_hold,
            "legal_advice": False,
        }


async def effective(session, tenant, *, environment_id: uuid.UUID | None = None) -> dict:
    scope = str(environment_id) if environment_id else "tenant"
    row = await _row(session, tenant.id, scope)
    if row is None and scope != "tenant":
        row = await _row(session, tenant.id, "tenant")
    enabled = tenant.record_calls if row is None else row.enabled
    days = (
        settings.call_retention_days
        if row is None or row.retention_days is None
        else row.retention_days
    )
    hold = False if row is None else row.legal_hold
    return {
        "enabled": bool(enabled),
        "retention_days": int(days),
        "legal_hold": bool(hold),
        "environment_scope": scope if row is None else row.environment_scope,
        "source": "tenant_default" if row is None else "policy",
        "legal_advice": False,
    }


async def save(
    session,
    tenant,
    *,
    enabled: bool,
    retention_days: int | None,
    legal_hold: bool = False,
    environment_id: uuid.UUID | None = None,
) -> RecordingPolicy:
    if retention_days is not None and (retention_days < 1 or retention_days > _MAX_DAYS):
        raise ValidationFailed("Retention days must be between 1 and 3650")
    scope = str(environment_id) if environment_id else "tenant"
    row = await _row(session, tenant.id, scope)
    if row is None:
        row = RecordingPolicy(tenant_id=tenant.id, environment_scope=scope)
        session.add(row)
    row.enabled = enabled
    row.retention_days = retention_days
    row.legal_hold = legal_hold
    row.updated_at = _now()
    await session.flush()
    return row


async def get_owned(session, tenant_id: uuid.UUID, policy_id: uuid.UUID) -> RecordingPolicy:
    row = await session.get(RecordingPolicy, policy_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


def deadline(days: int, *, now: datetime | None = None) -> datetime:
    moment = now or _now()
    return moment + timedelta(days=max(1, days))


def expired(created_at: datetime, days: int, *, now: datetime | None = None) -> bool:
    return created_at is not None and _as_utc(created_at) < retention_cutoff(days, now=now)


async def _row(session, tenant_id, scope: str) -> RecordingPolicy | None:
    return (
        await session.execute(
            select(RecordingPolicy).where(
                RecordingPolicy.tenant_id == tenant_id,
                RecordingPolicy.environment_scope == scope,
            )
        )
    ).scalar_one_or_none()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
