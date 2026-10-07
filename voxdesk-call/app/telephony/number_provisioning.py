"""Phone-number lifecycle.

Search does not persist. Reserve happens before the provider call so two
tenants cannot both hold the same active number. Release clears the active
key. Ownership is the row's tenant id, never a value from the client body.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.billing.entitlements import check_metric, load_context
from app.db.models import Base, Tenant, UsageMetric
from app.telephony.phone import InvalidPhoneNumber, normalize
from app.telephony.provider_errors import (
    ProviderConflictError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.tenancy.isolation import BoundaryDenied, NotFound

ACTIVE = frozenset({"available", "reserved", "provisioned", "assigned"})
STATUSES = ACTIVE | {"released"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_phone_numbers_active"),
        CheckConstraint(
            "status IN ('available', 'reserved', 'provisioned', 'assigned', 'released')",
            name="ck_phone_numbers_status",
        ),
        CheckConstraint(
            "provider IN ('twilio', 'telnyx', 'vonage')",
            name="ck_phone_numbers_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    e164: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    active_key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    country: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    region: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    capability_source: Mapped[str] = mapped_column(
        String(32), default="unconfirmed", nullable=False
    )
    assigned_use: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "provider": self.provider,
            "e164": self.e164,
            "external_id": self.external_id,
            "status": self.status,
            "country": self.country,
            "region": self.region,
            "capabilities": dict(self.capabilities or {}),
            "capability_source": self.capability_source,
            "assigned_use": self.assigned_use,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "released_at": self.released_at.isoformat() if self.released_at else None,
        }


def _active_key(provider: str, e164: str) -> str:
    # The provider argument is kept so callers stay explicit. The lock is the
    # number itself: one active assignment, regardless of provider.
    del provider
    return e164


async def get_owned(
    session: AsyncSession, tenant_id: uuid.UUID, number_id: uuid.UUID
) -> PhoneNumber:
    row = await session.get(PhoneNumber, number_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def list_owned(session: AsyncSession, tenant_id: uuid.UUID) -> list[PhoneNumber]:
    rows = (
        (
            await session.execute(
                select(PhoneNumber)
                .where(PhoneNumber.tenant_id == tenant_id)
                .order_by(PhoneNumber.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def search(
    session: AsyncSession, tenant: Tenant, *, country: str, provider: str, limit: int = 10
) -> list[dict]:
    from app.telephony.providers.factory import select as select_adapter

    adapter = await select_adapter(session, tenant.id, requested=provider)
    rows = await adapter.search_numbers(country=country, limit=limit)
    return [row.as_dict() for row in rows]


async def provision(
    session: AsyncSession,
    tenant: Tenant,
    *,
    e164: str,
    provider: str,
    country: str = "",
    adapter=None,
) -> PhoneNumber:
    name = (provider or "").strip().lower()
    try:
        number = normalize(e164)
    except InvalidPhoneNumber as exc:
        raise ProviderValidationError(str(exc), provider=name) from exc
    if name not in {"twilio", "telnyx", "vonage"}:
        raise ProviderValidationError("Provider is not supported", provider=name)
    await _admit(session, tenant)
    row = PhoneNumber(
        tenant_id=tenant.id,
        provider=name,
        e164=number,
        status="reserved",
        active_key=_active_key(name, number),
        country=country[:8],
        capabilities={},
        capability_source="unconfirmed",
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise ProviderConflictError("Number is already active", provider=name) from exc
    chosen = adapter
    if chosen is None:
        from app.telephony.providers.factory import select as select_adapter

        chosen = await select_adapter(session, tenant.id, requested=name)
    try:
        purchased = await chosen.provision_number(number, country=country)
    except Exception:
        row.status = "released"
        row.active_key = None
        row.released_at = _now()
        row.updated_at = _now()
        await session.flush()
        raise
    if not purchased.external_id:
        row.status = "released"
        row.active_key = None
        row.released_at = _now()
        row.updated_at = _now()
        await session.flush()
        raise ProviderValidationError("Provider did not return a number id", provider=name)
    row.external_id = purchased.external_id[:80]
    row.status = "provisioned"
    row.capabilities = purchased.capabilities.as_dict()
    row.capability_source = purchased.capabilities.source
    row.country = (purchased.country or country)[:8]
    row.region = purchased.region[:32]
    row.updated_at = _now()
    await session.flush()
    return row


async def assign(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    number_id: uuid.UUID,
    *,
    use: str,
) -> PhoneNumber:
    row = await get_owned(session, tenant_id, number_id)
    if row.status == "released":
        raise ProviderConflictError("Released number cannot be assigned", provider=row.provider)
    wanted = (use or "voice").strip().lower()
    if wanted not in {"voice", "sms", "mms", "whatsapp", "recording"}:
        raise ProviderValidationError("Unknown number use", provider=row.provider)
    if not bool((row.capabilities or {}).get(wanted)):
        raise UnsupportedCapability(
            f"{wanted} is not confirmed for this number", provider=row.provider
        )
    if row.status == "assigned" and row.assigned_use and row.assigned_use != wanted:
        raise ProviderConflictError(
            "Release the number before changing its assignment", provider=row.provider
        )
    row.status = "assigned"
    row.assigned_use = wanted[:32]
    row.assigned_at = _now()
    row.updated_at = row.assigned_at
    await session.flush()
    return row


async def release(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    number_id: uuid.UUID,
    *,
    adapter=None,
) -> PhoneNumber:
    row = await get_owned(session, tenant_id, number_id)
    if row.status == "released":
        return row
    if row.tenant_id != tenant_id:
        raise BoundaryDenied()
    chosen = adapter
    if chosen is None and row.external_id:
        from app.telephony.providers.factory import select as select_adapter

        chosen = await select_adapter(session, tenant_id, requested=row.provider)
    if chosen is not None and row.external_id:
        await chosen.release_number(row.external_id)
    row.status = "released"
    row.active_key = None
    row.released_at = _now()
    row.updated_at = row.released_at
    await session.flush()
    return row


def capabilities_of(row: PhoneNumber) -> dict:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "source": row.capability_source,
        "capabilities": dict(row.capabilities or {}),
    }


async def _admit(session: AsyncSession, tenant: Tenant) -> None:
    context = await load_context(session, tenant)
    decision = await check_metric(session, context, UsageMetric.VOICE_MINUTE, additional=0)
    if decision.decision.value == "deny":
        raise ProviderValidationError(
            "Billing does not allow another telephony purchase",
            provider="billing",
        )
