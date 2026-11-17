#!/bin/sh
set -eu

: "${VOXDESK_SIP_SIGNAL_HOST:?set the public SIP TLS hostname or IPv4 address}"

# Kamailio's advertised TLS address becomes the SIP Record-Route for dialogs.
# Validate it before substituting into the config so untrusted deployment
# input cannot alter the configuration grammar.
python3 - "$VOXDESK_SIP_SIGNAL_HOST" <<'PY'
import ipaddress
import re
import sys

value = sys.argv[1]
try:
    parsed = ipaddress.ip_address(value)
except ValueError:
    label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    if len(value) > 253 or not re.fullmatch(rf"{label}(?:\.{label})*", value):
        raise SystemExit("VOXDESK_SIP_SIGNAL_HOST must be an IPv4 literal or DNS hostname")
else:
    if parsed.version != 4:
        raise SystemExit("VOXDESK_SIP_SIGNAL_HOST must be an IPv4 literal or DNS hostname")
PY

sed "s|@VOXDESK_SIP_SIGNAL_HOST@|$VOXDESK_SIP_SIGNAL_HOST|g" \
    /etc/kamailio/kamailio.cfg.template > /run/kamailio.cfg

exec /usr/sbin/kamailio -DD -E -f /run/kamailio.cfg
