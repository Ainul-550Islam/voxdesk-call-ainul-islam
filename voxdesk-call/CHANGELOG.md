# Changelog

All notable changes to the VoxDesk Voice AI Platform (`voxdesk-call`) are
documented in this file. Release notes contain only measured, test-verified
capabilities (`docs/SALES/FEATURE_MATRIX_VERIFIED.md`) and never claim
unverified provider connections or third-party compliance certifications.

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
