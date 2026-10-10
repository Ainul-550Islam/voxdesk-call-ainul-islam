# VoxDesk SIP Ingress Gateway (`services/sip-gateway`)

Implements **ADR-001** (`docs/adr/ADR-001-sip-ingress.md`):
- **Signaling**: Kamailio (`kamailio.cfg`) listening on UDP/TCP `5060` and TLS `5061`.
- **Media Bridge**: RTPEngine (`rtpengine.conf`) bridging RTP/SRTP into VoxDesk's `SIPBridgeFrameSerializer` (`app/telephony/media/serializers.py`).
- **Authentication & Routing**: Validated by `app/telephony/sip.py` (`SipConnectionService.route_inbound_sip_invite`, `verify_sip_digest_auth`, `check_sip_ip_acl`).
