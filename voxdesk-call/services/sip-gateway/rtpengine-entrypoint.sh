#!/bin/sh
set -eu

: "${VOXDESK_SIP_PUBLIC_IP:?set the carrier-reachable public IPv4 address}"
: "${VOXDESK_SIP_MEDIA_IP:?set the RTPEngine private-network IPv4 address}"

# These values are inserted into the daemon's config. Validate strict IPv4
# literals first so config syntax cannot be injected through environment values.
python3 - "$VOXDESK_SIP_PUBLIC_IP" "$VOXDESK_SIP_MEDIA_IP" <<'PY'
import ipaddress
import sys

for value in sys.argv[1:]:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise SystemExit(f"RTPEngine requires IPv4 address literals: {value!r}") from exc
    if parsed.version != 4:
        raise SystemExit(f"RTPEngine requires IPv4 address literals: {value!r}")
PY

sed \
    -e "s|@VOXDESK_SIP_PUBLIC_IP@|$VOXDESK_SIP_PUBLIC_IP|g" \
    -e "s|@VOXDESK_SIP_MEDIA_IP@|$VOXDESK_SIP_MEDIA_IP|g" \
    /etc/rtpengine/rtpengine.conf.template > /tmp/rtpengine.conf

exec /usr/bin/rtpengine --config-file=/tmp/rtpengine.conf
