"""Provider-sourced Number Trust Profiles (STIR/SHAKEN, Branded Caller ID / CNAM, Spam Status, A2P 10DLC).

Closes Retell parity #20 (Branded caller ID / verified numbers):
- ``NumberTrustProfile`` rows are populated ONLY from verified carrier/provider
  API responses (Twilio Trust Hub / Voice Integrity / Lookup v2 CNAM, Telnyx
  Number Lookup / Branded Calling / 10DLC).
- User or operator request payloads can NEVER set ``shaken_attestation``,
  ``branded_caller_name``, ``spam_status``, or ``a2p_registration``.
- When provider credentials are not configured, refresh fails closed with
  ``NumberTrustNotConfiguredError`` (``code = "NOT_CONFIGURED"``, HTTP 501).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.db.models import Base
from app.db.telephony_models import TelephonyPhoneNumber
from app.telephony.number_provisioning import PhoneNumber

VALID_ATTESTATIONS = frozenset({"A", "B", "C", "unverified"})
VALID_SPAM_STATUSES = frozenset({"clean", "flagged", "likely_spam", "unknown"})
VALID_A2P_STATUSES = frozenset(
    {"approved", "pending", "rejected", "unregistered", "not_applicable"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NumberTrustError(RuntimeError):
    """Domain error for number-trust operations."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class NumberTrustNotConfiguredError(NumberTrustError):
    """Raised when the carrier trust API is not configured."""

    def __init__(
        self,
        provider: str,
        message: str | None = None,
    ) -> None:
        super().__init__(
            "NOT_CONFIGURED",
            message
            or f"Number trust lookup for provider '{provider}' is not configured with live credentials.",
            status_code=501,
        )
        self.provider = provider


@dataclass(frozen=True)
class ProviderTrustSnapshot:
    """Normalized trust attributes parsed strictly from a carrier API response."""

    provider: str
    e164: str
    shaken_attestation: str | None
    branded_caller_name: str | None
    spam_status: str
    a2p_registration: str
    provider_bundle_sid: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=_now)
    _from_provider_api: bool = field(default=True, repr=False)


class NumberTrustProfile(Base):
    """Persisted trust profile for a tenant phone number (`number_trust_profiles` table)."""

    __tablename__ = "number_trust_profiles"
    __table_args__ = (
        CheckConstraint(
            "shaken_attestation IS NULL OR shaken_attestation IN ('A', 'B', 'C', 'unverified')",
            name="ck_number_trust_profiles_attestation",
        ),
        CheckConstraint(
            "spam_status IN ('clean', 'flagged', 'likely_spam', 'unknown')",
            name="ck_number_trust_profiles_spam_status",
        ),
        CheckConstraint(
            "a2p_registration IN ('approved', 'pending', 'rejected', 'unregistered', 'not_applicable')",
            name="ck_number_trust_profiles_a2p",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone_number_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    e164: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="twilio")
    shaken_attestation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    branded_caller_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    spam_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    a2p_registration: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unregistered"
    )
    provider_bundle_sid: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    @property
    def cnam(self) -> str | None:
        return self.branded_caller_name

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "phone_number_id": str(self.phone_number_id),
            "e164": self.e164,
            "provider": self.provider,
            "shaken_attestation": self.shaken_attestation,
            "branded_caller_name": self.branded_caller_name,
            "cnam": self.branded_caller_name,
            "spam_status": self.spam_status,
            "a2p_registration": self.a2p_registration,
            "provider_bundle_sid": self.provider_bundle_sid,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "source": "provider_api" if self.last_checked else "unverified",
        }


class NumberTrustProviderClient(Protocol):
    """Protocol for carrier Trust Hub / Number Lookup adapters."""

    provider_name: str

    def is_configured(self) -> bool: ...

    async def fetch_trust_snapshot(
        self, e164: str, *, provider_sid: str | None = None
    ) -> ProviderTrustSnapshot: ...


