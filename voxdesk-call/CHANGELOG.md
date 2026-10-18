# Changelog

All notable changes to the VoxDesk Voice AI Platform (`voxdesk-call`) are
documented in this file. Release notes contain only measured, test-verified
capabilities (`docs/SALES/FEATURE_MATRIX_VERIFIED.md`) and never claim
unverified provider connections or third-party compliance certifications.

---

## [Unreleased] — 2026-10-18 (Round 8: media-stream close hardening)

### Fixed
- **`media_stream` close after a malformed frame or a completed response.** A non-JSON `start`
  frame, a binary frame (`receive_text` raises `KeyError("text")`), or a non-object payload
  escaped the handler after `accept()`. Those cases now close once with 1008. All closes in the
  handler go through `_safe_close()`, which swallows `RuntimeError` and `WebSocketDisconnect` raised
  when the ASGI response has already completed. Verified live against the app via the test client
  (no-token start, immediate disconnect, bad JSON, binary frame: each closes 1008, no ASGI
  `RuntimeError`). Regression tests: `tests/telephony/test_media_stream_disconnect.py` (+4 tests).
  Test run: `tests/telephony tests/security tests/test_stream_lifecycle.py` → 501 passed, 2 skipped
  (`audit_logs/telephony_security_r8.log`). Full-file payload: `audit_tools/payload/twilio_handler.py`.

## [Unreleased] — 2026-10-10 (Production-readiness audit)

### Behavior-changing fixes (review separately)
- **Per-source login velocity limit.** `POST /auth/login` had only a per-account lockout, so one
  client could try many accounts without a throttle (live check before the change: 12 attempts,
  all 401). The handler now spends a per-IP budget (`LOGIN_IP_LIMIT_PER_MINUTE`, default 30 per
  60 s) through the shared identity limiter before any account lookup. Over budget returns 429
  with `Retry-After: 60` and logs `auth.login.ip_rate_limited`. The budget is in Redis when
  `REDIS_URL` is set, so it holds across replicas, and in-process otherwise. Regression:
  `tests/security/test_login_ip_velocity.py` (3 tests; the two blocking tests fail with the check
  disabled). `tests/conftest.py` clears the identity limiter state, including the `voxdesk:rate:v1:identity:*` keys in the Redis a test used, so counters do not leak between tests.
- **Public widget budgets are shared across replicas.** The widget limiter kept counts in a
  per-process dictionary, so N API replicas allowed N times the budget. Its per-key and per-IP
  budgets now go through the shared identity limiter (`allow_identity_action`): Redis when
  `REDIS_URL` is set, in-process otherwise. Trade-off, chosen deliberately: as with the other
  identity limits, an unavailable Redis refuses widget requests (429, `rate_limit.backend_unavailable`
  logged) rather than allowing them. To change this, edit `_enforce_widget_rate_limit` in
  `app/services/public_widget_service.py`. Regression: `tests/security/test_widget_shared_rate_limit.py`.
- **Declared the `audioop` dependency of the load tests.** `loadtest/voice_ws_user.py` imports
  `audioop`, removed in Python 3.13. `requirements-test.txt` now pins `audioop-lts==0.2.2` for
  Python 3.13 only (`python_version >= "3.13"`). Python 3.12 has `audioop` in the standard library.
  Verified: `scripts/verify_dependencies.py` passes and `loadtest.voice_ws_user` imports on 3.13.
- **Media stream handshake: no second close after a client disconnect.** A client that dropped
  during the Twilio media-stream handshake raised `WebSocketDisconnect`, and the handler then called
  `websocket.close()` on the closed socket. The ASGI server logged a `RuntimeError`
  ("response already completed"). Disconnects now return quietly. Handshake timeouts still close
  with 1008. Found by the live Caddy check. Regression: `tests/telephony/test_media_stream_disconnect.py`
  (fails on the old handler).
