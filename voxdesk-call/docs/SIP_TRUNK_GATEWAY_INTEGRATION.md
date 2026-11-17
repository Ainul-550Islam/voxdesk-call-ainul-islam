# SIP trunk / gateway integration audit

## Status

**Inbound tenant/trunk resolution is wired and covered by local tests, but
production BYOC remains disabled.** The Go gateway makes a fresh,
bearer-authenticated route lookup for every inbound INVITE, verifies the
returned call/tenant/trunk identity, binds `TenantID` and `TrunkID` to the call
before SIP 200, and carries the binding into the media WebSocket. The Python
handler repeats the source-CIDR/database check before creating call history;
the authenticated start frame carries the gateway-derived source identity:
the UDP socket peer directly, or an asserted carrier IP accepted only from a
configured trusted SIP edge. A SIP header from any other peer cannot override
socket identity. The route requires a ready tenant-owned SIP connection, a
matching inbound-enabled SIP DID, an active tenant, and a non-empty
per-connection source CIDR allow-list.
The resolver has no cache and creates no call row. It resolves the explicit
number/connection environment or the tenant's active production environment;
the WebSocket recheck persists that exact environment to the app call record.
The Python production
`SipGatewayClient.supports_tenant_trunks` flag remains `false`, so the
application currently returns 503 for tenant-trunk routing and the gateway
fails closed; this gate must not be flipped until secure transport is verified.
The Go gateway remains a UDP SIP/RTP prototype without SIP TLS or SRTP. The
bearer-authenticated PCAP endpoint now receives the carrier/gateway call key
resolved from the tenant-owned durable call row, while the artifact remains
owned by the app's UUID call ID.

## Selected secure-transport architecture — edge configured, full proof pending

Use the **Kamailio + RTPEngine edge** described by
`docs/adr/ADR-001-sip-ingress.md`, not custom TLS/SRTP cryptography in the Go
UDP user agent. The repository now contains an ingress configuration in
`services/sip-gateway/kamailio.cfg`, TLS profiles in `tls.cfg`, a userspace
RTPEngine image/config/entrypoint, and a private two-network Compose topology in
`infra/docker/docker-compose.sip.yml`. Kamailio terminates carrier SIP TLS,
forwards SIP over private UDP to Go, and overwrites the proxy-only source-IP
header. Its public TLS hostname is rendered into Record-Route. RTPEngine uses
named `external`/`internal` interfaces, terminates the carrier SDES-SRTP
profile, and rewrites the Go-facing SDP/media leg to plain RTP on the private
edge network. The Go validator still accepts only `RTP/AVP` and rejects
SRTP/DTLS/ICE profiles or attributes if secure media reaches it unconverted.
No Go RTP port is published publicly.

This is ingress-only. Kamailio's client TLS profile is configured to verify
server certificates, but no outbound carrier route uses it yet. Carrier-facing
server TLS does not require a client certificate; tenant/trunk and source-IP
authorization remains in the application route, not in the edge. The default
Compose edge subnet is `172.30.60.0/24` with explicit Kamailio, Go, and RTPEngine
addresses and matching trusted-proxy/media CIDRs; change them together if the
subnet collides with the deployment network. Host firewall/NAT must publish TCP
5061 and forward the configured RTPEngine UDP media range to the RTPEngine
container; do not publish SIP UDP 5060 or NG control UDP 22222.

The edge must not become the tenant authorization authority. Go still calls
the Python route resolver for every inbound INVITE and binds the authorized
tenant/trunk/environment before SIP 200. The Go boundary implements
`VOXDESK_SIP_TRUSTED_PROXY_CIDRS`, requires exactly one valid
`X-VoxDesk-Source-IP` from those immediate peers, and applies trunk source ACLs
to the asserted carrier IP. Kamailio removes caller-provided copies and writes
the carrier socket source. The Go SDP boundary restricts media destinations to
the configured RTPEngine CIDR; Python repeats the source-CIDR/tenant checks
before creating call history.

