"""Provider selection.

The client names a provider. It never supplies a secret. An unknown name, a
disabled binding, or a missing credential is a typed error. The public view
reports whether a provider is configured, not the credential itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select as sa_select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderUnavailableError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.telephony.providers.base import TelephonyAdapter

SUPPORTED = frozenset({"twilio", "telnyx", "vonage"})
_SECRET_MARKERS = ("key", "secret", "token", "authorization", "password", "private")


class ProviderBinding(Base):
    """Which providers a tenant has enabled. No credential column."""

    __tablename__ = "telephony_provider_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_telephony_provider_bindings"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


def public_config() -> list[dict]:
    """Configured flags only. Values that look like secrets are not copied."""
    from app.core.config import settings

    rows = [
        {
            "provider": "twilio",
            "configured": bool(settings.twilio_account_sid and settings.twilio_auth_token),
        },
        {"provider": "telnyx", "configured": bool((settings.telnyx_api_key or "").strip())},
        {
            "provider": "vonage",
            "configured": bool(
                (settings.vonage_api_key or "").strip()
                and (settings.vonage_api_secret or "").strip()
            ),
        },
    ]
    for row in rows:
        for key in list(row):
            if any(marker in key for marker in _SECRET_MARKERS):
                row.pop(key, None)
    return rows


def build(name: str) -> TelephonyAdapter:
    provider = (name or "").strip().lower()
    if provider not in SUPPORTED:
        raise ProviderValidationError("Provider is not supported", provider=provider)
    if provider == "twilio":
        from app.telephony.providers.twilio import TwilioAdapter

        return TwilioAdapter()
    if provider == "telnyx":
        from app.telephony.providers.telnyx import TelnyxAdapter

        return TelnyxAdapter()
    from app.telephony.providers.vonage import VonageAdapter

    return VonageAdapter()


def carrier_media_capabilities() -> dict[str, dict]:
    """Return per-carrier media streaming and serializer capabilities (Sub-Phase 2F)."""
    from app.telephony.media.serializers import supported_media_serializers

    return supported_media_serializers()


def media_serializer_for(provider: str) -> str:
    """The media-stream serializer a carrier is served by.

    Telnyx is served by :class:`app.telephony.providers.telnyx_media.TelnyxMediaStream`
    (Batch 01), which owns the wire codec and the barge-in queue; Twilio keeps the
    runtime-configurable Twilio transport. Unknown carriers are a validation
    error rather than a silent fallback, because a wrong serializer answers a
    live caller with noise.
    """
    name = (provider or "").strip().lower()
    mapping = {
        "twilio": "TwilioMediaStream",
        "telnyx": "TelnyxMediaStream",
        "plivo": "PlivoMediaStream",
        "sip": "SIPBridgeFrameSerializer",
    }
    if name not in mapping:
        raise ProviderValidationError("Provider has no media serializer", provider=name or "unknown")
    return mapping[name]


async def resolve_media_capability(session, tenant_id: uuid.UUID, provider: str) -> dict:
    """Resolve the *effective* AI media-stream capability for one tenant.

    Three inputs, all required:

    1. the deployment credential set (``TelnyxAdapter.supports_ai_media_stream``);
    2. the tenant's provider binding being enabled (a tenant that has not opted
       into the carrier must not be routed to it);
    3. the serializer registration for that carrier.

    The returned dict always carries ``supports_ai_media_stream`` and a ``state``
    of ``READY`` or ``NOT_CONFIGURED`` plus the reason, so a caller can answer
    ``501 UNSUPPORTED_CAPABILITY`` with an explanation instead of a stack trace.
    """
    name = (provider or "").strip().lower()
    if name not in SUPPORTED:
        raise ProviderValidationError("Provider is not supported", provider=name)

    adapter = build(name)
    deployment = (
        adapter.media_stream_capability()
        if hasattr(adapter, "media_stream_capability")
        else {
            "provider": name,
            "supports_ai_media_stream": False,
            "state": "UNSUPPORTED_CAPABILITY",
            "missing": ["media_stream_adapter"],
        }
    )
    serializer = media_serializer_for(name)

    row = await binding_for(session, tenant_id, name)
    tenant_enabled = bool(row is not None and row.enabled)
    deployment_ready = bool(deployment.get("supports_ai_media_stream"))

    if deployment_ready and tenant_enabled:
        state, reason = "READY", "deployment credentials present and tenant binding enabled"
    elif not deployment_ready:
        state = "NOT_CONFIGURED"
        reason = "deployment is missing " + ", ".join(deployment.get("missing") or ["credentials"])
    elif name != "twilio" and row is None:
        state = "NOT_CONFIGURED"
        reason = f"tenant {tenant_id} has no enabled {name} provider binding"
    else:
        state = "NOT_CONFIGURED"
        reason = f"tenant {tenant_id} has disabled the {name} provider binding"

    return {
        "provider": name,
        "tenant_id": str(tenant_id),
        "supports_ai_media_stream": deployment_ready and tenant_enabled,
        "state": state,
        "reason": reason,
        "serializer": serializer,
        "deployment": deployment,
        "tenant_binding_enabled": tenant_enabled,
    }


async def require_media_capability(session, tenant_id: uuid.UUID, provider: str) -> dict:
    """``resolve_media_capability``, or ``UNSUPPORTED_CAPABILITY`` when absent."""
    capability = await resolve_media_capability(session, tenant_id, provider)
    if not capability["supports_ai_media_stream"]:
        raise UnsupportedCapability(
            f"{provider} AI media streaming is not available: {capability['reason']}",
            provider=provider,
        )
    return capability


async def binding_for(session, tenant_id: uuid.UUID, provider: str) -> ProviderBinding | None:
    return (
        await session.execute(
            sa_select(ProviderBinding).where(
                ProviderBinding.tenant_id == tenant_id,
                ProviderBinding.provider == provider,
            )
        )
    ).scalar_one_or_none()


async def enable(
    session, tenant_id: uuid.UUID, provider: str, *, enabled: bool = True
) -> ProviderBinding:
    name = (provider or "").strip().lower()
    if name not in SUPPORTED:
        raise ProviderValidationError("Provider is not supported", provider=name)
    row = await binding_for(session, tenant_id, name)
    if row is None:
        row = ProviderBinding(tenant_id=tenant_id, provider=name, enabled=enabled)
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = await binding_for(session, tenant_id, name)
            if row is None:
                raise ProviderUnavailableError("Provider binding conflict", provider=name) from None
    row.enabled = enabled
    await session.flush()
    return row


async def select(
    session,
    tenant_id: uuid.UUID,
    *,
    requested: str | None = None,
    required_capability: str | None = None,
) -> TelephonyAdapter:
    name = await _resolve_name(session, tenant_id, requested)
    adapter = build(name)
    try:
        adapter.require_configured()
    except ProviderConfigurationError:
        raise
    if required_capability and not adapter.capabilities().allows(required_capability):
        raise UnsupportedCapability(
            f"{required_capability} is not confirmed for this provider",
            provider=name,
        )
    return adapter


async def _resolve_name(session, tenant_id: uuid.UUID, requested: str | None) -> str:
    if requested is None:
        enabled = (
            (
                await session.execute(
                    sa_select(ProviderBinding).where(
                        ProviderBinding.tenant_id == tenant_id,
                        ProviderBinding.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        names = {row.provider for row in enabled}
        if "twilio" in names or not names:
            return "twilio"
        if len(names) == 1:
            return next(iter(names))
        raise ProviderValidationError("Provider must be selected", provider="")
    name = requested.strip().lower()
    if name not in SUPPORTED:
        raise ProviderValidationError("Provider is not supported", provider=name)
    row = await binding_for(session, tenant_id, name)
    if name == "twilio" and row is None:
        return name
    if row is None or not row.enabled:
        raise ProviderUnavailableError("Provider is not enabled for this tenant", provider=name)
    return name