def normalize_twilio_trust_response(
    e164: str, payload: dict[str, Any]
) -> ProviderTrustSnapshot:
    """Map a Twilio Trust Hub + Lookup v2 response dictionary into ``ProviderTrustSnapshot``."""
    raw_att = str(
        payload.get("shaken_attestation")
        or payload.get("attestation_level")
        or (payload.get("voice_integrity") or {}).get("attestation_level")
        or (payload.get("stir_shaken") or {}).get("attestation")
        or ""
    ).strip().upper()
    if raw_att in {"A", "FULL", "FULL_ATTESTATION"}:
        attestation: str | None = "A"
    elif raw_att in {"B", "PARTIAL", "PARTIAL_ATTESTATION"}:
        attestation = "B"
    elif raw_att in {"C", "GATEWAY", "GATEWAY_ATTESTATION"}:
        attestation = "C"
    elif raw_att in {"UNVERIFIED", "NONE"}:
        attestation = "unverified"
    else:
        attestation = None

    caller_name_obj = payload.get("caller_name")
    if isinstance(caller_name_obj, dict):
        cnam = caller_name_obj.get("caller_name")
    else:
        cnam = payload.get("branded_caller_name") or payload.get("cnam")
    cleaned_cnam = str(cnam).strip()[:128] if cnam else None

    raw_spam = str(
        payload.get("spam_status")
        or (payload.get("reputation") or {}).get("spam_risk")
        or "unknown"
    ).strip().lower()
    if raw_spam in {"clean", "low", "none", " benign"}:
        spam_status = "clean"
    elif raw_spam in {"flagged", "medium"}:
        spam_status = "flagged"
    elif raw_spam in {"likely_spam", "high", "spam", "blocked"}:
        spam_status = "likely_spam"
    else:
        spam_status = "unknown"

    raw_a2p = str(
        payload.get("a2p_registration")
        or (payload.get("a2p_10dlc") or {}).get("status")
        or (payload.get("campaign_registration") or {}).get("status")
        or "unregistered"
    ).strip().lower()
    if raw_a2p in {"approved", "verified", "registered", "active"}:
        a2p_status = "approved"
    elif raw_a2p in {"pending", "in_progress", "in-review"}:
        a2p_status = "pending"
    elif raw_a2p in {"rejected", "failed", "suspended"}:
        a2p_status = "rejected"
    elif raw_a2p in {"not_applicable", "toll_free_verified"}:
        a2p_status = "not_applicable"
    else:
        a2p_status = "unregistered"

    bundle_sid = (
        payload.get("bundle_sid")
        or payload.get("customer_profile_sid")
        or (payload.get("trust_product") or {}).get("sid")
    )
    return ProviderTrustSnapshot(
        provider="twilio",
        e164=e164,
        shaken_attestation=attestation,
        branded_caller_name=cleaned_cnam,
        spam_status=spam_status,
        a2p_registration=a2p_status,
        provider_bundle_sid=str(bundle_sid)[:96] if bundle_sid else None,
        raw_payload=dict(payload),
        checked_at=_now(),
    )