A host-local TLS-to-Go component interop check passed using Kamailio 6.0.1,
RTPEngine 12.5.1, the actual Go gateway, a temporary self-signed TLS
certificate, and rendered test addresses. The test client skipped verification
of that certificate. A small mocked application supplied the authenticated
route and media-WebSocket endpoints. The test exercised a TLS INVITE where
tenant/trunk/environment IDs and the socket-derived source IP were carried into
the media start frame before SIP 200; an unknown DID failed with SIP 404 and no
media WebSocket; and duplicate caller-supplied source headers were replaced
with Kamailio's socket IP. The 200's Contact and public/private Record-Route
pair were checked. The client then formed its UAC route set by reversing the
Record-Route pair and sent a 2xx ACK through TLS; the Go gateway's call-scoped
PCAP contained that ACK at its SIP socket. A subsequent routed BYE received
200 and left the Go call terminated. A separate 404/non-2xx ACK probe confirmed
Kamailio relays an ACK without a dialog Route set to the matching transaction's
temporary UDP test UAS. The run also exercised SDES-SRTP-to-private-RTP SDP
translation with public/private media-address rewriting. It did **not** use
Docker Compose, the actual Python route/database or production
WebSocket, carrier-issued certificate validation, real carrier IPs, or RTP
media packets. Docker/Compose is unavailable in this workspace; the static
Compose YAML was parsed with PyYAML, but the images and private network have not
been started here.

This proves a local edge/RTPEngine/Go component path against a mock application,
not a complete production integration. No staging run has proved a trusted
certificate chain, carrier TLS interoperability, SRTP/DTLS variations,
anti-downgrade behavior, tenant binding through the real Go/Python services, or
media reachability/quality. Keep the Python
`SipGatewayClient.supports_tenant_trunks` flag `false` and all production BYOC
routing disabled until the integrated no-skip and staging gates pass.

## Existing contracts

| Surface | Current shape |
|---|---|
| Python model `app.db.telephony_models.SipTrunk` | Per-tenant `host`, `port`, `transport`, `srtp_required`, `auth_username`, `auth_secret_ref`, `caller_id`, and egress metadata. The row stores a reference, not a password. |
| Python route `app.api.sip_trunk_routes` | Tenant-scoped CRUD, health, and gateway-health check. `auth_password` is write-only and immediately sealed; references are verified with purpose `sip_trunk_auth`. The public serializer never returns a password. `/internal/telephony/sip-trunk-runtime-config` requires the shared gateway bearer token and filters by tenant, trunk, and optional environment IDs before resolving the secret. |
| Python inbound SIP routing | `/internal/telephony/sip-inbound-route` requires the shared gateway bearer token and performs a fresh, side-effect-free lookup from E.164 DID to exactly one READY `TelephonySipConnection` plus one inbound-enabled, SIP-enabled `TelephonyPhoneNumber` for an active tenant. A non-empty connection `metadata_json.allowed_cidrs` list is mandatory and the gateway-supplied SIP peer IP must match. The environment must belong to that tenant and be active; if neither row has an explicit environment, the route resolves the tenant's active production environment. Duplicate/ambiguous routes and unavailable capability fail closed. |
| Python `SipOriginator` | Requires tenant/trunk IDs and builds an ID-only runtime request. The production `SipGatewayClient` still explicitly declares no tenant-trunk support until TLS+SRTP is implemented and verified, so origination and inbound route resolution fail closed before any successful call. |
| Python `SipOutboundAdapter` | Sends bearer auth, an extra HMAC header, and an `Idempotency-Key`; the Go control API verifies only the bearer token and currently ignores both HMAC and idempotency. The Python clients therefore do not automatically retry mutating requests; a timeout has an unknown outcome, and an operator or upstream replay can still duplicate a side effect. |
| Go control API and inbound path | The control API accepts `call_id`, `from`, `to`, `codec`, plus optional `tenant_id`, `trunk_id`, and `environment_id`. Without IDs, a SIP URI is a direct UDP lab target and E.164 uses static machine-wide `VOXDESK_SIP_TRUNK_HOST`/`PORT`. With IDs, it fetches the exact scoped profile; TLS/SRTP is still rejected before INVITE. Inbound SIP uses `VOXDESK_SIP_INBOUND_ROUTE_URL` for a fresh bearer-authenticated route lookup; the gateway verifies the SIP Call-ID, normalized called DID, and UUID tenant/trunk/environment scope; it binds those IDs and the authorized source IP (socket peer, or trusted-edge assertion) to the call before SIP 200. The authenticated WebSocket start frame repeats that scope and source IP, and Python requires the route recheck to match before creating history. Missing or unavailable route service fails closed with SIP 503. |
| Go PCAP endpoint | `POST /v1/calls/{call_id}/pcap` is bearer-authenticated, enforces the call's known tenant, and captures SIP/RTP/RTCP UDP datagrams only for that call and requested window. For app-owned UUID calls, the app resolves the authorized row's `call_sid`/`provider_call_id` for the gateway while keeping artifact ownership on the UUID. Arbitrary packet-filter expressions are unsupported and explicitly rejected with `400 unsupported_filter`. It emits classic DLT_RAW PCAP bytes with packet-count and truncation metadata; the app validates the header, packet-record boundaries, and reported count before storage, persists count/truncation on the artifact row, and includes them in the response and audit event. It does not reconstruct signalling/media packets that occurred before capture was armed. |
| Python PCAP lifecycle | Capture requires `calls.debug` and a known tenant-owned call row; listing filters artifacts by the requesting tenant. The app verifies short-lived HMAC download links, marks binary responses `private, no-store`, distinguishes a missing object (404) from storage outage (503), and audits successful captures, authorized capture failures, link-mint outcomes, and purge results. Purge is tenant-scoped; a cross-tenant scheduler must invoke it once for each tenant. |
| Go `VOXDESK_SIP_CONFIG_URL` | Legacy global JSON object with `username`, `password`, and `allowed_cidrs`; it remains separate from tenant settings. |
| Go `VOXDESK_SIP_TENANT_TRUNK_CONFIG_URL` | Distinct internal endpoint URL. The Go cache adds tenant/trunk/environment query parameters, sends the gateway bearer token, verifies returned IDs, and caches per exact scope. Unavailable/incomplete configuration fails closed. |
| Go `VOXDESK_SIP_INBOUND_ROUTE_URL` | Separate internal POST endpoint for per-INVITE routing; each request carries the SIP `To`, `From`, Call-ID, and authorized source IP (direct UDP peer or assertion from an explicitly trusted edge). The authenticated response is never cached and carries only route IDs and normalized numbers, not credentials. |
| Go trusted SIP edge | `VOXDESK_SIP_TRUSTED_PROXY_CIDRS` must enumerate only edge socket addresses; Kamailio must remove any caller-supplied `X-VoxDesk-Source-IP` and write exactly one value from the actual carrier socket. Never trust generic forwarding headers. |
| Go media destination ACL | `VOXDESK_SIP_MEDIA_PEER_CIDRS` limits SDP RTP endpoints to the private edge/relay network; with no list, the advertised media address must match the SIP socket peer. |
| Root `docker-compose.sip.yml` | Disposable lab only. It supplies a local test token and a test media WebSocket peer that accepts/discards media. It does not start the Python app or configure any runtime or inbound route URL. |