- **Caddy crash-loop with empty DOMAIN.** `docker-compose.prod.yml` and
  `docker-compose.staging.yml` passed `DOMAIN: ${DOMAIN:-}` (and `GRAFANA_DOMAIN`). An empty value
  expands to an empty site address, and `caddy validate` rejects it (`server block without any key
  is global configuration`). The compose defaults are now `http://localhost` and
  `http://grafana.localhost`, the same fallbacks the Caddyfile uses. Verified with `caddy validate`
  on the rebuilt tree: defaults and real hostnames pass; an empty `DOMAIN` still fails by design.
- **Telephony webhook fail-closed.** The public default secret
  `voxdesk-telephony-webhook-secret` is removed. Webhooks are rejected with
  `ProviderWebhookVerificationError` when `TELEPHONY_WEBHOOK_SECRET` is unset, and the
  default is no longer a fallback candidate when a secret is set. Production refuses
  placeholder values. Regression: `tests/security/test_telephony_webhook_fail_closed.py`.
- **Migration graph fixed.** Ten legacy duplicate-number migrations were deleted (the
  canonical short-named files carry identical operations; `app/db/migration_compatibility.py`
  maps them). The graph had 5 Alembic heads, so `alembic upgrade head` (run by the
  production entrypoint) would fail. It now has one head, `0062_drop_pcap_artifacts`.
  Verified: PostgreSQL upgrade → downgrade base → upgrade round trip, and `alembic check`
  reports no drift.
- **Production healthcheck.** `/health` returned 400 under `TRUSTED_HOSTS` because the
  Dockerfile probe sends `Host: 127.0.0.1:8000`. `app/middleware/loopback_probe.py`
  replaces `TrustedHostMiddleware` in `app/main.py`. It exempts only `GET`/`HEAD /health`
  from a `127.0.0.1` or `::1` peer. All other paths and hosts stay enforced. Regression:
  `tests/security/test_loopback_probe_trusted_host.py`.
- **Compose secret wiring.** `docker-compose.prod.yml` requires `TELEPHONY_WEBHOOK_SECRET`
  (`:?` fail-closed interpolation). `docker-compose.staging.yml` passes it with an empty
  default. `.env.example` documents it.
- **Deleted placeholder code.** 94 NUL-filled route modules (truncated, unloadable), the
  unmounted `app/api/advanced_analytics_routes.py`, 15 duplicate dashboard HTML mockups
  under `dashboard/src/pages/**`, and the directories `realtime-engine/`,
  `voxdesk-concurrency-engine/`, and `voxdesk-native-audio-engine/` (the repository's
  `verify_no_filler` gate forbids them). Recoverable from git history (`a76cb82`).
- **Stale artifacts removed from the repo root** (no tests or gates read them):
  two `.zip` evidence bundles, `ROUND4-LINT-CLEANUP.patch.gz`, `FINAL_TEST_RESULTS.json`,
  `PROMPT8B_FINAL_REPORT.md`, `PROMPT8_FINAL_REPORT.md`, `BACKEND_SUITE_REPAIR_REPORT.md`,
  and `tests_manifest.txt`. Recoverable from git history.

### Build, CI, and tests
- Added `requirements-test.txt` with pinned test tooling (pytest, pytest-asyncio,
  pytest-timeout, aiosqlite, httpx, respx, fakeredis, ruff). CI installs it.
- CI installs `redis-server` and sets `DATABASE_URL` to the Postgres service
  (`voxdesk_ci`) for the Tests step.
- `requirements.txt` declares `psutil==7.2.2`. `scripts/boot_probe.py` imports it, and
  `verify_dependencies` failed without it.
- Registered the `timeout` pytest marker in `pytest.ini`.
- `tests/truth/test_no_template_routes.py` now derives its expected route count from
  whether the dashboard build exists. The `/assets` mount and the SPA fallback route are
  mounted only when `dashboard/dist` is present.
- Added `tests/webhook_secret.py` (test-only secret, never used in production code).

### Security notes
- **Tracked secret file removed.** `.env.check` was committed with populated values
  (`SECRET_KEY`, `JWT_SECRET`, owner/demo passwords, gateway ingest and webhook secrets).
  The file is generated by `scripts/make_check_env.py` (mode 0600) and is now untracked and
  ignored via `.gitignore`. **Every value in that file must be treated as exposed and rotated**,
  because it remains in git history.