def normalize_telnyx_trust_response(
    e164: str, payload: dict[str, Any]
) -> ProviderTrustSnapshot:
    """Map a Telnyx Number Lookup / Verified Numbers / 10DLC response into ``ProviderTrustSnapshot``."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw_att = str(
        data.get("stir_shaken_attestation")
        or data.get("attestation_level")
        or (data.get("voice") or {}).get("stir_shaken")
        or ""
    ).strip().upper()
    if raw_att in {"A", "B", "C"}:
        attestation: str | None = raw_att
    elif raw_att in {"UNVERIFIED", "NONE"}:
        attestation = "unverified"
    else:
        attestation = None

    caller_name_obj = data.get("caller_name")
    if isinstance(caller_name_obj, dict):
        cnam = caller_name_obj.get("caller_name")
    else:
        cnam = data.get("cnam_listing_details") or data.get("branded_caller_name")
    cleaned_cnam = str(cnam).strip()[:128] if cnam else None

    raw_spam = str(data.get("spam_status") or data.get("spammers_risk") or "unknown").strip().lower()
    if raw_spam in {"clean", "low", "none"}:
        spam_status = "clean"
    elif raw_spam in {"flagged", "medium"}:
        spam_status = "flagged"
    elif raw_spam in {"likely_spam", "high", "spam"}:
        spam_status = "likely_spam"
    else:
        spam_status = "unknown"

    raw_a2p = str(
        data.get("a2p_status")
        or data.get("10dlc_status")
        or data.get("campaign_status")
        or "unregistered"
    ).strip().lower()
    if raw_a2p in {"approved", "active", "verified"}:
        a2p_status = "approved"
    elif raw_a2p in {"pending", "in_progress"}:
        a2p_status = "pending"
    elif raw_a2p in {"rejected", "failed"}:
        a2p_status = "rejected"
    else:
        a2p_status = "unregistered"

    bundle_id = data.get("verified_number_id") or data.get("messaging_profile_id")
    return ProviderTrustSnapshot(
        provider="telnyx",
        e164=e164,
        shaken_attestation=attestation,
        branded_caller_name=cleaned_cnam,
        spam_status=spam_status,
        a2p_registration=a2p_status,
        provider_bundle_sid=str(bundle_id)[:96] if bundle_id else None,
        raw_payload=dict(payload),
        checked_at=_now(),
    )


class TwilioTrustHubClient:
    """Queries Twilio Lookup v2 & Trust Hub APIs over HTTPS."""

    provider_name = "twilio"

    def __init__(
        self,
        *,
        account_sid: str | None = None,
        auth_token: str | None = None,
        lookup_base_url: str = "https://lookups.twilio.com",
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.account_sid = (account_sid if account_sid is not None else settings.twilio_account_sid or "").strip()
        self.auth_token = (auth_token if auth_token is not None else settings.twilio_auth_token or "").strip()
        self.lookup_base_url = lookup_base_url.rstrip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token)

    async def fetch_trust_snapshot(
        self, e164: str, *, provider_sid: str | None = None
    ) -> ProviderTrustSnapshot:
        if not self.is_configured():
            raise NumberTrustNotConfiguredError("twilio")
        url = f"{self.lookup_base_url}/v2/PhoneNumbers/{quote(e164, safe='+')}?Fields=caller_name,line_type_intelligence"
        try:
            validate_outbound_url(url, require_https=True)
        except OutboundUrlError as exc:
            raise NumberTrustError("SSRF_BLOCKED", str(exc), status_code=400) from exc

        try:
            if self._http_client is not None:
                resp = await self._http_client.get(
                    url, auth=(self.account_sid, self.auth_token)
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, follow_redirects=False
                ) as client:
                    resp = await client.get(
                        url, auth=(self.account_sid, self.auth_token)
                    )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise NumberTrustError(
                "PROVIDER_UNAVAILABLE",
                "Failed to contact Twilio Trust Hub / Lookup v2 endpoint",
                status_code=502,
            ) from exc

        if resp.status_code != 200:
            raise NumberTrustError(
                "PROVIDER_ERROR",
                f"Twilio Lookup v2 returned HTTP {resp.status_code}",
                status_code=502,
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise NumberTrustError(
                "PROVIDER_ERROR", "Twilio returned invalid JSON", status_code=502
            )
        return normalize_twilio_trust_response(e164, payload)


class TelnyxTrustClient:
    """Queries Telnyx Number Lookup & Branded Calling APIs over HTTPS."""

    provider_name = "telnyx"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.telnyx.com",
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else getattr(settings, "telnyx_api_key", "") or ""
        ).strip()
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def fetch_trust_snapshot(
        self, e164: str, *, provider_sid: str | None = None
    ) -> ProviderTrustSnapshot:
        if not self.is_configured():
            raise NumberTrustNotConfiguredError("telnyx")
        url = f"{self.base_url}/v2/number_lookup/{quote(e164, safe='+')}"
        try:
            validate_outbound_url(url, require_https=True)
        except OutboundUrlError as exc:
            raise NumberTrustError("SSRF_BLOCKED", str(exc), status_code=400) from exc

        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            if self._http_client is not None:
                resp = await self._http_client.get(url, headers=headers)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, follow_redirects=False
                ) as client:
                    resp = await client.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise NumberTrustError(
                "PROVIDER_UNAVAILABLE",
                "Failed to contact Telnyx Number Lookup endpoint",
                status_code=502,
            ) from exc

        if resp.status_code != 200:
            raise NumberTrustError(
                "PROVIDER_ERROR",
                f"Telnyx Number Lookup returned HTTP {resp.status_code}",
                status_code=502,
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise NumberTrustError(
                "PROVIDER_ERROR", "Telnyx returned invalid JSON", status_code=502
            )
        return normalize_telnyx_trust_response(e164, payload)


def build_provider_trust_client(provider: str) -> NumberTrustProviderClient:
    normalized = (provider or "twilio").strip().lower()
    if normalized == "twilio":
        return TwilioTrustHubClient()
    if normalized == "telnyx":
        return TelnyxTrustClient()
    raise NumberTrustNotConfiguredError(
        normalized,
        f"Provider '{normalized}' does not have a configured Number Trust adapter.",
    )


async def _resolve_owned_number(
    session: AsyncSession, tenant_id: uuid.UUID, number_id: uuid.UUID
) -> tuple[uuid.UUID, str, str, str | None]:
    """Return ``(id, e164, provider, provider_sid)`` for a tenant-owned number or raise LookupError."""
    legacy = await session.scalar(
        select(PhoneNumber).where(
            PhoneNumber.id == number_id, PhoneNumber.tenant_id == tenant_id
        )
    )
    if legacy is not None:
        return (
            legacy.id,
            legacy.e164,
            (legacy.provider or "twilio").lower(),
            legacy.provider_sid or legacy.external_id or None,
        )

    ent = await session.scalar(
        select(TelephonyPhoneNumber).where(
            TelephonyPhoneNumber.id == number_id,
            TelephonyPhoneNumber.tenant_id == tenant_id,
        )
    )
    if ent is not None:
        return (
            ent.id,
            ent.e164_number or ent.number,
            (ent.provider or "twilio").lower(),
            ent.provider_number_id,
        )

    raise LookupError(f"Phone number {number_id} not found for tenant {tenant_id}")


async def get_number_trust_profile(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    number_id: uuid.UUID,
) -> dict[str, Any]:
    """Return the stored trust profile for a tenant's phone number, or an unverified view."""
    resolved_id, e164, provider, _ = await _resolve_owned_number(
        session, tenant_id, number_id
    )
    row = await session.scalar(
        select(NumberTrustProfile).where(
            NumberTrustProfile.tenant_id == tenant_id,
            NumberTrustProfile.phone_number_id == resolved_id,
        )
    )
    if row is not None:
        return row.as_dict()
    return {
        "id": None,
        "tenant_id": str(tenant_id),
        "phone_number_id": str(resolved_id),
        "e164": e164,
        "provider": provider,
        "shaken_attestation": None,
        "branded_caller_name": None,
        "cnam": None,
        "spam_status": "unknown",
        "a2p_registration": "unregistered",
        "provider_bundle_sid": None,
        "last_checked": None,
        "source": "unverified",
    }