The trunk-page `test` endpoint probes gateway health only; it reports
`GATEWAY_REACHABLE`, not `READY`, because it does not send SIP OPTIONS to the
carrier. Do not point any Go config URL at `/api/telephony/trunks`: the
operator-facing API returns references, not call-time passwords. The runtime
profile URL must point only to `/internal/telephony/sip-trunk-runtime-config`,
and the inbound route URL only to `/internal/telephony/sip-inbound-route`.

## Security and protocol boundary

- `SIP_GATEWAY_TOKEN` in the Python service and `VOXDESK_SIP_GATEWAY_TOKEN` in
the Go service must be the same strong, non-placeholder secret. In Compose,
the value `sip-e2e-token` is deliberately test-only.
- The Go config fetch uses `Authorization: Bearer <gateway token>`. Since its
  runtime-profile JSON includes a plaintext SIP digest password, production
  must carry it over TLS or an equivalently restricted private service network.
  Never put carrier passwords in Go logs or the public trunk API.
- Public trunk writes accept a write-only `auth_password` (sealed immediately)
  or a verified opaque reference. The canonical secret-store purpose is
  `sip_trunk_auth`, bound to the tenant. The runtime-config route resolves it
  in Python and returns the password only to a caller with the shared gateway
  bearer token; operator CRUD/list serializers never return it.
- The scoped runtime route and cache are implemented but not yet exercised
  together in a live Compose deployment. The response contains call-time
  credentials, so production must use HTTPS or an equivalently restricted
  private service network. The Go cache key includes tenant, trunk, and
  environment IDs and verifies tenant/trunk IDs in the response.
