"""Batch 07 platform surfaces: workforce management, white-label, mobile push.

Three products with one deployment story, so they share one settings object and
one router aggregator:

* :mod:`app.platform_v2.wfm` — interval history, seasonal forecasting, Erlang
  staffing, constraint-solved schedules, time off, swaps/bids, intraday and
  adherence. Every number it reports is computed from stored rows; nothing in
  this package carries a hardcoded forecast or accuracy figure.
* :mod:`app.platform_v2.whitelabel` — customer-owned domains (CNAME + TXT
  ownership), on-demand TLS driven by an ``ask`` endpoint that only approves
  active, verified domains, branding, branded email senders, widget theming.
* :mod:`app.platform_v2.mobile` — device/push-token registry, FCM (HTTP v1) and
  APNs (HTTP/2 + ES256) senders, and the mobile OAuth-PKCE flow with refresh
  rotation and reuse detection.

Import order matters only in one place: :mod:`app.platform_v2.settings` must be
importable without any of the three packages, because the API mounts this batch
at import time and a configuration error has to be reportable on its own.
"""

from __future__ import annotations

from app.platform_v2.settings import PlatformV2Settings, get_plat2_settings

__all__ = ["PlatformV2Settings", "get_plat2_settings"]
