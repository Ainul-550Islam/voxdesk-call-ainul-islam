# ADR-003: Polyglot Scope — What Is On the Call Path and What Is Experimental

- **Status**: Accepted
- **Date**: 2026-11-08
- **Phase**: PART 17B — Platform Operations (Gate G17b)
- **Closes**: S-15

---

## Context

The repository contains services in five languages. Some of them carry
production calls; some are design work. Until this ADR they all lived side by
side under `services/`, which made the repository look like a platform that
runs a Rust control plane, a Go signalling hub, a C++ media plane and a Go ops
CLI. It does not. A buyer reading the tree, or an engineer reading
`services/README.md`, could not tell which of the sixteen directories answer a
phone call.

That ambiguity is a diligence defect in both directions:

- **Overclaiming.** Marketing or a sales engineer can point at
  `services/media-plane/` and say "we have an SFU in C++", when the SFU that
  ships RTP is `services/realtime/media-engine-rs`.
- **Underclaiming.** `services/realtime/gateway-go` and `gateway-media-engine-rs`
  are real, deployed, and tested — but they sat in the same undifferentiated
  bucket, so nothing distinguished them from a roadmap sketch.

The audit finding **S-15** recorded the second half explicitly: the "polyglot"
claim was not honest because the tree did not state its own boundaries.

## Decision

### 1. The call path is exactly these components

A customer phone call traverses, in order:

| # | Component | Language | Where | What it does on the call |
|---|---|---|---|---|
| 1 | Carrier → **SIP gateway** | Go + Kamailio | `services/sip-gateway` | SIP INVITE/ACK/BYE, digest auth, SDP offer/answer (PCMU/PCMA), RTP sockets, RFC 4733 DTMF → digits, bridges G.711 over `/telephony/sip/ws`. **Deployed only when `SIP_GATEWAY_URL` and a non-placeholder `VOXDESK_SIP_GATEWAY_TOKEN` are set** (PART 13); otherwise carrier calls arrive at the API's carrier webhooks as before. |
| 2 | **API** (Pipecat runtime) | Python | `app/` (`app/agent/pipeline.py`) | The conversation: STT → LLM → TTS, barge-in, flow graph traversal (`app/builder/flow_runner.py`), tool/function calls, transfer orchestration (`app/telephony/`). This is the brain. |
| 3 | **Realtime gateway** | Go | `services/realtime/gateway-go` | The public WebSocket edge for dashboards and browsers: JWT-gated `hello`, tenant-pinned rooms, ingest fan-out, and point-to-point WebRTC signalling. Helm: `realtimeGateway.enabled`. |
| 4 | **Media engine** | Rust | `services/realtime/media-engine-rs` | The media plane: WebRTC SFU, DTLS-SRTP, Opus jitter buffer, recording sink, transcription tap. Helm: `mediaEngine.enabled`. Compose: `media-engine`. |

Everything else in Python — `app/scheduler`, `app/workers`, outbox dispatch,
billing/metering — is **platform support**, not per-call, and is not part of
this decision.

Entry-point evidence, which is what makes the table above a fact rather than an
assertion:

- `docker-compose.prod.yml` builds `api`, `web`, `realtime-gateway`
  (`./services/realtime/gateway-go`) and `media-engine`
  (`./services/realtime/media-engine-rs`), and sets
  `VOXDESK_CANONICAL_REALTIME: "gateway-go+media-engine-rs"`.
- `infra/helm/voxdesk` renders those two as Deployments (`realtime-gateway`,
  `media-engine`) and `sip-gateway` behind `sipGateway.enabled` (default `false`).
- `grep -rn "services/control-plane\|services/signal-go\|services/media-plane\|services/ops" app/` returns **nothing**. No production code references anything in `experimental/`.

### 2. Everything else is `experimental/`

Moved out of `services/` by this ADR:

