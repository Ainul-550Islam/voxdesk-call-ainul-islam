# Production-readiness audit — 2026-10-10

Scope: `voxdesk-call/` in `Ainul-550Islam/voxdesk-call-ainul-islam`, base commit `a76cb82`.
Raw logs were kept outside the repository in `/home/user/audit_logs/`. Passing tests show
what was exercised, not the absence of defects. Behavior-changing fixes are listed in
`CHANGELOG.md` under `[Unreleased]`.

## Results

| Area | Result | Evidence |
|---|---|---|
| Python test suite (chunked, 16 chunks, round 6 final) | **4,711 passed, 0 failed, 0 errors, 0 timeouts, 0 crashes**; 4 skipped; 23 deselected by marker | `audit_logs/chunks_r6_final.log` |
| Targeted security regressions (`tests/security/test_telephony_webhook_fail_closed.py`, `tests/security/test_loopback_probe_trusted_host.py`, `tests/truth/test_no_template_routes.py`) | 14 passed | re-run this session |
| Config regression (`tests/test_config.py::test_production_without_telephony_webhook_secret_is_refused`) | 1 passed; `tests/test_config.py` together with the two security files: 27 passed | re-run this session |
| Repo gates (null bytes, filler, fake success, dependencies, contracts, ruff) | rc=0 | `r3_gates.txt`, `gate_*_rebuilt.log` |
| Migrations (PostgreSQL round trip, `alembic check`) | pass; one head | `alembic_check_rebuilt.log` |
| Dashboard (Node 20): `npm ci`, build, vitest | rc=0; vitest 50 files, 562 tests | `dashboard_*_rebuilt.log`, `r3_dash.txt` |
| Go gateway `services/realtime/gateway-go` | `go vet` rc=0; `go test -race` 186 cases, pass | `go_test_r3.jsonl`, `go_vet_r3.log` |
| Go signal-go and ops | `go vet` rc=0; tests 37 and 43 pass | `r3_go.txt` |
| Rust control-plane (`services/control-plane`) | `cargo test`: 59 passed, 0 failed (6 binaries); `cargo fmt --check` rc=0 | `rust_control_test.log`, `rust_status.txt` |
| Rust media engine (`services/realtime/media-engine-rs`) | fmt rc=0; **test, clippy and image build blocked** by missing `vendor/dimpl` (Defect 1) | `rust_media_test.log`, `rust_media_clippy.log`, `docker_build_media.log` |
| Docker: API image (`Dockerfile`, repo root) | builds (rc=0). Container healthy under HEALTHCHECK; `/health` 200 with `TRUSTED_HOSTS=127.0.0.1,localhost`; `/api/calls` without credentials 401 | `docker_build_api.log`, container checks this session |
| Docker: gateway image | builds (rc=0) | `docker_build_gateway.log` |
| Docker: media-engine image | **fails**: `"/vendor": not found` (Defect 1) | `docker_build_media.log` |
| Production fail-closed start (`APP_ENV=production`, no telephony secret, placeholder config) | refused to start: `RuntimeError: runtime configuration validation failed: deepgram_configuration_missing, elevenlabs_configuration_missing, llm_configuration_missing, security_configuration_invalid…` | `prod_notel.log` |
| Compose (`docker-compose.yml`, `.prod`, `.staging`, `.full`, `.check` overlays) | `docker compose config -q` rc=0 with temporary env files (removed) | session log |
| Caddyfile | `caddy validate` rc=0 with compose defaults and with real hostnames. Empty `DOMAIN` is rejected (fixed, Defect 4). **Live run (round 6):** Caddy 2.6.2 in front of uvicorn, with test-only upstream addresses: `/health` 200; `/api/calls` 401 from the app; WebSocket `/telephony/ws` `101 Switching Protocols` passed through; `/realtime/ws` with the gateway down gives Caddy's 502; a foreign Host never reached the app (Caddy answers an empty 200 for unmatched hosts) | round 6 |
| Load test (Locust, 20 users, 5/s spawn, 60 s, `/telephony/ws` included) | 534 requests, **0 failures**; WebSocket 86 requests, 0 failures | `locust_api_run.log`, `locust_api_*.csv` |
| Voice capacity (10 and 20 concurrent calls, 2 turns) | completed; e2e p95 294.9 ms at 10, 314.7 ms at 20; knee at 20 calls; recommended HPA 16 calls/worker | `voice_capacity_small.json` |
| Bot detection, device fingerprinting, IP reputation, velocity limits, anti-sybil, behavioral traps | **not implemented** (see Security controls) | code search and live test below |

Not run: full 10–60 call voice ramp, Rust media-engine tests and clippy, `cargo audit`, full
`docker compose up` with the media engine, Caddy in front of a running API, the 27 deselected
`live` / `real_provider` / `docker` tests, real Deepgram / ElevenLabs / LLM / Twilio calls.

## Security controls (requirement 3)

Code search across `app/` plus a live request test:

