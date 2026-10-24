"""SIP media path for VoxDesk.

* :mod:`app.telephony.sip.connection` -- durable SIP trunk configuration
  (moved here from ``app/telephony/sip.py``; the public names are
  re-exported below so existing imports keep working).
* :mod:`app.telephony.sip.sip_codec` -- G.711 codecs, sample-rate
  conversion and RFC 2833 DTMF parsing (pure functions).
* :mod:`app.telephony.sip.sip_gateway_client` -- async client for the
  gateway control API (originate, hangup, transfer, DTMF, status).
* :mod:`app.telephony.sip.sip_capabilities` -- honest capability resolution:
  the media path exists only when the gateway is really ready.
* :mod:`app.telephony.sip.sip_handler` -- the authenticated WebSocket the
  gateway bridges media into.
"""

from __future__ import annotations

from app.telephony.sip.connection import (
    SipConnectionService,
    check_sip_ip_acl,
    compute_sip_digest_response,
    verify_sip_digest_auth,
)

__all__ = [
    "SipConnectionService",
    "check_sip_ip_acl",
    "compute_sip_digest_response",
    "sip_capabilities",
    "sip_codec",
    "sip_gateway_client",
    "sip_handler",
    "verify_sip_digest_auth",
]