- The inbound route endpoint returns no credentials and is not cached. It
  requires a ready connection, a matching inbound-enabled SIP DID, an active
  tenant, and a non-empty per-connection `allowed_cidrs` list; missing or
  malformed ACLs and source mismatches fail closed. For direct lab traffic,
  the gateway forwards the UDP socket peer as source identity. For an immediate
  peer in `VOXDESK_SIP_TRUSTED_PROXY_CIDRS`, it requires exactly one valid
  `X-VoxDesk-Source-IP` value. The edge must strip any incoming copy and
  overwrite it with the carrier source; missing, repeated, or malformed values
  from a trusted edge fail closed, while headers from untrusted peers are
  ignored. The gateway's `VOXDESK_SIP_ALLOWED_CIDRS` constrains immediate socket
  peers, while the trunk ACL and Python route check apply to the original carrier
  source. `VOXDESK_SIP_MEDIA_PEER_CIDRS` restricts SDP RTP destinations; if
  empty, the RTP IP must equal the signalling socket peer. The authenticated
  media socket repeats the tenant/trunk/DID check before writing call history.
- The Python `SipGatewayClient.supports_tenant_trunks` flag remains `false`.
  Therefore the Python resolver returns 503 and tenant SIP connections cannot
  become READY through the supported lifecycle until the full secure transport
  capability is verified. The Go gateway still uses SIP over UDP and plain RTP;
  it does not implement SIP TCP/TLS or SRTP. Do not flip the flag or enable
  production BYOC based only on the route-binding tests.
- `VOXDESK_SIP_ALLOWED_CIDRS` is a separate deployment-wide gateway ACL; an
  empty list allows all sources at that layer. Production must set a restrictive
  network/gateway ACL as well as the per-connection source list.

## What the current tests prove

- Python SIP handler tests verify that inbound media starts carry a tenant/trunk
  claim, that the live database route must match those claims, and that an
  unscoped or mismatched inbound start is closed before call history is written.
- `tests/telephony/test_sip_inbound_route.py` covers bearer authentication,
  the still-disabled TLS/SRTP capability gate, a positive one-tenant/one-trunk
  DID resolution, no-write behavior, and source-CIDR denial.
- `tests/telephony/test_sip_trunk_runtime_config.py` exercises gateway bearer
  auth, write-time sealing, tenant/purpose-bound secret resolution, scoped DB
  lookup, and public response redaction.
- Go tests cover fresh authenticated inbound route resolution, rejection of
  invalid/mismatched call-ID or DID/tenant/trunk/environment responses, trusted
  proxy source-IP handling, plain-RTP-only SDP enforcement, RTP endpoint
  allow-listing, the authorized tenant/trunk/environment and source IP in the
  media start frame, tenant-scoped
  PCAP capture, and a 503 with no call creation when the resolver is absent.
  A concurrent-session regression test verifies the active
  call gauge decrements accurately rather than dropping to zero on the first
  call's finish.
- Current local validation: `go test ./...`, `go test -race ./...`, and
  `go vet ./...` pass for the gateway. Six focused Python SIP/PCAP/runtime
  modules pass **101 tests, 7 warnings**. The complete `tests/telephony`
  population passes through the chunk runner: **810 collected, 808 passed,
  2 skipped, 0 failed/errors, 0 unaccounted** (explicit PostgreSQL URL).
  Three migration modules plus the two PostgreSQL backup/recovery cases pass
  **7 tests** against the disposable database migrated to Alembic head
  `0091_call_debug_pcap_metadata`. The inbound-route test explicitly asserts
  the successful response includes `"ok": true`. An earlier standard non-live
  Python run recorded **8,040 collected, 8,031 passed, 9 skipped, 0
  failed/errors, 0 timeouts/crashes, 0 unaccounted** across 201 chunks, with
  explicit `DATABASE_URL`; opt-in `real_provider` and `live` tests were
  excluded, so it does **not** satisfy the requested no-skip gate. After the
  latest code changes, a fresh rerun collected 8,042 nodes but hit the
  30-minute execution limit at chunk 87/202; no complete aggregate exists and
  this attempt is not a passing result. A fresh SIP-focused run (`pytest -q`
  over the route, trunk-runtime, gateway client, handler, outbound, test-media,
  connection, and non-stack SIP-stack
  modules) passed **76 tests**, with 2 optional stack-marked tests deselected
  and 7 warnings. The earlier focused run passed **59 tests** across SIP
  connection/route, ops preflight, and due-diligence evidence-gate modules.
  Machine-readable results from the earlier complete
  run are in `validation/full-python-explicit-db/`.