- Production refuses to boot without `TELEPHONY_WEBHOOK_SECRET`; covered by
  `tests/test_config.py::test_production_without_telephony_webhook_secret_is_refused`.
- No hardcoded production credentials were introduced. Test secrets are test-only constants.

---

## [1.0.0] — 2026-10-08 (First Truthful Sell-Ready Release — Gates G0–G9)

### Gate G0 — Truth & Anti-Facade Foundation (PART 0)
- Added CI truth guards (`make verify-truth`): `scripts/verify_no_filler.py`,
  `scripts/verify_no_fake_success.py`, `scripts/verify_no_null_bytes.py`,
  `scripts/verify_retired_references.py`, `scripts/strip_padding_markers.py`,
  and `scripts/strip_generated_tails.py`.
- Emptied `scripts/fake_success_allowlist.txt` (`0` bytes); every route either
  performs its real side effect or returns an honest `501 NOT_CONFIGURED` /
  `UNSUPPORTED_CAPABILITY` / `409 PENDING_PROVIDER`.
- Added `scripts/generate_feature_matrix.py` and `tests/truth/feature_manifest.yaml`
  requiring passing JUnit XML test node evidence before any feature can be listed
  as `LIVE`.

### Gate G1 — Evented Platform, Post-Call Intelligence & Retention (PART 1A, 1C, 1D, 1E)
- **Webhooks (1A)**: Real transactional outbox + HMAC-SHA256 signed delivery
  (`X-VoxDesk-Signature`), SSRF DNS validation, exponential retry, dead-letter
  queue (DLQ) redrive, and all 12 lifecycle events (`call_started`, `call_ended`,
  `call_analyzed`, `transfer_*`, `voicemail_detected`, `dtmf_received`,
  `recording_ready`, `batch_*`, `agent_published`).
- **Post-Call Analysis (1C)**: Automatic post-call extraction (`summary`,
  `sentiment`, `call_successful`, typed custom fields) triggered on call
  termination, firing `call_analyzed` upon completion.
- **Automated QA (1D)**: Post-call QA sampling rules (`percentage`,
  `negative_sentiment`, `transfers`, `long_calls`), rubric scoring, evidence
  citations, and coaching signals (`app/qa/`).
- **Retention & PII Redaction (1E)**: Per-agent and per-tenant TTL purges
  (`app/core/retention.py`), legal-hold exemptions, two-party recording consent
  disclosures, and real-time PII redaction + audio buffer zeroing during mute
  windows.

### Gate G2 — Outbound Batch & Power Dialer (PART 1B)
- Real outbound batch campaign scheduler (`app/services/batch_dialer.py`,
  `app/telephony/dialer_limits.py`) with E.164 normalization, DNC suppression,
  recipient timezone calling windows, Redis token-bucket CPS pacing, AMD
  voicemail handling, and `SELECT ... FOR UPDATE SKIP LOCKED` concurrency safety.

### Gate G3 — Voice Runtime, Latency Proof & Call Control Tools (PART 2)
- **Latency Instrumentation (2A)**: Per-turn `LatencyObserver` measuring STT,
  LLM TTFT, TTS TTFB, network RTT, and total E2E turn latency (`CallLatencyStat`,
  Prometheus histograms, `scripts/bench_latency.py`, `docs/LATENCY_BENCHMARK.md`).
- **Smart Turn-Taking & Failover (2B, 2C)**: Configurable endpointing,
  interruption sensitivity, backchannels, idle reminders, and automatic
  circuit-breaker failover across STT (Deepgram, AssemblyAI, Whisper, Azure,
  Google), LLM (OpenAI, Anthropic, Gemini, Azure, Groq, Together), and TTS
  (ElevenLabs, OpenAI, Cartesia, Deepgram Aura, PlayHT, Azure).
- **Voice Controls & Call Tools (2D, 2E, 2F)**: Pronunciation dictionaries,
  5 synthesized ambient sound beds, `NoisereduceFilter`, `end_call`, `send_dtmf`,
  `transfer_call` (cold/warm with whisper briefing + AI recovery),
  `agent_handoff`, `navigate_ivr`, and phone-number-to-published-agent-version
  binding.