- **Bot detection**: no implementation.
- **Device fingerprinting**: no implementation. Matches are deployment fingerprints, not device identity.
- **IP reputation**: no implementation.
- **Velocity limits**: implemented for sign-in (round 4).
  - **Per-IP login budget (new).** `POST /auth/login` spends `login_ip_limit_per_minute` (default 30 per 60 s)
    through the shared identity limiter before any account lookup. Over budget: 429, `Retry-After: 60`, and the
    structured log event `auth.login.ip_rate_limited`. Redis-backed when `REDIS_URL` is set; in-process otherwise.
    Before this change, 12 attempts for an unknown account from one client all returned 401 (live check).
  - Public widget (`app/services/public_widget_service.py`, `_enforce_widget_rate_limit`) now spends its per-key
    and per-IP budgets in the shared identity limiter (round 5), so limits hold across replicas. Web call
    (`app/telephony/web_call.py`, `_enforce_rate_limit`) returns HTTP 429 when exceeded.
  - Account lockout after `max_failed_logins` (default 8, `app/auth/service.py`) applies only to existing accounts.
- **Anti-sybil**: no implementation.
- **Behavioral traps (honeypots)**: no implementation.

Because these controls are absent, there are no blocking logs to verify. Implementing them is a product
and design decision.

## Defects found and status

1. **`vendor/dimpl` is not in the repository; the Rust media engine cannot build. Open, decision recorded: left documented as blocked.** Rebuilding the patch would change DTLS-SRTP negotiation, so it was not done without the original tree.
   `Cargo.toml` patches `dimpl` to `vendor/dimpl`, and `crates/dtls/src/lib.rs` calls
   `ConfigBuilder::srtp_profiles`, which exists only in the patched copy. `.gitignore` ignores `vendor/`,
   and no commit in the history contains it. Not fixed: needs the original vendored tree or an approved patch.
   Replacing it with the upstream crate would change DTLS-SRTP negotiation.
2. **Tracked secrets in `.env.check`. Fixed in tree; values must be rotated.** The file held populated
   `SECRET_KEY`, `JWT_SECRET`, owner passwords, and gateway and webhook secrets. Removed from the tree and
   ignored via `.gitignore`. The values remain in git history.
3. **Undeclared dependency. Fixed (round 6).** `loadtest/voice_ws_user.py` imports `audioop`, removed in Python
   3.13. `requirements-test.txt` now pins `audioop-lts==0.2.2` on 3.13 only.
4. **Caddy crash-loop with empty `DOMAIN`. Fixed.** `docker-compose.prod.yml` and `docker-compose.staging.yml`
   passed `DOMAIN: ${DOMAIN:-}` (and `GRAFANA_DOMAIN`). An empty value is rejected by Caddy. Defaults are now
   `http://localhost` and `http://grafana.localhost`.
10. **Media stream handshake raised a second close after client disconnect. Fixed (round 6).** See CHANGELOG and `tests/telephony/test_media_stream_disconnect.py`.
5. **No per-source login velocity limit. Fixed (round 4).** See Security controls and `tests/security/test_login_ip_velocity.py`.
6. **Public widget rate limits were per process. Fixed (round 5).** Now shared via the identity limiter; Redis outage refuses widget requests (deliberate, see CHANGELOG).
7. **Placeholder and dead code removed. Fixed.** 94 NUL-filled route modules, an unmounted analytics route, 15
   duplicate HTML mockups, and retired engine directories (`realtime-engine/`, `voxdesk-concurrency-engine/`,
   `voxdesk-native-audio-engine/`) were removed. Recoverable from `a76cb82`.
8. **Telephony webhook default secret and prod boot. Fixed.** See CHANGELOG `[Unreleased]`.
9. **Migration graph had 5 heads. Fixed.** See CHANGELOG.

## Workspace note

Round 4: the working copy was found damaged (no `.git`, missing files such as `app/api/auth_routes.py`,
and the Python venv's interpreter symlinks gone). It was moved to `voxdesk-call-ainul-islam.damaged-restore`
(kept, not deleted) and rebuilt from a fresh clone of `a76cb82` with `audit_tools/reapply.py`. The venv was
recreated from the system Python 3.13 with `requirements-test.txt`. PostgreSQL 17 and Redis were reinstalled
via apt.

The working copy was rebuilt from a fresh clone of `a76cb82` more than once during this audit. The rebuild is
scripted by `audit_tools/reapply.py` (exact-match edits plus a payload). The most recent rebuild reproduces the
working tree file for file, including this document.

## Reproduce

```bash
git clone https://github.com/Ainul-550Islam/voxdesk-call-ainul-islam.git && cd voxdesk-call-ainul-islam && git checkout a76cb82
python3 /path/to/audit_tools/reapply.py "$PWD" /tmp/audited    # applies all audit changes
cd /tmp/audited/voxdesk-call
python3 -m venv ~/venv && ~/venv/bin/pip install -r requirements-test.txt
# Provide DATABASE_URL for a PostgreSQL instance you control. Do not commit it. REDIS_URL unset.
~/venv/bin/python scripts/pytest_chunks.py --chunk-size 25 --per-chunk-timeout 900 --per-test-timeout 150
~/venv/bin/python -m pytest -q tests/security tests/test_config.py tests/truth/test_no_template_routes.py
cd services/realtime/gateway-go && go vet ./... && go test -race -count=1 ./...
cd ../../control-plane && cargo test --workspace
caddy validate --config Caddyfile --adapter caddyfile
DOMAIN=http://localhost GRAFANA_DOMAIN=http://grafana.localhost caddy validate --config Caddyfile --adapter caddyfile
```
