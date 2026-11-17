# VoxDesk SIP Gateway (`services/sip-gateway`)

Two independent images live here:

- **`Dockerfile` — Go SIP/RTP gateway.** It listens for SIP over UDP, handles
  the basic INVITE/ACK/BYE call path, negotiates PCMU/PCMA, owns the RTP
  sockets and bridges raw G.711 frames to the VoxDesk WebSocket. The control
  API originates a direct `sip:user@host[:port]` UDP call; an E.164 call uses
  the optional machine-wide `VOXDESK_SIP_TRUNK_HOST`/`PORT`.
- **`Dockerfile.kamailio` — Kamailio TLS ingress proxy.** It uses
  `kamailio.cfg`, `tls.cfg`, and a validated public signalling hostname. SIP
  UDP on port 5060 is only for the private edge-to-Go hop; the Compose stack
  publishes TLS on 5061 only.
- **`Dockerfile.rtpengine` — RTPEngine media relay.** It runs userspace
  RTPEngine with named `external` and `internal` interfaces. The
  `infra/docker/docker-compose.sip.yml` stack joins it to the isolated SIP
  edge network and publishes only its carrier-facing media port range.
- **`Dockerfile.bridge` — one-call Python RTP/WebSocket bridge.** It has no SIP
  signalling and is not the Go gateway. See `bridge/README.md`.

The Go user agent is deliberately small. It does **not** implement SIP
TCP/TLS, SRTP, registration, or full SIP transaction/retransmission handling.
An authenticated tenant/trunk runtime-config endpoint and ID-only control
request contract now exist, but the gateway rejects profiles requiring TLS or
SRTP before sending an INVITE, and the production Python client keeps
`supports_tenant_trunks = false`. The Go media leg accepts only plain
`RTP/AVP` SDP without SRTP, DTLS, or ICE attributes; an edge must terminate and
rewrite carrier media before forwarding it. This guard is not SRTP support or
proof of edge integration. This is not carrier-ready BYOC routing. See
[`docs/SIP_TRUNK_GATEWAY_INTEGRATION.md`](../../docs/SIP_TRUNK_GATEWAY_INTEGRATION.md)
before treating this component as a production trunk.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOXDESK_SIP_GATEWAY_TOKEN` | — | Required, non-placeholder bearer token for the control API and key of each media HMAC. Use the same value as Python `SIP_GATEWAY_TOKEN`. |
| `VOXDESK_SIP_MEDIA_WS_URL` | — | Required application/test media socket, e.g. `ws://api:8000/telephony/sip/ws`. |
| `VOXDESK_SIP_HOST` | `sip.voxdesk.local` | Address advertised in SDP. Set to a routable address for the SIP/RTP peer. |
| `VOXDESK_SIP_PORT` | `5060` | Local SIP UDP port. |
| `VOXDESK_SIP_API_ADDR` | `:8080` | Control API listen address. |
| `VOXDESK_MEDIA_PORT_MIN` / `MAX` | `30000` / `30100` | RTP UDP port range. |
| `VOXDESK_SIP_CODEC` | `PCMU` | Preferred codec: `PCMU` or `PCMA`. |
| `VOXDESK_SIP_TRUNK_HOST` | empty | Optional static outbound host for E.164; never inferred from the media/SIP advertised address. |
| `VOXDESK_SIP_TRUNK_PORT` | `5060` | Static outbound host port. |
| `VOXDESK_SIP_CONFIG_URL` | empty | Optional legacy machine-wide JSON config endpoint returning `username`, `password`, and `allowed_cidrs`. It is not keyed by tenant/trunk. |
| `VOXDESK_SIP_TENANT_TRUNK_CONFIG_URL` | empty | Separate authenticated internal API URL, normally `GET /internal/telephony/sip-trunk-runtime-config`. The gateway sends `tenant_id`, `trunk_id`, and optional `environment_id` query parameters; the API returns the matching trunk profile and resolved password. Missing URL, token, mismatched IDs, or incomplete config fails closed. |
| `VOXDESK_SIP_INBOUND_ROUTE_URL` | empty | Per-INVITE authenticated internal route URL, normally `POST /internal/telephony/sip-inbound-route`. It resolves the called DID, active SIP connection, tenant, and source-IP allow-list without caching or creating a call row. Missing URL or failed lookup returns a non-2xx SIP response; the gateway never accepts an unscoped inbound call. |
| `VOXDESK_SIP_CONFIG_TTL` | `30s` | Cache lifetime for the machine-wide and tenant/trunk config responses. Inbound routing is deliberately fetched fresh for every INVITE. |
| `VOXDESK_SIP_ALLOWED_CIDRS` | empty | Immediate SIP-socket-peer ACL (direct carrier in a lab, trusted edge in production); empty allows every socket peer, so set a restrictive list. |
| `VOXDESK_SIP_TRUSTED_PROXY_CIDRS` | empty | Explicit networks allowed to assert one `X-VoxDesk-Source-IP` header. Untrusted sources' headers are ignored; configured proxies missing, duplicating, or supplying an invalid value fail closed. The SIP edge must remove caller-provided copies and overwrite the header. |
| `VOXDESK_SIP_MEDIA_PEER_CIDRS` | empty | SDP RTP destination allow-list (normally the private RTPEngine edge network). Without it, RTP destinations must use the SIP socket peer IP. Configure a restrictive list and firewall the UDP media ports. |

