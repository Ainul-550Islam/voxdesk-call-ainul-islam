# experimental/ — not on the call path, not part of sale claims

**Nothing in this directory runs a customer call.** No VoxDesk deployment —
`docker-compose.prod.yml`, `infra/helm/voxdesk`, the Caddy edge, or any CI
install job — starts, routes to, or depends on a single process here. If every
file in this directory were deleted tonight, a voice agent would still answer a
phone call tomorrow morning.

These services are **engineering work**, not shipped product. They exist to
prove that VoxDesk's invariants (tenant isolation, idempotency, observability)
hold when expressed in other languages, and to hold a place for a future
architecture. They are tested, and the tests are real — but a green test in
`experimental/` is evidence about *design*, never evidence about the running
platform.

## What is here and why it was set aside

| Directory | Language | What it is | Why it is not on the call path |
|---|---|---|---|
| `control-plane/` | Rust | `voxdesk-control` session/transfer state machine, usage/rating pipeline, token-bucket limiter, plus `voxdesk-signal` WebSocket hub | The live state machine is `app/telephony/call_state.py` + `transfer_service.py` in Python. Running two state machines over one call is a correctness hazard; migrating is an unstarted project, not a config flag. |
| `signal-go/` | Go | Real-time WebSocket signalling hub, differential counterpart of `voxdesk-signal` | Superseded for the public edge by `services/realtime/gateway-go`, which *is* deployed. `signal-go` is the internal-room variant and has no ingress. |
| `media-plane/` | C++ | WebRTC SFU pieces: Opus jitter buffer, consistent-hash tenant router, FFT/denoiser, G.711 codecs, VAD | The deployed media path is `services/realtime/media-engine-rs`. Two media planes would both want the same UDP ports. |
| `ops/` | Go | `voxops` CLI (`backup-verify`, parity helpers) | An operator convenience, not a platform dependency. The Python equivalents (`scripts/`, `ops.py`) are what the runbooks call. |

## What is NOT here

Three services stay in `services/` because they are on the call path:

- `services/realtime/gateway-go` — the public WebSocket edge (deployed, helm `realtime-gateway`)
- `services/realtime/media-engine-rs` — the media plane (deployed, helm `media-engine`)
- `services/sip-gateway` — SIP ingress after PART 13 (deployed when `SIP_GATEWAY_URL` and a non-placeholder `SIP_GATEWAY_TOKEN` are set; helm `sipGateway.enabled`)

That split, and the evidence required to promote something out of this
directory, is recorded in `docs/adr/ADR-003-polyglot-scope.md`.

## House rules

1. **A sale claim may not cite code in this directory.** Pricing pages,
   capability matrices and buyer decks describe what `docker-compose.prod.yml`
   and the Helm chart actually run.
2. **CI still builds and tests these** (`.github/workflows/polyglot.yml`), because
   a broken build here is still a broken repo. The jobs are labelled
   `experimental/*` and are not release-gating.
3. **No production dependency may be added in this direction.** Production code
   must not import, call, or require anything under `experimental/`. There is a
   test that fails if it does — `tests/deployment/test_polyglot_scope.py`.
