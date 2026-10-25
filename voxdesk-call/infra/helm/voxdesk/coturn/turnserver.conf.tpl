listening-port=3478
{{ if .Values.coturn.tls.enabled }}tls-listening-port=5349
cert=/etc/coturn/tls/tls.crt
pkey=/etc/coturn/tls/tls.key
{{ end }}external-ip={{ required "coturn.externalIP is required when coturn is enabled" .Values.coturn.externalIP }}
realm={{ required "coturn.realm is required" .Values.coturn.realm }}
min-port={{ .Values.coturn.minPort }}
max-port={{ .Values.coturn.maxPort }}
fingerprint
lt-cred-mech
use-auth-secret
no-multicast-peers
no-loopback-peers
no-cli
no-tlsv1
no-tlsv1_1
log-file=stdout
