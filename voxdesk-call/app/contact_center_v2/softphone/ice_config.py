"""STUN/TURN configuration with short-lived coturn REST credentials."""
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from typing import Any

from app.contact_center_v2.errors import NotConfigured
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import ContactCenterV2Settings, settings


def ice_servers(
    user_id: uuid.UUID,
    config: ContactCenterV2Settings = settings,
    *,
    production: bool | None = None,
) -> list[dict[str, Any]]:
    """Return browser ICE servers without inventing relay infrastructure.

    coturn's REST username embeds the expiry and agent identity. The credential
    is the standard base64 HMAC-SHA1 of that username using the shared secret.
    Configuring a TURN URI without its secret is an error, never a STUN-only
    success silently presented as production relay capability.
    """
    servers: list[dict[str, Any]] = []
    if config.stun_url:
        if not config.stun_url.startswith("stun:"):
            raise NotConfigured("STUN URLs must use the stun: scheme")
        servers.append({"urls": [config.stun_url]})
    turn_urls = [config.turn_udp_url, config.turn_tcp_url, config.turns_url]
    turn_urls = [item for item in turn_urls if item]
    if production is None:
        from app.core.config import settings as core_settings

        production = core_settings.is_production
    if production and not turn_urls:
        raise NotConfigured("Production WebRTC requires at least one TURN relay URI")
    if turn_urls:
        if not config.turn_static_secret:
            raise NotConfigured("TURN URLs require CC2_TURN_STATIC_SECRET")
        for item in turn_urls:
            if not item.startswith(("turn:", "turns:")):
                raise NotConfigured("TURN URLs must use turn: or turns: URIs")
        expires = int(utcnow().timestamp()) + config.turn_ttl_seconds
        username = f"{expires}:{user_id}"
        digest = hmac.new(
            config.turn_static_secret.encode("utf-8"),
            username.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        servers.append({
            "urls": turn_urls,
            "username": username,
            "credential": base64.b64encode(digest).decode("ascii"),
        })
    return servers


__all__ = ["ice_servers"]
