"""Carrier + telephony-v2 HTTP surface, composed once.

Mounted by ``app/main.py`` as ``carrier_router`` (Batch 06).

What this composes
------------------
* :mod:`app.api.carrier_media_routes` — ``/api/v1/carriers/*`` (capability
  catalogue, per-carrier connection probes) and ``/api/v1/s2s/sessions``, the
  speech-to-speech session read model.
* :mod:`app.telephony.vonage_handler` — ``/telephony/vonage/answer`` (NCCO),
  ``/telephony/vonage/events`` (signature-verified callback) and the
  ``/telephony/vonage/ws`` media socket.
* :mod:`app.telephony.sip.sip_handler` — ``/telephony/sip/ws``.

What this deliberately does **not** compose
-------------------------------------------
:mod:`app.telephony.web_transport` (``POST /telephony/web/offer`` and
``/telephony/web/ws``) is already included by
:mod:`app.api.web_call_live_routes`, which ``app/main.py`` mounts. Including it
again here would register both paths twice: the first copy would win at runtime,
and ``contracts/openapi.json`` would gain duplicate operation ids, which is a
hard failure for every generated client.

Public paths
------------
``/telephony/vonage/answer`` and ``/telephony/vonage/events`` are reachable
without a session because the carrier is the caller. Each verifies its own
credential — the Vonage JWT/HMAC signature on the raw body — before the body is
trusted, and both are on the reviewed public allowlist in
``security_harness/authz_matrix.PUBLIC_ROUTES``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import carrier_media_routes
from app.telephony import vonage_handler
from app.telephony.sip import sip_handler

router = APIRouter()

# BATCH06:carrier_surface_composition
router.include_router(carrier_media_routes.router)
router.include_router(vonage_handler.router)
router.include_router(sip_handler.router)

__all__ = ["router"]