async def apply_provider_trust_snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    number_id: uuid.UUID,
    snapshot: ProviderTrustSnapshot,
) -> NumberTrustProfile:
    """Persist a verified ``ProviderTrustSnapshot`` onto ``NumberTrustProfile``."""
    if not isinstance(snapshot, ProviderTrustSnapshot) or not snapshot._from_provider_api:
        raise NumberTrustError(
            "USER_ATTESTATION_FORBIDDEN",
            "Trust profile attestation values can only be populated from provider API responses.",
            status_code=400,
        )
    if (
        snapshot.shaken_attestation is not None
        and snapshot.shaken_attestation not in VALID_ATTESTATIONS
    ):
        raise NumberTrustError(
            "INVALID_PROVIDER_ATTESTATION",
            f"Unsupported provider attestation value: {snapshot.shaken_attestation!r}",
            status_code=502,
        )
    spam_status = (
        snapshot.spam_status
        if snapshot.spam_status in VALID_SPAM_STATUSES
        else "unknown"
    )
    a2p_status = (
        snapshot.a2p_registration
        if snapshot.a2p_registration in VALID_A2P_STATUSES
        else "unregistered"
    )

    row = await session.scalar(
        select(NumberTrustProfile).where(
            NumberTrustProfile.tenant_id == tenant_id,
            NumberTrustProfile.phone_number_id == number_id,
        )
    )
    now = snapshot.checked_at or _now()
    if row is None:
        row = NumberTrustProfile(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            phone_number_id=number_id,
            e164=snapshot.e164,
            provider=snapshot.provider,
            shaken_attestation=snapshot.shaken_attestation,
            branded_caller_name=snapshot.branded_caller_name,
            spam_status=spam_status,
            a2p_registration=a2p_status,
            provider_bundle_sid=snapshot.provider_bundle_sid,
            provider_payload=dict(snapshot.raw_payload),
            last_checked=now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.e164 = snapshot.e164
        row.provider = snapshot.provider
        row.shaken_attestation = snapshot.shaken_attestation
        row.branded_caller_name = snapshot.branded_caller_name
        row.spam_status = spam_status
        row.a2p_registration = a2p_status
        row.provider_bundle_sid = snapshot.provider_bundle_sid
        row.provider_payload = dict(snapshot.raw_payload)
        row.last_checked = now
        row.updated_at = now

    await session.flush()
    return row


async def refresh_number_trust_profile(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    number_id: uuid.UUID,
    provider_client: NumberTrustProviderClient | None = None,
) -> NumberTrustProfile:
    """Query the carrier API for a tenant's number and update ``NumberTrustProfile``."""
    resolved_id, e164, provider, provider_sid = await _resolve_owned_number(
        session, tenant_id, number_id
    )
    client = provider_client or build_provider_trust_client(provider)
    if not client.is_configured():
        raise NumberTrustNotConfiguredError(provider)

    snapshot = await client.fetch_trust_snapshot(e164, provider_sid=provider_sid)
    return await apply_provider_trust_snapshot(
        session,
        tenant_id=tenant_id,
        number_id=resolved_id,
        snapshot=snapshot,
    )


async def scheduled_refresh_number_trust_profiles(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    provider_clients: dict[str, NumberTrustProviderClient] | None = None,
) -> dict[str, Any]:
    """Scheduled background refresh across active tenant numbers."""
    stmt = select(PhoneNumber).where(PhoneNumber.status != "released")
    if tenant_id is not None:
        stmt = stmt.where(PhoneNumber.tenant_id == tenant_id)
    numbers = list((await session.execute(stmt)).scalars().all())

    refreshed = 0
    skipped_unconfigured = 0
    errors = 0

    for num in numbers:
        prov = (num.provider or "twilio").lower()
        client = (provider_clients or {}).get(prov)
        if client is None:
            try:
                client = build_provider_trust_client(prov)
            except NumberTrustNotConfiguredError:
                skipped_unconfigured += 1
                continue
        if not client.is_configured():
            skipped_unconfigured += 1
            continue
        try:
            await refresh_number_trust_profile(
                session,
                tenant_id=num.tenant_id,
                number_id=num.id,
                provider_client=client,
            )
            refreshed += 1
        except NumberTrustNotConfiguredError:
            skipped_unconfigured += 1
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            errors += 1

    return {
        "total_numbers": len(numbers),
        "refreshed": refreshed,
        "skipped_unconfigured": skipped_unconfigured,
        "errors": errors,
    }
