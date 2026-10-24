# VoxDesk SIP Ingress Gateway (`services/sip-gateway`)

Two images live here.

**`Dockerfile` — the media gateway (Go).** A single binary that

- runs a SIP user agent (`internal/sipua`): it parses and builds INVITE,
  ACK, BYE, REFER and INFO, answers a digest challenge, and matches
  responses to their transactions;
- negotiates the session description (`internal/sdp`) and offers PCMU or
  PCMA;
- owns the RTP sockets (`internal/rtp`, `internal/bridge`) and turns
  RFC 4733 telephone events into digits in both directions;
- bridges the audio to the VoxDesk application (`internal/bridge`) over the
  media WebSocket `/telephony/sip/ws`, as binary G.711 frames (the control
  events are JSON text frames);
- exposes the control API (`internal/api`): `POST /v1/calls`,
  `GET /v1/calls/{id}`, `POST /v1/calls/{id}/hangup`,
  `POST /v1/calls/{id}/dtmf`, `POST /v1/calls/{id}/transfer`,
  `GET /healthz`, `GET /readyz`, `GET /metrics`.

**`Dockerfile.kamailio` — the signalling proxy.** Kamailio with the
configuration in `kamailio.cfg` and `rtpengine.conf`, unchanged from before
the Go gateway existed.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOXDESK_SIP_GATEWAY_TOKEN` | — | Required. Bearer credential for the control API and the key of the per-call HMAC session token. |
| `VOXDESK_SIP_MEDIA_WS_URL` | — | Required. The application's media socket, e.g. `ws://api:8000/telephony/sip/ws`. |
| `VOXDESK_SIP_HOST` | `sip.voxdesk.local` | The host advertised in SDP. |
| `VOXDESK_SIP_PORT` | `5060` | The SIP signalling port. |
| `VOXDESK_SIP_API_ADDR` | `:8080` | The control API address. |
| `VOXDESK_MEDIA_PORT_MIN` / `MAX` | `30000` / `30100` | The RTP port range. |
| `VOXDESK_SIP_CODEC` | `PCMU` | `PCMU` or `PCMA`. |
| `VOXDESK_SIP_CONFIG_URL` | — | The tenant SIP trunk configuration the gateway reads (cached for `VOXDESK_SIP_CONFIG_TTL` seconds). |
| `VOXDESK_SIP_ALLOWED_CIDRS` | empty | The inbound CIDR allowlist; empty allows every source. |

The process refuses to start when the token is missing or contains a
placeholder value, or when the media WebSocket URL is missing.

## The media protocol

```
-> {"type":"start","call_id":"...","from":"+1...","to":"+1...","codec":"PCMU"}
-> binary frame: the raw G.711 audio of the call
-> {"type":"dtmf","digit":"5"}
<- binary frame: the raw G.711 audio the gateway plays out
```

The socket is authenticated with the bearer token and the per-call session
token `HMAC-SHA256(secret, "{call_id}:{timestamp}:{nonce}")`, sent in the
`X-VoxDesk-SIP-Session` header. The application verifies the same material
in `app/telephony/sip/sip_handler.py`.

## Tests

```
cd services/sip-gateway && go test -race ./...
```
