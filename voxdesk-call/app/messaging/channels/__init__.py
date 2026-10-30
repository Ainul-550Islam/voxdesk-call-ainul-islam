"""Messaging ``ChannelAdapter`` registry (Capability 11).

Dispatches supported ``(provider, channel_type)`` pairs:
- ``("twilio", "sms")`` -> :class:`TwilioSmsChannelAdapter`
- ``("twilio", "whatsapp")`` -> :class:`TwilioWhatsAppAdapter`
- ``("telnyx", "sms")`` -> :class:`TelnyxMessagingAdapter`
- ``("vonage", "sms")`` -> :class:`VonageSmsAdapter`

Unsupported channels (e.g. ``slack``, ``teams``, ``email``, ``webchat``) raise
:class:`ChannelUnsupportedError` so API routes reject them with HTTP 422 rather
than 501 or fake success.
"""
from __future__ import annotations

from typing import Any

from app.messaging.channels.base import (
    ChannelAdapter,
    ChannelAdapterError,
    ChannelDeliveryError,
    ChannelNotConfiguredError,
    ChannelSignatureError,
    ChannelUnsupportedError,
    TwilioSmsChannelAdapter,
    apply_inbound_keyword_dnc,
    classify_compliance_keyword,
    normalize_e164,
    resolve_tenant_for_destination,
)
from app.messaging.channels.telnyx_messaging import (
    TelnyxMessagingAdapter,
    verify_telnyx_ed25519_signature,
)
from app.messaging.channels.twilio_whatsapp import (
    TwilioWhatsAppAdapter,
    format_whatsapp_address,
)
from app.messaging.channels.vonage_sms import (
    VonageSmsAdapter,
    verify_vonage_webhook_signature,
)
from app.schemas.messaging import ChannelAdapterInfo

CHANNEL_ADAPTER_REGISTRY: dict[tuple[str, str], type[ChannelAdapter]] = {
    ("twilio", "sms"): TwilioSmsChannelAdapter,
    ("twilio", "whatsapp"): TwilioWhatsAppAdapter,
    ("telnyx", "sms"): TelnyxMessagingAdapter,
    ("vonage", "sms"): VonageSmsAdapter,
}

SUPPORTED_CHANNEL_TYPES = frozenset({"sms", "whatsapp"})
SUPPORTED_PROVIDERS = frozenset({"twilio", "telnyx", "vonage"})


def get_channel_adapter(
    provider: str,
    channel_type: str,
    *,
    transport: Any = None,
) -> ChannelAdapter:
    prov = (provider or "").strip().lower()
    chan = (channel_type or "").strip().lower()
    if chan not in SUPPORTED_CHANNEL_TYPES:
        raise ChannelUnsupportedError(
            f"Channel type '{chan or 'unspecified'}' is not supported; supported channel types are sms and whatsapp.",
            channel_type=chan,
            provider=prov,
        )
    adapter_cls = CHANNEL_ADAPTER_REGISTRY.get((prov, chan))
    if adapter_cls is None:
        supported_pairs = ", ".join(f"{p}:{c}" for p, c in sorted(CHANNEL_ADAPTER_REGISTRY))
        raise ChannelUnsupportedError(
            f"Provider '{prov or 'unspecified'}' does not support channel '{chan}'; supported pairs: {supported_pairs}.",
            channel_type=chan,
            provider=prov,
        )
    return adapter_cls(transport=transport) if transport is not None else adapter_cls()


def list_registered_channel_adapters() -> list[ChannelAdapterInfo]:
    items: list[ChannelAdapterInfo] = []
    for (prov, chan), cls in sorted(CHANNEL_ADAPTER_REGISTRY.items()):
        inst = cls()
        items.append(
            ChannelAdapterInfo(
                provider=prov,
                channel_type=chan,
                configured=inst.configured(),
                supports_templates=inst.supports_templates,
                supports_media=inst.supports_media,
                webhook_signature_scheme=inst.webhook_signature_scheme,
            )
        )
    return items


__all__ = [
    "CHANNEL_ADAPTER_REGISTRY",
    "SUPPORTED_CHANNEL_TYPES",
    "SUPPORTED_PROVIDERS",
    "ChannelAdapter",
    "ChannelAdapterError",
    "ChannelDeliveryError",
    "ChannelNotConfiguredError",
    "ChannelSignatureError",
    "ChannelUnsupportedError",
    "TelnyxMessagingAdapter",
    "TwilioSmsChannelAdapter",
    "TwilioWhatsAppAdapter",
    "VonageSmsAdapter",
    "apply_inbound_keyword_dnc",
    "classify_compliance_keyword",
    "format_whatsapp_address",
    "get_channel_adapter",
    "list_registered_channel_adapters",
    "normalize_e164",
    "resolve_tenant_for_destination",
    "verify_telnyx_ed25519_signature",
    "verify_vonage_webhook_signature",
]