### Gate G4 — Browser Web Calls, Embeddable Widget & Web/Node/Python SDKs (PART 3)
- Browser WebSocket media transport (`/api/v1/telephony/web-calls/{call_id}/ws`),
  single-use ephemeral token minting, strict origin allowlists, embeddable Shadow
  DOM widget (`widget/`), and TypeScript/Python SDKs (`sdk/js`, `sdk/node`,
  `sdk/python`).

### Gate G5 — Live Call Monitoring, Supervisor Whisper & Takeover (PART 4)
- Zero-overhead `MonitorBus` (`app/telephony/monitor_bus.py`) and Pipecat
  `MonitorTapProcessor`, supervisor live listen WebSocket, whisper guidance
  injection into the active LLM context, and live call takeover via Twilio REST
  call redirect with automatic rollback on failure (`app/telephony/takeover.py`).

### Gate G6 — Visual Conversation-Flow Builder & Runtime (PART 5)
- Typed 6-node conversation-flow graph (`conversation`, `function`, `transfer`,
  `press_digit`, `branch`, `end`), deterministic equation evaluator + bounded
  LLM transition judge (`app/builder/flow_runner.py`), `FlowProcessor` mid-call
  prompt/tool swapping, and React Flow visual editor in `dashboard/` and
  `dashboard-next/`.

### Gate G7 — Simulation Testing, Regression Suite, A/B Routing & Custom Dashboards (PART 1G, 6)
- Multi-turn `SimulatedCaller` (`app/services/simulation_caller.py`) against
  pinned `AgentVersion` snapshots, one-click promotion of failed calls into PII-
  redacted regression test cases, live inbound A/B traffic splitting with two-
  proportion z-test winner promotion, and tenant-scoped custom analytics
  dashboards with scheduled CSV email delivery.

### Gate G8 — Real Salesforce & CRM Integrations + Automation Connectors (PART 1F, 6)
- Real Salesforce OAuth 2.0 + PKCE flow, AES-GCM encrypted refresh tokens,
  automatic 401 token refresh, SOQL escaping, Contact/Lead upsert, Task/Case/
  Opportunity creation (`app/integrations/crm/providers/salesforce.py`), plus
  HubSpot, GoHighLevel, Jobber, Zoho, and Pipedrive adapters and durable
  automation connectors.

### Gate G9 — Enterprise Identity, Reliability/Scale Proof & Due-Diligence Packaging (PART 7, 8, 9)
- **Enterprise Identity (PART 7)**: OIDC + PKCE and SAML 2.0 SSO verified against
  Keycloak reference IdP (`infra/keycloak/`), SCIM 2.0 RFC 7643/7644 user/group
  provisioning with immediate session revocation on deprovision, KMS secret
  rotation, and STIR/SHAKEN number trust profiles (`0061_number_trust_profile`).
- **Reliability & Scale Proof (PART 8)**: Deleted unimplemented PCAP facade and
  dropped `pcap_artifacts` table (`0062_drop_pcap_artifacts`), added graceful
  call drain (`app/core/graceful_shutdown.py`), Helm HPA on `voxdesk_active_calls`
  + PDB, chaos fault injection tests (`tests/resilience/test_chaos_calls.py`),
  measured single-worker capacity model (`docs/CAPACITY_MODEL.md`), and scripted
  PostgreSQL backup/restore DR drill (`scripts/dr_drill.sh`, measured RPO `0.35s`,
  RTO `2.23s`).
- **Packaging & Sales Proof (PART 9)**: Added CycloneDX SBOM generation (`sbom/`),
  static OpenAPI documentation (`scripts/build_api_docs.sh` -> `docs/api/`),
  complete due-diligence documentation (`docs/DUE_DILIGENCE/`), verified sales
  collateral (`docs/SALES/`), release workflow (`.github/workflows/release.yml`),
  and `make verify-sale` producing `dist/due-diligence-<date>.zip` with
  `SHA256SUMS`.