The gateway refuses to start when its shared token is missing or contains an
obvious placeholder, or when the media WebSocket URL is missing. The control
API currently verifies the bearer token only; it does not verify the optional
Python `X-VoxDesk-Signature` header or deduplicate `Idempotency-Key`. The
shipped Python clients do not automatically retry mutating requests, but a
timeout leaves the outcome unknown and a caller/operator replay can still
repeat an INVITE or other side effect. Do not treat those headers as an
exactly-once or request-integrity guarantee yet.

If either config URL returns credentials, protect that response with TLS or
an equivalently restricted private service network. `VOXDESK_SIP_TENANT_TRUNK_CONFIG_URL`
may point only to the authenticated internal runtime-config route; do not point
it at the public trunk CRUD route, which must never return plaintext
credentials. `VOXDESK_SIP_INBOUND_ROUTE_URL` must point only to
`/internal/telephony/sip-inbound-route`. The application requires a ready,
tenant-owned SIP connection, an active inbound-enabled SIP DID, and an explicit
per-connection `allowed_cidrs` list; the source IP is taken from the gateway's
UDP peer address unless the peer belongs to `VOXDESK_SIP_TRUSTED_PROXY_CIDRS`,
in which case exactly one valid `X-VoxDesk-Source-IP` is required. The Kamailio
edge must strip caller-provided copies and overwrite the header from its own
socket peer. The gateway attaches the returned tenant/trunk/environment IDs to
its call record before SIP 200 and repeats them, plus the authorized source IP,
in the authenticated media-WebSocket start frame. The Python handler repeats the
source-CIDR check and validates the same binding before creating call history;
the call row uses that exact environment (or the tenant's active production
environment when neither number nor connection has an explicit one). Do not
expose the internal endpoint publicly.

The empty route URL is intentionally fail-closed: inbound INVITEs receive
`503 Service Unavailable` rather than becoming unscoped calls. Production BYOC
remains disabled while the Python `SipGatewayClient.supports_tenant_trunks`
capability is false. The Go SIP/RTP user agent itself is still UDP/plain RTP;
the Kamailio + RTPEngine ingress edge described below is an experimental
boundary, not proof of production trunk support.

## TLS / SRTP ingress edge (experimental)

`infra/docker/docker-compose.sip.yml` describes the selected ingress topology:
Kamailio terminates carrier SIP TLS, RTPEngine anchors carrier media, and only
private UDP/plain RTP is exchanged between RTPEngine and the Go gateway. It
publishes TCP 5061 for SIP TLS and UDP 10000–20000 for carrier media. SIP UDP
5060, RTPEngine's NG control socket on UDP 22222, and the Go RTP range
30000–30100 are not published on the host. The RTPEngine and Go services share
the private `sip-edge` network; the API/WebSocket path uses a separate
`sip-app` network. The default edge subnet is `172.30.60.0/24`; if it conflicts
with the host or another network, change the subnet and every matching static
address/CIDR in Compose and the gateway configuration together.