- The test-media peer tests check the disposable Compose harness's bearer/HMAC
  handshake and HTTP status endpoints.
- The opt-in SIPp test attempts one real UDP SIP dialog through a running Go
  gateway, when SIPp and explicit lab variables are available. It is skipped
  by default. It does not prove TLS/SRTP, carrier credentials, Python tenant
  trunk selection, live audio quality, or a production carrier call.
- `tests/real/test_sip_gateway_pcap.py` is an opt-in, one-second smoke test for
  an explicitly supplied active lab call. It validates the authenticated HTTP
  response, classic DLT_RAW PCAP structure, and packet-count metadata in memory;
  it does not originate/terminate calls or persist packet bytes. It has not run
  live in this workspace because no gateway URL/token and active call are set.
- `tests/real/test_sip_gateway_pcap_s3.py` is an opt-in S3-compatible store
  round-trip using a random disposable key and an empty PCAP. It verifies
  upload/readback, the app HMAC link, and deletion, and does not record bucket,
  key, credentials, signing secret, or packet bytes. It has not run live here
  because no bucket or AWS credentials are configured.
- PyYAML 6.0.3 parses `infra/docker/docker-compose.sip.yml` and the declared
  static addresses fit its `172.30.60.0/24` SIP-edge subnet. Docker/Compose and
  `rclone` are unavailable, so Compose's own config/build/start checks have not
  run.
- Host `/usr/sbin/kamailio -c` with rendered local addresses reports
  `config file ok` using Debian Kamailio 6.0.1. The RTPEngine 12.5.1.31-1
  userspace daemon starts from the rendered project config; the package service
  itself was not started by the host init system.
- A local TLS SIP test through Kamailio and RTPEngine to the actual Go gateway
  passed with a mocked HTTP route resolver and media WebSocket. An authorized
  INVITE received 200 only after route resolution and WebSocket start; its
  tenant/trunk/environment/source binding appeared in the start frame. An
  unknown DID received SIP 404 and opened no media session. Source-header
  replacement, Contact, the public/private Record-Route pair, and
  SDES-SRTP/public-SDP to RTP/AVP/private-SDP translation were verified. A
  2xx ACK sent through the reversed route set was present in the Go gateway's
  call-scoped SIP PCAP; a subsequent in-dialog BYE received 200 and terminated
  the call. A separate 404 response / non-2xx ACK probe confirmed the proxy's
  transaction-matched fallback forwards ACK without a dialog Route set to a
  temporary UDP test UAS. The client did not verify the temporary self-signed TLS
  certificate; test addresses were used and no RTP packets were exchanged.
  This is local component interop evidence only, not staging or carrier proof.
- The opt-in SIPp v3.7.3 UDP UAS scenario passed against the actual Go
  gateway and test-only authenticated media WebSocket peer without Docker. It
  completed an outbound INVITE/100/200/ACK/BYE exchange and verified the Go
  call reached `terminated`; the scenario now requires the ACK. It did not
  exchange RTP packets or exercise the Python tenant route.
- No Docker image build, carrier-issued certificate validation, real Python
  route/database test, or real carrier/media-quality test has run.

## Remaining work before production trunk parity

1. Build and run `infra/docker/docker-compose.sip.yml` with production-like
   static addresses, a trusted certificate/SAN, firewall rules, and a test
   tenant/connection/DID. Exercise the actual Go route lookup and Python
   WebSocket binding before enabling anything. Add RTP packet-flow checks and
   cover carrier DTLS-SRTP, SIP retransmissions/failure paths, NAT and the
   carrier's TLS trust requirements. Docker image/build/runtime validation is
   still outstanding; the local mock-peer check is not sufficient.
2. Connect `SipTrunk` selection to the authoritative outbound call runtime;
   the generic `SipOutboundAdapter` is still machine-wide and the current
   production request path does not select a `SipTrunk` row.
3. Add SIP call cancellation/retransmission and complete dialog handling,
   including SIP Digest authentication where required and all failure cases.
4. Run a Compose/lab test against the actual Python route and WebSocket
   endpoints with a test tenant/connection/DID, then build the images, start the
   stack, and run SIPp interop. Staging/live gateway and S3 evidence remain
   outstanding; do not change evidence claims to verified without those runs.
