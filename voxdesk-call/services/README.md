# services/ — the services that carry a call

This directory contains **only code that runs customer calls in production.**
Everything else the repository builds in another language lives in
`experimental/` and is explicitly *not* on the call path and *not* part of any
sale claim. The rule, and the evidence required to move a service between the
two, is `docs/adr/ADR-003-polyglot-scope.md`.

## Layout

```
services/
├── realtime/
│   ├── gateway-go/       Go — PUBLIC WebSocket edge for dashboards and
│   │                     browsers: JWT-gated hello, tenant-pinned rooms,
│   │                     ingest fan-out from the API, point-to-point WebRTC
│   │                     signalling. Helm: realtimeGateway.enabled
│   └── media-engine-rs/  Rust — media plane / SFU: WebRTC, DTLS-SRTP, Opus
│                         jitter buffer, recording sink, transcription tap.
│                         Helm: mediaEngine.enabled
└── sip-gateway/          Go + Kamailio — SIP ingress: INVITE/ACK/BYE, digest
                          auth, SDP offer/answer (PCMU/PCMA), RTP sockets,
                          RFC 4733 DTMF, G.711 bridged to the API over
                          /telephony/sip/ws. Helm: sipGateway.enabled
                          (off by default; PART 13 made it config-gated)
```

## Why only these three

They are the only non-Python components that appear in
`docker-compose.prod.yml`, in the Helm chart, or in a deployment path at all.
`docker-compose.prod.yml` builds `services/realtime/gateway-go` and
`services/realtime/media-engine-rs` and sets
`VOXDESK_CANONICAL_REALTIME: "gateway-go+media-engine-rs"`; the SIP gateway is
the ingress added by PART 13 and is reachable only when `SIP_GATEWAY_URL` and a
non-placeholder `VOXDESK_SIP_GATEWAY_TOKEN` are configured.

The rest of the platform — the conversation runtime, the flow engine, the
telephony state machine, scheduling, billing — is Python in `app/`, and that is
the brain of every call. The Go and Rust services are the edges around it.

## Moved out

`control-plane/` (Rust), `signal-go/` (Go), `media-plane/` (C++) and `ops/` (Go)
now live in `experimental/`. They are tested and CI still builds them, but no
deployment starts them and no production code imports them. See
`experimental/README.md` for what each one is and why it was set aside.

## The polyglot gate

The services here are held to the same invariants the Python backend enforces,
expressed in their own tooling, and the wire contracts live in
`contracts/proto` (verified by `scripts/verify_contracts.py`,
`tests/test_contracts.py`):

1. **Tenant isolation** — every message is tenant-scoped.
2. **Idempotency / exactly-once side effects** — mirrors the database
   `UniqueConstraint(tenant_id, idempotency_key)` guarantees.
3. **Observability** — Prometheus metrics, structured logs, trace ids.
4. **No production mutation from tests** — no real calls, charges or bookings
   from any test or tool.
5. **Reproducible builds** — pinned toolchain per language, wired into CI
   (`.github/workflows/polyglot.yml`).

## Verification

| Service | Gate |
|---|---|
| `realtime/gateway-go` | `gofmt` + `go vet` + `go test -race ./...` + `go build` (107 tests) |
| `realtime/media-engine-rs` | `cargo test --workspace --locked` (115 tests, 15 crates) |
| `sip-gateway` | `gofmt` + `go vet` + `go test -race ./...` |

`bash scripts/check_other.sh` runs them together; `make go-test-all` and
`make cargo-test-all` run the language-wide loops.
