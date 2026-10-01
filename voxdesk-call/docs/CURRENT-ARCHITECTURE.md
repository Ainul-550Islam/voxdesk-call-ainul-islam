# Current Architecture — Canonical Production Topology (P0-01, P0-07 Fix)

## Frontend Source-of-Truth (P0-01)

**CANONICAL PRODUCTION FRONTEND:** `dashboard/` (Vite + React, JSX)
- Built in Dockerfile stage `dashboard-build` via `npm run build`
- Copied to `/srv/dashboard/dist` in runtime image
- Served by FastAPI in `app/main.py::_mount_dashboard_if_built()` at `/` with SPA fallback
- CI gate: `.github/workflows/ci.yml` job `frontend` (npm ci, npm test, npm run build)
- E2E coverage: `dashboard/tests/` (17 test files)

**SHADOW / ROADMAP FRONTEND:** `dashboard-next/` (Next.js 14 + TypeScript)
- Status: Increment-by-increment strangler migration per `docs/EXPANSION-ROADMAP.md` Phase 1
- CI gate: `.github/workflows/polyglot.yml` job `dashboard-next` (npm ci, npm test, tsc --noEmit, build)
- NOT built in production Dockerfile, NOT served by production compose
- Promotion criteria: Every page in migration checklist (see dashboard-next/README.md) must pass parity checks
- When promoted, Dockerfile will switch to Next.js build and `app/main.py` will mount `.next/standalone`

```
Browser
  │
  └── Caddy (:80/:443)
       ├── /realtime/ws → Go realtime gateway (:8790) [CANONICAL]
       └── everything else → FastAPI API (:8000) → serves dashboard/dist [CANONICAL]
```

## Realtime / Media Topology (P0-07)

**CANONICAL PRODUCTION PATH:**
- `services/realtime/gateway-go` (Go 1.27) — WebSocket edge, JWT verification, presence, signaling router
- `services/realtime/media-engine-rs` (Rust) — SFU/media engine, UDP :5000 public, control :9001 internal
- Wired in `docker-compose.prod.yml`: api → realtime-gateway → media-engine
- Health: gateway health reflects engine availability

**SHADOW / DIFFERENTIAL / ROADMAP PATHS (NOT in production compose):**
- `services/signal-go` (Go 1.27) — differential signaling hub, counterpart to Rust control-plane signal crate, CI-tested in polyglot.yml
- `services/control-plane` (Rust 1.90 per CI, README previously said 1.98+ — fixed to 1.90) — roadmap Phase 2, session/broadcast/ratelimit/registry/retry/scheduler/usage, shadow-parity path
- `services/media-plane` (C++17) — DSP foundation (jitter_buffer, consistent_hash_router, fft, spectral_denoise, g711, vad), NOT full production WebRTC stack per its own README (missing MediaControl RPC, streaming denoiser wrapper, Opus, DTLS-SRTP, congestion control)

```
Canonical: Browser --WebSocket--> gateway-go --control--> media-engine-rs --UDP--> Browser (ICE)
Shadow:    Browser --WebSocket--> signal-go (differential)
           Rust control-plane (roadmap shadow parity)
           C++ media-plane (DSP primitives)
```

## Organization Access (Preserved Existing Logic)

Organization is the parent of one or more tenants. Each tenant has environments
(development, staging, production). A user still has exactly one `users.tenant_id`.
Membership rows record that binding so it can be suspended or revoked without
deleting the user or flipping `is_active`.

Effective access is resolved server-side:

1. Explicit revoked or suspended membership denies, suspended for privileged
   actions only.
2. An explicit environment membership, capped so it cannot outrank the tenant role.
3. The tenant membership.
4. An organization owner or admin, for tenants in that organization only.
5. Otherwise deny. A missing membership row is the legacy single-tenant path.

`organization_id` and `environment_id` are not JWT claims. The current
environment, when one is selected, is a server-side row.

Roles are the existing `owner` / `admin` / `manager` / `agent` / `viewer`
values. Conceptual names such as `organization_owner` are aliases only.
Permissions are the existing `Permission` enum. There is no second RBAC engine.

Quota resolution reads organization, tenant and environment rows and the
existing billing entitlement. A hierarchy value may tighten a billing cap. It
may not raise one. A missing limit is unknown, not zero.

Identity policy remains the base. Scope overlays may only make mandatory
controls stricter. Child policies cannot weaken a parent MFA, SSO, password,
API-key or service-account restriction.

## Backend / API

- FastAPI `app/main.py` (0.4.0) — 593 route decorators across 78 modules per audit
- PostgreSQL 16 + Redis 7 + scheduler (same image as API, runs `scripts.scheduler`)
- Caddy 2.9 as TLS terminator, mounts dashboard/dist via API
- Python 3.12, AST parse 1071/1071 pass

## Polyglot Services Summary

| Service | Language | Version | Production? | Role |
|---|---|---|---|---|
| gateway-go | Go | 1.27 | YES | WebSocket edge |
| media-engine-rs | Rust | 1.90 | YES | SFU/media |
| signal-go | Go | 1.27 | NO (shadow) | Differential signaling |
| control-plane | Rust | 1.90 | NO (roadmap) | Shadow parity control |
| media-plane | C++17 | g++ 14 | NO (foundation) | DSP primitives |
| ops (voxops) | Go | 1.27 | NO (tooling) | Backup/ops |

## Verification

- `docker-compose.prod.yml` env markers: VOXDESK_CANONICAL_FRONTEND, VOXDESK_CANONICAL_REALTIME
- `app/main.py` logs: dashboard.mounted with canonical/shadow flags
- CI: `ci.yml` builds Vite frontend + API image + gateway image
- Polyglot: `polyglot.yml` builds Next, control-plane, signal-go, ops-go, media-plane
- No silent degradation: REALTIME_GATEWAY_INGEST_SECRET required interpolation fails deploy up front

## Future Promotion

When dashboard-next achieves full parity:
1. Update Dockerfile to build dashboard-next instead (or both with flag)
2. Update app/main.py to mount Next.js output
3. Update docker-compose.prod.yml markers
4. Update this doc and docs/DASHBOARD.md
5. Add E2E for promoted frontend
