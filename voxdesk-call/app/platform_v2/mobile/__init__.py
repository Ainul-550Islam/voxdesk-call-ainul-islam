"""Mobile: device registration, push delivery and the token lifecycle.

Four pieces, each with one job:

* :mod:`.devices` — which phone belongs to whom, what it accepts, and when it was
  last heard from. Revoking a device is the only thing an attacker with a stolen
  handset fears.
* :mod:`.fcm` / :mod:`.apns` — the two push transports. Both take a structured
  message and return a provider message id or a classified failure; neither knows
  anything about rota templates.
* :mod:`.oauth` — the mobile sign-in flow: PKCE authorization codes and rotating
  refresh tokens with family-based reuse detection.

The delivery record (``mob_push_deliveries``) is written *before* the provider call
and updated after it, so a crash mid-send leaves a row that says "unknown" rather
than a notification that vanished without a trace.
"""

from __future__ import annotations

__all__: list[str] = []
