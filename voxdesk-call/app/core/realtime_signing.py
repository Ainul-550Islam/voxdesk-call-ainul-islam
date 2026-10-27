"""Shared HMAC key for live-monitoring and contact-center realtime tickets.

Both ``app.api.ws.monitor_ws`` and ``app.contact_center_v2.realtime.websocket``
sign short-lived JWTs and must derive the *same* key. Keeping the derivation in
this dependency-free module (instead of inside the ``app.api.ws`` package)
breaks an import cycle: importing ``app.api.ws.monitor_ws`` executes
``app.api.ws.__init__``, which eagerly imports ``assist_ws``, which imports
``app.contact_center_v2.realtime``. Importing the realtime package first used to
fail collection with "cannot import name 'active_realtime_user' from partially
initialized module".
"""

from __future__ import annotations

import hashlib

from app.core.config import settings

_SECRET_SETTING_NAMES = ("media_stream_signing_secret", "jwt_secret_key", "secret_key")
_DEVELOPMENT_FALLBACK = "dev-voxdesk-monitor-ws-signing-secret-do-not-use-in-prod"


def signing_secret() -> str:
    """Resolve the configured signing key, or the development-only fallback.

    Short configured values are expanded with SHA-256 so a low-entropy secret
    never becomes a short HMAC key. Production refuses to start with no secret.
    """
    for attr in _SECRET_SETTING_NAMES:
        val = (getattr(settings, attr, None) or "").strip()
        if val:
            if len(val.encode("utf-8")) < 32:
                return hashlib.sha256(val.encode("utf-8")).hexdigest()
            return val
    if getattr(settings, "environment", "development").lower() == "production":
        raise RuntimeError("Monitoring WebSocket signing secret is not configured")
    return _DEVELOPMENT_FALLBACK
