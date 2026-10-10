"""Self-service Twilio & multi-carrier phone-number provisioning and agent binding.

Supports both:
- Functional number lifecycle helpers (`get_owned`, `list_owned`, `search`,
  `provision`, `assign`, `release`, `capabilities_of`) used by
  `/api/phone-numbers` and callback reconciliation.
- Service-based provisioning (`NumberProvisioningService`,
  `find_tenant_for_called_number`, `get_provisioning_service`) and number-to-agent
  binding (`inbound_agent_id`, `inbound_agent_version`, `outbound_agent_id`)
  for Sub-Phase 2E.
"""

from __future__ import annotations

import asyncio
import enum
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.billing.entitlements import check_metric, load_context
from app.core.config import settings
from app.core.logging import log
from app.db.models import Agent, AgentVersion, Base, Tenant, UsageMetric
from app.telephony.phone import InvalidPhoneNumber, normalize
from app.telephony.provider_errors import (
    ProviderConflictError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.tenancy.isolation import BoundaryDenied, NotFound

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

ACTIVE = frozenset({"available", "reserved", "provisioned", "assigned", "active", "provisioning"})
STATUSES = ACTIVE | {"released", "release_failed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NumberProvisioningError(RuntimeError):
    """Domain error with a stable ``code`` suitable for HTTP mapping."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PhoneNumberStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    AVAILABLE = "available"
    RESERVED = "reserved"
    PROVISIONED = "provisioned"
    ASSIGNED = "assigned"
    RELEASE_FAILED = "release_failed"
    RELEASED = "released"


class PhoneNumberType(str, enum.Enum):
    LOCAL = "local"
    TOLL_FREE = "toll_free"
    MOBILE = "mobile"


class PhoneNumber(Base):
    """A phone number owned by a tenant (`phone_numbers` table)."""

    __tablename__ = "phone_numbers"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_phone_numbers_active"),
        CheckConstraint(
            "status IN ('available', 'reserved', 'provisioned', 'assigned', 'active', 'provisioning', 'release_failed', 'released')",
            name="ck_phone_numbers_status",
        ),
        CheckConstraint(
            "provider IN ('twilio', 'telnyx', 'vonage', 'sip')",
            name="ck_phone_numbers_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(20), default="twilio", nullable=False)
    e164: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    provider_sid: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    friendly_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country_code: Mapped[str] = mapped_column(String(8), default="US", nullable=False)
    country: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    region: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    number_type: Mapped[str] = mapped_column(
        String(24),
        default=PhoneNumberType.LOCAL.value,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        default=PhoneNumberStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    active_key: Mapped[str | None] = mapped_column(String(48), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=lambda: {"voice": True, "sms": True, "mms": False}, nullable=False
    )
    capability_source: Mapped[str] = mapped_column(
        String(32), default="unconfirmed", nullable=False
    )
    assigned_use: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Number-to-Agent binding columns (Sub-Phase 2E)
    inbound_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    inbound_agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outbound_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    voice_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status_callback_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sms_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    provisioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "provider": self.provider,
            "e164": self.e164,
            "external_id": self.external_id or self.provider_sid,
            "provider_sid": self.provider_sid or self.external_id,
            "friendly_name": self.friendly_name or self.e164,
            "status": (
                self.status.value
                if isinstance(self.status, enum.Enum)
                else str(self.status)
            ),
            "country": self.country or self.country_code,
            "country_code": self.country_code or self.country or "US",
            "region": self.region,
            "capabilities": dict(self.capabilities or {}),
            "capability_source": self.capability_source,
            "assigned_use": self.assigned_use,
            "inbound_agent_id": str(self.inbound_agent_id) if self.inbound_agent_id else None,
            "inbound_agent_version": self.inbound_agent_version,
            "outbound_agent_id": str(self.outbound_agent_id) if self.outbound_agent_id else None,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "released_at": self.released_at.isoformat() if self.released_at else None,
        }


def _active_key(provider: str, e164: str) -> str:
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
    row.provider_sid = purchased.external_id[:80]
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


# ===========================================================================
# Provider abstraction & NumberProvisioningService
# ===========================================================================


@dataclass(frozen=True)
class AvailableNumber:
    e164: str
    friendly_name: str
    country_code: str
    number_type: PhoneNumberType
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    capabilities: dict[str, bool] = field(
        default_factory=lambda: {"voice": True, "sms": True, "mms": False}
    )


@dataclass(frozen=True)
class PurchasedNumber:
    provider_sid: str
    e164: str
    friendly_name: str
    capabilities: dict[str, bool]


@dataclass(frozen=True)
class WebhookBundle:
    voice_url: str
    status_callback_url: str
    sms_url: str

    @classmethod
    def for_base_url(cls, base_url: str) -> "WebhookBundle":
        base = base_url.rstrip("/")
        return cls(
            voice_url=f"{base}/telephony/voice",
            status_callback_url=f"{base}/telephony/status",
            sms_url=f"{base}/channels/sms",
        )


class NumberProvider(Protocol):
    name: str

    async def search_available(
        self,
        *,
        country_code: str = "US",
        number_type: PhoneNumberType = PhoneNumberType.LOCAL,
        area_code: str | None = None,
        contains: str | None = None,
        sms_enabled: bool = True,
        voice_enabled: bool = True,
        limit: int = 10,
    ) -> list[AvailableNumber]: ...

    async def purchase(
        self,
        *,
        e164: str,
        friendly_name: str,
        webhooks: WebhookBundle,
    ) -> PurchasedNumber: ...

    async def fetch_existing(self, *, provider_sid: str) -> PurchasedNumber: ...

    async def update_webhooks(
        self,
        *,
        provider_sid: str,
        webhooks: WebhookBundle,
        friendly_name: str | None = None,
    ) -> None: ...

    async def release(self, *, provider_sid: str) -> None: ...


class TwilioNumberProvider:
    """Real Twilio IncomingPhoneNumbers / AvailablePhoneNumbers driver."""

    name = "twilio"

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        self._account_sid = account_sid or settings.twilio_account_sid
        self._auth_token = auth_token or settings.twilio_auth_token

    def _client(self):
        if not (self._account_sid and self._auth_token):
            raise NumberProvisioningError(
                "twilio_not_configured",
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set to provision numbers.",
                status_code=503,
            )
        from twilio.rest import Client

        return Client(self._account_sid, self._auth_token)

    async def search_available(
        self,
        *,
        country_code: str = "US",
        number_type: PhoneNumberType = PhoneNumberType.LOCAL,
        area_code: str | None = None,
        contains: str | None = None,
        sms_enabled: bool = True,
        voice_enabled: bool = True,
        limit: int = 10,
    ) -> list[AvailableNumber]:
        def _run() -> list[AvailableNumber]:
            client = self._client()
            country = client.available_phone_numbers(country_code.upper())
            sublist = {
                PhoneNumberType.LOCAL: country.local,
                PhoneNumberType.TOLL_FREE: country.toll_free,
                PhoneNumberType.MOBILE: country.mobile,
            }[number_type]
            kwargs: dict[str, Any] = {
                "sms_enabled": sms_enabled,
                "voice_enabled": voice_enabled,
                "limit": max(1, min(limit, 30)),
            }
            if area_code:
                kwargs["area_code"] = area_code
            if contains:
                kwargs["contains"] = contains
            items = sublist.list(**kwargs)
            out: list[AvailableNumber] = []
            for it in items:
                caps = getattr(it, "capabilities", None) or {}
                out.append(
                    AvailableNumber(
                        e164=it.phone_number,
                        friendly_name=getattr(it, "friendly_name", it.phone_number),
                        country_code=country_code.upper(),
                        number_type=number_type,
                        locality=getattr(it, "locality", None),
                        region=getattr(it, "region", None),
                        postal_code=getattr(it, "postal_code", None),
                        capabilities={
                            "voice": bool(caps.get("voice", True)),
                            "sms": bool(caps.get("SMS", caps.get("sms", True))),
                            "mms": bool(caps.get("MMS", caps.get("mms", False))),
                        },
                    )
                )
            return out

        return await asyncio.to_thread(_run)

    async def purchase(
        self,
        *,
        e164: str,
        friendly_name: str,
        webhooks: WebhookBundle,
    ) -> PurchasedNumber:
        def _run() -> PurchasedNumber:
            client = self._client()
            rec = client.incoming_phone_numbers.create(
                phone_number=e164,
                friendly_name=friendly_name[:64],
                voice_url=webhooks.voice_url,
                voice_method="POST",
                status_callback=webhooks.status_callback_url,
                status_callback_method="POST",
                sms_url=webhooks.sms_url,
                sms_method="POST",
            )
            caps = getattr(rec, "capabilities", None) or {}
            return PurchasedNumber(
                provider_sid=rec.sid,
                e164=rec.phone_number,
                friendly_name=getattr(rec, "friendly_name", friendly_name),
                capabilities={
                    "voice": bool(caps.get("voice", True)),
                    "sms": bool(caps.get("sms", caps.get("SMS", True))),
                    "mms": bool(caps.get("mms", caps.get("MMS", False))),
                },
            )

        return await asyncio.to_thread(_run)

    async def fetch_existing(self, *, provider_sid: str) -> PurchasedNumber:
        def _run() -> PurchasedNumber:
            client = self._client()
            rec = client.incoming_phone_numbers(provider_sid).fetch()
            caps = getattr(rec, "capabilities", None) or {}
            return PurchasedNumber(
                provider_sid=rec.sid,
                e164=rec.phone_number,
                friendly_name=getattr(rec, "friendly_name", rec.phone_number),
                capabilities={
                    "voice": bool(caps.get("voice", True)),
                    "sms": bool(caps.get("sms", caps.get("SMS", True))),
                    "mms": bool(caps.get("mms", caps.get("MMS", False))),
                },
            )

        return await asyncio.to_thread(_run)

    async def update_webhooks(
        self,
        *,
        provider_sid: str,
        webhooks: WebhookBundle,
        friendly_name: str | None = None,
    ) -> None:
        def _run() -> None:
            client = self._client()
            kwargs: dict[str, Any] = {
                "voice_url": webhooks.voice_url,
                "voice_method": "POST",
                "status_callback": webhooks.status_callback_url,
                "status_callback_method": "POST",
                "sms_url": webhooks.sms_url,
                "sms_method": "POST",
            }
            if friendly_name is not None:
                kwargs["friendly_name"] = friendly_name[:64]
            client.incoming_phone_numbers(provider_sid).update(**kwargs)

        await asyncio.to_thread(_run)

    async def release(self, *, provider_sid: str) -> None:
        def _run() -> None:
            client = self._client()
            client.incoming_phone_numbers(provider_sid).delete()

        await asyncio.to_thread(_run)


class StubNumberProvider:
    """Deterministic in-memory provider for dev / CI / unit tests."""

    name = "twilio"

    def __init__(self) -> None:
        self.purchased: dict[str, dict[str, Any]] = {}
        self._counter = 1000

    async def search_available(
        self,
        *,
        country_code: str = "US",
        number_type: PhoneNumberType = PhoneNumberType.LOCAL,
        area_code: str | None = None,
        contains: str | None = None,
        sms_enabled: bool = True,
        voice_enabled: bool = True,
        limit: int = 10,
    ) -> list[AvailableNumber]:
        prefix = area_code or ("800" if number_type == PhoneNumberType.TOLL_FREE else "415")
        dial = "+1" if country_code.upper() in ("US", "CA") else "+44"
        results: list[AvailableNumber] = []
        for idx in range(max(1, min(limit, 30))):
            last4 = f"{5000 + idx:04d}"
            e164 = f"{dial}{prefix}555{last4}"
            if contains and contains not in e164:
                continue
            if any(p["e164"] == e164 for p in self.purchased.values()):
                continue
            results.append(
                AvailableNumber(
                    e164=e164,
                    friendly_name=f"({prefix}) 555-{last4}",
                    country_code=country_code.upper(),
                    number_type=number_type,
                    locality="San Francisco",
                    region="CA",
                    postal_code="94105",
                    capabilities={"voice": voice_enabled, "sms": sms_enabled, "mms": False},
                )
            )
        return results

    async def purchase(
        self,
        *,
        e164: str,
        friendly_name: str,
        webhooks: WebhookBundle,
    ) -> PurchasedNumber:
        if any(p["e164"] == e164 for p in self.purchased.values()):
            raise NumberProvisioningError(
                "number_unavailable", f"{e164} is no longer available", status_code=409
            )
        self._counter += 1
        sid = f"PNstub{self._counter:028d}"
        rec = {
            "sid": sid,
            "e164": e164,
            "friendly_name": friendly_name,
            "voice_url": webhooks.voice_url,
            "status_callback_url": webhooks.status_callback_url,
            "sms_url": webhooks.sms_url,
            "capabilities": {"voice": True, "sms": True, "mms": False},
        }
        self.purchased[sid] = rec
        return PurchasedNumber(
            provider_sid=sid,
            e164=e164,
            friendly_name=friendly_name,
            capabilities=dict(rec["capabilities"]),
        )

    async def fetch_existing(self, *, provider_sid: str) -> PurchasedNumber:
        rec = self.purchased.get(provider_sid)
        if not rec:
            raise NumberProvisioningError(
                "provider_sid_not_found",
                f"Twilio IncomingPhoneNumber {provider_sid} not found in account",
                status_code=404,
            )
        return PurchasedNumber(
            provider_sid=rec["sid"],
            e164=rec["e164"],
            friendly_name=rec["friendly_name"],
            capabilities=dict(rec["capabilities"]),
        )

    async def update_webhooks(
        self,
        *,
        provider_sid: str,
        webhooks: WebhookBundle,
        friendly_name: str | None = None,
    ) -> None:
        rec = self.purchased.get(provider_sid)
        if not rec:
            raise NumberProvisioningError(
                "provider_sid_not_found",
                f"Twilio IncomingPhoneNumber {provider_sid} not found",
                status_code=404,
            )
        rec["voice_url"] = webhooks.voice_url
        rec["status_callback_url"] = webhooks.status_callback_url
        rec["sms_url"] = webhooks.sms_url
        if friendly_name is not None:
            rec["friendly_name"] = friendly_name

    async def release(self, *, provider_sid: str) -> None:
        if provider_sid not in self.purchased:
            raise NumberProvisioningError(
                "provider_sid_not_found",
                f"Twilio IncomingPhoneNumber {provider_sid} not found",
                status_code=404,
            )
        self.purchased.pop(provider_sid, None)


class NumberProvisioningService:
    def __init__(
        self,
        provider: NumberProvider | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        if provider is None:
            if settings.twilio_account_sid and settings.twilio_auth_token:
                provider = TwilioNumberProvider()
            else:
                provider = StubNumberProvider()
        self.provider: NumberProvider = provider
        self._base_url = base_url

    def _webhooks(self) -> WebhookBundle:
        return WebhookBundle.for_base_url(self._base_url or settings.public_base_url)

    async def search_available(
        self,
        *,
        country_code: str = "US",
        number_type: PhoneNumberType = PhoneNumberType.LOCAL,
        area_code: str | None = None,
        contains: str | None = None,
        sms_enabled: bool = True,
        voice_enabled: bool = True,
        limit: int = 10,
    ) -> list[AvailableNumber]:
        if len(country_code) != 2 or not country_code.isalpha():
            raise NumberProvisioningError("invalid_country", "country_code must be ISO-3166 alpha-2")
        if area_code and (not area_code.isdigit() or not (2 <= len(area_code) <= 5)):
            raise NumberProvisioningError("invalid_area_code", "area_code must be 2–5 digits")
        return await self.provider.search_available(
            country_code=country_code.upper(),
            number_type=number_type,
            area_code=area_code,
            contains=contains,
            sms_enabled=sms_enabled,
            voice_enabled=voice_enabled,
            limit=limit,
        )

    async def list_for_tenant(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        include_released: bool = False,
        environment_id: uuid.UUID | None = None,
    ) -> list[PhoneNumber]:
        stmt = select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id)
        if environment_id is not None:
            stmt = stmt.where(PhoneNumber.environment_id == environment_id)
        if not include_released:
            stmt = stmt.where(PhoneNumber.status != PhoneNumberStatus.RELEASED.value)
        stmt = stmt.order_by(PhoneNumber.is_primary.desc(), PhoneNumber.provisioned_at.asc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def provision(
        self,
        session: AsyncSession,
        *,
        tenant: Tenant,
        e164: str,
        country_code: str = "US",
        number_type: PhoneNumberType = PhoneNumberType.LOCAL,
        friendly_name: str | None = None,
        make_primary: bool | None = None,
    ) -> PhoneNumber:
        e164 = e164.strip()
        if not E164_RE.match(e164):
            raise NumberProvisioningError("invalid_e164", f"'{e164}' is not a valid E.164 number")

        dup = await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.e164 == e164,
                PhoneNumber.status == PhoneNumberStatus.ACTIVE.value,
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise NumberProvisioningError(
                "number_already_assigned",
                f"{e164} is already assigned to a tenant",
                status_code=409,
            )

        existing_active = await self.list_for_tenant(session, tenant_id=tenant.id)
        should_be_primary = (len(existing_active) == 0) if make_primary is None else bool(make_primary)

        webhooks = self._webhooks()
        label = friendly_name or f"VoxDesk - {tenant.name}"
        purchased = await self.provider.purchase(
            e164=e164, friendly_name=label, webhooks=webhooks
        )

        if should_be_primary and existing_active:
            await session.execute(
                update(PhoneNumber)
                .where(
                    PhoneNumber.tenant_id == tenant.id,
                    PhoneNumber.status == PhoneNumberStatus.ACTIVE.value,
                )
                .values(is_primary=False)
            )

        record = PhoneNumber(
            tenant_id=tenant.id,
            e164=purchased.e164,
            friendly_name=friendly_name or purchased.friendly_name,
            country_code=country_code.upper(),
            country=country_code.upper(),
            number_type=number_type.value if isinstance(number_type, PhoneNumberType) else str(number_type),
            provider=self.provider.name,
            provider_sid=purchased.provider_sid,
            external_id=purchased.provider_sid,
            active_key=purchased.e164,
            capabilities=purchased.capabilities,
            is_primary=should_be_primary,
            status=PhoneNumberStatus.ACTIVE.value,
            voice_url=webhooks.voice_url,
            status_callback_url=webhooks.status_callback_url,
            sms_url=webhooks.sms_url,
        )
        session.add(record)
        if should_be_primary:
            tenant.twilio_number = purchased.e164
        await session.flush()

        log.info(
            "telephony.number.provisioned",
            tenant_id=str(tenant.id),
            e164=record.e164,
            provider_sid=record.provider_sid,
            is_primary=record.is_primary,
        )
        return record

    async def import_existing(
        self,
        session: AsyncSession,
        *,
        tenant: Tenant,
        provider_sid: str,
        country_code: str = "US",
        number_type: PhoneNumberType = PhoneNumberType.LOCAL,
        friendly_name: str | None = None,
        reconfigure_webhooks: bool = True,
        make_primary: bool | None = None,
    ) -> PhoneNumber:
        dup = await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.provider == self.provider.name,
                PhoneNumber.provider_sid == provider_sid,
            )
        )
        existing_row = dup.scalar_one_or_none()
        if existing_row is not None and existing_row.status == PhoneNumberStatus.ACTIVE.value:
            raise NumberProvisioningError(
                "number_already_imported",
                f"Provider SID {provider_sid} is already attached to a tenant",
                status_code=409,
            )

        fetched = await self.provider.fetch_existing(provider_sid=provider_sid)
        webhooks = self._webhooks()
        if reconfigure_webhooks:
            await self.provider.update_webhooks(
                provider_sid=provider_sid,
                webhooks=webhooks,
                friendly_name=friendly_name or f"VoxDesk - {tenant.name}",
            )

        existing_active = await self.list_for_tenant(session, tenant_id=tenant.id)
        should_be_primary = (len(existing_active) == 0) if make_primary is None else bool(make_primary)
        if should_be_primary and existing_active:
            await session.execute(
                update(PhoneNumber)
                .where(
                    PhoneNumber.tenant_id == tenant.id,
                    PhoneNumber.status == PhoneNumberStatus.ACTIVE.value,
                )
                .values(is_primary=False)
            )

        record = PhoneNumber(
            tenant_id=tenant.id,
            e164=fetched.e164,
            friendly_name=friendly_name or fetched.friendly_name,
            country_code=country_code.upper(),
            country=country_code.upper(),
            number_type=number_type.value if isinstance(number_type, PhoneNumberType) else str(number_type),
            provider=self.provider.name,
            provider_sid=fetched.provider_sid,
            external_id=fetched.provider_sid,
            active_key=fetched.e164,
            capabilities=fetched.capabilities,
            is_primary=should_be_primary,
            status=PhoneNumberStatus.ACTIVE.value,
            voice_url=webhooks.voice_url if reconfigure_webhooks else None,
            status_callback_url=webhooks.status_callback_url if reconfigure_webhooks else None,
            sms_url=webhooks.sms_url if reconfigure_webhooks else None,
        )
        session.add(record)
        if should_be_primary:
            tenant.twilio_number = fetched.e164
        await session.flush()
        return record

    async def set_primary(
        self,
        session: AsyncSession,
        *,
        tenant: Tenant,
        phone_number_id: uuid.UUID,
        environment_id: uuid.UUID | None = None,
    ) -> PhoneNumber:
        record = await self._load_owned(
            session, tenant_id=tenant.id, phone_number_id=phone_number_id,
            environment_id=environment_id,
        )
        if record.status == PhoneNumberStatus.RELEASED.value:
            raise NumberProvisioningError(
                "number_not_active", "Cannot promote a released number to primary"
            )
        await session.execute(
            update(PhoneNumber)
            .where(
                PhoneNumber.tenant_id == tenant.id,
                PhoneNumber.status != PhoneNumberStatus.RELEASED.value,
            )
            .values(is_primary=False)
        )
        record.is_primary = True
        tenant.twilio_number = record.e164
        await session.flush()
        return record

    async def reconfigure_webhooks(
        self,
        session: AsyncSession,
        *,
        tenant: Tenant,
        phone_number_id: uuid.UUID,
        environment_id: uuid.UUID | None = None,
    ) -> PhoneNumber:
        record = await self._load_owned(
            session, tenant_id=tenant.id, phone_number_id=phone_number_id,
            environment_id=environment_id,
        )
        if record.status == PhoneNumberStatus.RELEASED.value:
            raise NumberProvisioningError(
                "number_not_active", "Cannot reconfigure webhooks on a released number"
            )
        webhooks = self._webhooks()
        await self.provider.update_webhooks(
            provider_sid=record.provider_sid,
            webhooks=webhooks,
            friendly_name=record.friendly_name,
        )
        record.voice_url = webhooks.voice_url
        record.status_callback_url = webhooks.status_callback_url
        record.sms_url = webhooks.sms_url
        await session.flush()
        return record

    async def release(
        self,
        session: AsyncSession,
        *,
        tenant: Tenant,
        phone_number_id: uuid.UUID,
        environment_id: uuid.UUID | None = None,
    ) -> PhoneNumber:
        record = await self._load_owned(
            session, tenant_id=tenant.id, phone_number_id=phone_number_id,
            environment_id=environment_id,
        )
        if record.status == PhoneNumberStatus.RELEASED.value:
            return record

        await self.provider.release(provider_sid=record.provider_sid)
        was_primary = record.is_primary
        record.is_primary = False
        record.status = PhoneNumberStatus.RELEASED.value
        record.active_key = None
        record.released_at = datetime.now(timezone.utc)
        await session.flush()

        if was_primary:
            remaining = await self.list_for_tenant(session, tenant_id=tenant.id)
            if remaining:
                remaining[0].is_primary = True
                tenant.twilio_number = remaining[0].e164
            await session.flush()

        log.info(
            "telephony.number.released",
            tenant_id=str(tenant.id),
            e164=record.e164,
            provider_sid=record.provider_sid,
        )
        return record

    async def bind_agent(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        phone_number_id: uuid.UUID,
        inbound_agent_id: uuid.UUID | None | Any = ...,
        inbound_agent_version: int | None | Any = ...,
        outbound_agent_id: uuid.UUID | None | Any = ...,
        friendly_name: str | None = None,
        environment_id: uuid.UUID | None = None,
    ) -> PhoneNumber:
        """Validate tenant ownership of the number and agent(s) and update binding (2E)."""
        record = await self._load_owned(
            session,
            tenant_id=tenant_id,
            phone_number_id=phone_number_id,
            environment_id=environment_id,
        )

        if inbound_agent_id is not ...:
            if inbound_agent_id is not None:
                agent = await self._validate_tenant_agent(
                    session, tenant_id=tenant_id, agent_id=inbound_agent_id
                )
                record.inbound_agent_id = agent.id
            else:
                record.inbound_agent_id = None
                if inbound_agent_version is ...:
                    record.inbound_agent_version = None

        if inbound_agent_version is not ...:
            if inbound_agent_version is not None:
                target_agent_id = record.inbound_agent_id
                if target_agent_id is None:
                    raise NumberProvisioningError(
                        "inbound_agent_required",
                        "inbound_agent_id must be set when pinning inbound_agent_version",
                        status_code=400,
                    )
                ver_row = (
                    await session.execute(
                        select(AgentVersion).where(
                            AgentVersion.tenant_id == tenant_id,
                            AgentVersion.agent_id == target_agent_id,
                            AgentVersion.version_number == int(inbound_agent_version),
                        )
                    )
                ).scalar_one_or_none()
                if ver_row is None:
                    raise NumberProvisioningError(
                        "agent_version_not_found",
                        f"Version {inbound_agent_version} not found for agent {target_agent_id}",
                        status_code=404,
                    )
                record.inbound_agent_version = int(inbound_agent_version)
            else:
                record.inbound_agent_version = None

        if outbound_agent_id is not ...:
            if outbound_agent_id is not None:
                agent = await self._validate_tenant_agent(
                    session, tenant_id=tenant_id, agent_id=outbound_agent_id
                )
                record.outbound_agent_id = agent.id
            else:
                record.outbound_agent_id = None

        if friendly_name is not None:
            record.friendly_name = friendly_name[:120]

        await session.flush()
        return record

    async def _validate_tenant_agent(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID | str,
    ) -> Agent:
        parsed_id = uuid.UUID(str(agent_id)) if not isinstance(agent_id, uuid.UUID) else agent_id
        agent = (
            await session.execute(
                select(Agent).where(
                    Agent.id == parsed_id,
                    Agent.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if agent is None:
            raise NumberProvisioningError(
                "agent_not_found",
                f"Agent {parsed_id} does not belong to this tenant",
                status_code=404,
            )
        return agent

    async def _load_owned(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        phone_number_id: uuid.UUID,
        environment_id: uuid.UUID | None = None,
    ) -> PhoneNumber:
        stmt = select(PhoneNumber).where(
            PhoneNumber.id == phone_number_id,
            PhoneNumber.tenant_id == tenant_id,
        )
        if environment_id is not None:
            stmt = stmt.where(PhoneNumber.environment_id == environment_id)
        res = await session.execute(stmt)
        rec = res.scalar_one_or_none()
        if rec is None:
            raise NumberProvisioningError(
                "number_not_found", "Phone number not found for this tenant", status_code=404
            )
        return rec


async def find_tenant_for_called_number(
    session: AsyncSession, e164: str
) -> Tenant | None:
    """Resolve an inbound ``To`` number -> Tenant via ``phone_numbers`` first,
    falling back to ``tenants.twilio_number`` for legacy single-number tenants.
    """
    res = await session.execute(
        select(Tenant)
        .join(PhoneNumber, PhoneNumber.tenant_id == Tenant.id)
        .where(
            PhoneNumber.e164 == e164,
            PhoneNumber.status != PhoneNumberStatus.RELEASED.value,
            Tenant.is_active.is_(True),
        )
        .limit(1)
    )
    tenant = res.scalar_one_or_none()
    if tenant is not None:
        return tenant
    res2 = await session.execute(
        select(Tenant).where(Tenant.twilio_number == e164, Tenant.is_active.is_(True))
    )
    return res2.scalar_one_or_none()


_default_service: NumberProvisioningService | None = None


def get_provisioning_service() -> NumberProvisioningService:
    global _default_service
    if _default_service is None:
        _default_service = NumberProvisioningService()
    return _default_service


def _reset_for_tests(provider: NumberProvider | None = None) -> NumberProvisioningService:
    global _default_service
    _default_service = NumberProvisioningService(provider=provider or StubNumberProvider())
    return _default_service