| Directory | Was | Now |
|---|---|---|
| `control-plane/` (Rust) | `services/control-plane` | `experimental/control-plane` |
| `signal-go/` (Go) | `services/signal-go` | `experimental/signal-go` |
| `media-plane/` (C++) | `services/media-plane` | `experimental/media-plane` |
| `ops/` (Go) | `services/ops` | `experimental/ops` |

`experimental/` carries a README whose first line is the rule: *not on the call
path, not part of sale claims*. CI still builds and tests these services
(`.github/workflows/polyglot.yml`, jobs renamed to the new paths) because a
broken build is still a broken repository — but those jobs are evidence about
**design**, and are not release-gating.

### 3. Direction of dependency is one-way

Production code may not import, call, or require anything under
`experimental/`. This is enforced, not documented-and-forgotten:
`tests/deployment/test_polyglot_scope.py` walks `app/`, `infra/`, the compose
files and the Helm chart and fails if an `experimental/` path appears as a
runtime dependency. CI may reference it; production may not.

## What it would take to promote a service out of `experimental/`

Promotion is a product decision, and the bar is evidence — not a green test
suite. A service moves onto the call path only when **all five** hold:

1. **It replaces something, not adds to it.** The incumbents are deleted in the
   same change. Two state machines over one call, or two media planes claiming
   the same UDP ports, is a correctness hazard, not redundancy. (Prior art:
   PART 17A deleted `dashboard/` outright rather than leaving two frontends.)
2. **Parity is proven differentially, not by unit tests alone.** The
   `control-plane` work already points the way: its session-parity tests replay
   the same event stream through the Rust machine and the Python one and
   compare outputs. A promotion needs that harness run against real recorded
   call traffic, with zero divergent transitions.
3. **It is in the deployment artefacts.** A Helm template and a compose service,
   including probes, resources, a hardened container context, and the
   fail-closed guards the other templates use — plus it appears in
   `scripts/check_helm.sh`'s rendered-object assertions.
4. **It is in the runbooks and the alerts.** An on-call engineer must be able to
   page on it (`observability/alerts.yml`, synced to the chart by
   `scripts/sync_chart_alerts.sh`) and recover it (`docs/RUNBOOKS/`).
5. **A load number exists.** The PART 11 profiling method (`docs/PERF_TUNING.md`)
   must show the new component is not slower on the path it replaces, measured
   on the real path with the real codecs.

A promoted service also stops being cited as a "polyglot" credential: it becomes
part of the platform, and the platform's claims change to describe it.

## Consequences

**Good.** The tree now answers "what runs a call?" without reading code.
Diligence can check one table instead of sixteen directories. Sales claims map
to deployed artefacts. The four experimental services keep their tests, so the
design work is not thrown away, and their CI jobs still protect the repo.

**Costs.** Paths moved, so `.github/workflows/polyglot.yml`,
`scripts/check_other.sh`, `scripts/polyglot_evidence.py`,
`scripts/build_prompt8b_report.py`, `.github/workflows/security-scan.yml` and
`Makefile` targets were rewritten; old commit links into `services/*` still
resolve historically but not on `HEAD`. `services/README.md` was rewritten to
describe only what remains. `docs/EXPANSION-ROADMAP.md`'s phase language now
describes `experimental/`; the roadmap is a plan, not a claim about `HEAD`.

**Not decided here.** Whether `sip-gateway` should be enabled by default is a
deployment question, not a scope question — PART 13 made it config-gated with
either path functional, and this ADR preserves that.

## References

- `experimental/README.md` — the house rules for that directory
- `services/README.md` — what remains, and why
- `docs/adr/ADR-001-sip-ingress.md` — the SIP ingress decision
- `docs/CURRENT-ARCHITECTURE.md`, `docs/DUE_DILIGENCE/ARCHITECTURE.md`
- `tests/deployment/test_polyglot_scope.py` — the enforcement
- `docker-compose.prod.yml`, `infra/helm/voxdesk/` — the deployed truth