Before starting the Compose stack, supply:

- `VOXDESK_SIP_SIGNAL_HOST`: public DNS name or IPv4 address used for TLS
  Record-Route; the certificate SAN must cover the DNS name.
- `VOXDESK_SIP_PUBLIC_IP`: carrier-reachable public IPv4 address advertised by
  RTPEngine. For NAT, configure the matching public-to-container UDP forwarding.
- `VOXDESK_SIP_TLS_CERT_FILE` and `VOXDESK_SIP_TLS_KEY_FILE`: host paths to the
  ingress certificate/full chain and matching private key.
- `VOXDESK_SIP_GATEWAY_TOKEN`: strong secret, identical to the API's
  `SIP_GATEWAY_TOKEN`.

The containers validate/render the public addresses into their configuration.
Allow only TCP 5061 and the configured RTPEngine UDP media range through the
public firewall, preferably scoped to carrier source networks where practical.
Do not publish UDP 5060 or 22222. The private edge source and media CIDRs are
set in Compose so the Go gateway trusts only Kamailio's SIP peer and only
RTPEngine's SDP media address. The carrier's source IP is stripped from any
caller-provided header and replaced by Kamailio before the fresh Python route
lookup.

This is ingress-only: Kamailio has no outbound carrier route, the Go agent
rejects tenant/trunk profiles requiring TLS/SRTP, and the Python capability
flag remains false. The application must still authorize each DID/trunk before
SIP success; the edge is not an authorization service. Docker Compose has not
been run in this workspace, and this configuration is not staging or carrier
certification. See the integration audit for the exact local checks and
remaining gates.

## Media protocol

```text
-> {"type":"start","call_id":"...","from":"+1...","to":"sip:user@host","codec":"PCMU","direction":"outbound"}
-> binary frame: raw negotiated G.711 audio
-> {"type":"dtmf","digit":"5"}
<- binary frame: raw G.711 audio to send as RTP
```

The gateway authenticates the media WebSocket with the bearer token and a
per-call `HMAC-SHA256(secret, "{call_id}:{timestamp}:{nonce}")` in
`X-VoxDesk-SIP-Session`. The Python app verifies the same material in
`app/telephony/sip/sip_handler.py`. An inbound start frame additionally carries
`tenant_id`, `trunk_id`, the resolved `environment_id`, and the gateway-derived
`source_ip`; the app re-resolves the DID and checks that peer address against
the connection's allow-list before creating a call row. The call row is scoped
to the same authorized environment that the gateway bound before SIP 200.

## Tests

Validated with Go 1.27.0 in this workspace: `gofmt` is clean, and `go test -count=1 ./...`, `go test -race -count=1 ./...`, and `go vet ./...` pass.

To repeat the test suite:

```bash
cd services/sip-gateway
go test ./...
go test -race ./...
go vet ./...
```

The root `docker-compose.sip.yml` uses a disposable, test-only media peer and
is described in `tests/telephony/sipp/README.md`. It is not the Python
production media endpoint. A host-local Kamailio + RTPEngine + actual Go
integration check used a mocked route/WebSocket app and distinct test
addresses. It exercised TLS ingress, tenant-bound 200 versus unknown-DID 404,
source-header replacement, and a successful 200's Contact plus public/private
Record-Route pair. An ACK built from the reversed route set was observed at the
Go SIP socket in its call-scoped PCAP; a routed BYE then received 200 and
terminated the call. The test also exercised SDES-SRTP-to-private-RTP SDP
translation. The TLS client skipped verification of the temporary self-signed
certificate. Separately, the opt-in SIPp v3.7.3 UDP UAS scenario passed
against the Go gateway and the test-only authenticated media peer, completing
INVITE/200/ACK/BYE and leaving the Go call terminated. That did not exchange
RTP packets or exercise tenant routing. Neither local check ran Docker, the
actual Python route/database, carrier-issued certificate validation, or
staging; they are not production proof.
