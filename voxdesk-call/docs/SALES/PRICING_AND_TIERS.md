# VoxDesk — Pricing Tiers, Sell Gates & Handoff Terms

This document defines the three commercial acquisition / licensing bands (**Band A**,
**Band B**, and **Band C**) for the VoxDesk Voice AI Platform (`voxdesk-call`),
mapped strictly to the verified **Sell Gates (`G0`–`G9`)** and the verified
feature rows in [`docs/SALES/FEATURE_MATRIX_VERIFIED.md`](FEATURE_MATRIX_VERIFIED.md).

> **Governance Note**: The dollar bands below represent the repository owner's
> target valuation ranges (`$30K–$60K`). Gates define **which technical claims
> are truthful and backed by passing test evidence**. No capability outside the
> verified `LIVE` rows of `FEATURE_MATRIX_VERIFIED.md` is included or implied.

---

## 1. Gate-to-Evidence Mapping (`G0`–`G9`)

| Gate | Scope Summary | Closed By | Verification Evidence in Repository | Gate Status |
|---|---|---|---|---|
| **G0: Truth** | Zero filler, clones, facades, padding, or fake success; shrink-only allowlist empty (`0` bytes); CI truth guards. | PART 0 | `make verify-truth` (`61 passed`); `reports/PART_0_REPORT.md` | **GREEN** |
| **G1: Evented Platform** | Signed call lifecycle webhooks (`12` event types) via transactional outbox + DLQ redrive; automatic post-call analysis; automated QA review; TTL retention + PII redaction. | PART 1A, 1C, 1D, 1E | Matrix `#25`, `#26`, `#27`, `#36`; `reports/PART_1_REPORT.md` | **GREEN** |
| **G2: Outbound** | Batch/power dialer with E.164 validation, DNC suppression, recipient timezone windows, Redis CPS pacing, AMD voicemail handling, and `SKIP LOCKED` single-dial guarantee. | PART 1B | Matrix `#17`, `#21`; `tests/campaign/test_batch_dialing.py`, `tests/campaign/test_concurrency.py` | **GREEN** |
| **G3: Runtime Proof** | Per-turn `LatencyObserver`, smart turn-taking & backchannels, STT/LLM/TTS circuit-breaker failover, DTMF/end-call/transfer/IVR tools, ambient audio/denoise, and number->agent version binding. | PART 2 | Matrix `#1`, `#2`, `#4`–`#7`, `#9`–`#17`; `docs/LATENCY_BENCHMARK.md`; `reports/PART_2_REPORT.md` | **GREEN** |
| **G4: Web Calling** | Browser web-call WebSocket media transport, single-use ephemeral token minting, origin-allowlisted Shadow DOM widget, and Web/Node/Python SDKs. | PART 3 | Matrix `#22`, `#37`; `tests/telephony/test_web_call_flow.py`; `reports/PART_3_REPORT.md` | **GREEN** |
| **G5: Live Ops** | Zero-overhead `MonitorBus` live listen WebSocket, supervisor whisper-to-AI guidance injection, and live Twilio REST call takeover with rollback. | PART 4 | Matrix `#31`; `tests/telephony/test_takeover.py`, `tests/api/test_live_monitoring_authz.py`; `reports/PART_4_REPORT.md` | **GREEN** |
| **G6: Flow Builder** | Visual 6-node conversation-flow builder (`conversation`, `function`, `transfer`, `press_digit`, `branch`, `end`), structural validator, `FlowRunner`, and Pipecat `FlowProcessor`. | PART 5 | Matrix `#8`; `tests/builder/test_flow_runner.py`; `reports/PART_5_REPORT.md` | **GREEN** |
| **G7: Quality Loop** | Multi-turn `SimulatedCaller`, one-click regression test creation from failed calls, live inbound A/B traffic splitting with z-test winner promotion, and custom analytics dashboards. | PART 1G, 6 | Matrix `#28`, `#29`, `#30`, `#32`; `reports/PART_6_REPORT.md` | **GREEN** |
| **G8: Integrations** | Real Salesforce OAuth 2.0 + PKCE + SOQL + Task/Case/Opportunity writeback, HubSpot/GHL/Jobber adapters, calendar booking, durable automation connectors, and SDKs. | PART 1F, 3, 6 | Matrix `#33`, `#34`, `#37`, `#38`; `tests/integrations/test_salesforce_provider.py` | **GREEN** |
| **G9: Enterprise & Scale** | OIDC + PKCE and SAML 2.0 SSO (verified against Keycloak), SCIM 2.0 provisioning + session revocation, number trust profiles, single-worker capacity model, chaos tests, DR drill, and due-diligence pack. | PART 7, 8, 9 | Matrix `#18`, `#20`, `#36`, `#39`; `docs/CAPACITY_MODEL.md`, `evidence/dr/dr_drill_report.json`, `dist/due-diligence-*.zip` | **GREEN** |

---

## 2. Commercial Tier Definitions (`Band A`, `Band B`, `Band C`)

### Band A — Core Multi-Tenant Voice Platform (`≈ $30,000`)
- **Required Gates**: `G0 + G1 + G2 + G3` (all green)
- **Included Verified Capabilities (`FEATURE_MATRIX_VERIFIED.md` IDs)**:
  - **Telephony & Voice Pipeline**: `#1` End-to-end latency instrumentation, `#2` Smart turn-taking & interruption controls, `#4` Multi-provider STT/TTS failover, `#5` Voice catalog & cloning, `#6` Voice controls (speed, volume, pronunciation, boosted keywords, ambient sound, denoise), `#7` Multilingual routing, `#9` Immutable agent versions & rollback, `#10` Phone-number-to-agent binding.
  - **Mid-Call Tools & Call Control**: `#11` Custom HTTP tools (SSRF-guarded), `#12` MCP tools, `#13` Cold/warm transfer + whisper briefing, `#14` Agent-to-agent handoff, `#15` IVR navigation, `#16` DTMF, `#17` Voicemail detection, `#18` Number provisioning & SIP trunking, `#23` SMS/chat agents, `#24` Knowledge base (RAG), `#34` Calendar booking, `#40` Billing & metering, `#41` Contact memory.
  - **Outbound & Post-Call**: `#21` Outbound batch/power dialer, `#25` Signed lifecycle webhooks + DLQ redrive, `#26` Automatic post-call analysis, `#27` Automated QA review.
- **License Model**: Commercial Source License (`LICENSE` Option B) for single-organization deployment.

### Band B — Omnichannel Web, Live Supervisor Ops & Quality Loop (`≈ $45,000`)
- **Required Gates**: `Band A + G4 + G5 + G7` (all green)
- **Everything in Band A, plus**:
  - **Browser Web Calls & SDKs (`G4`)**: `#22` Browser web-call WebSocket media transport & embeddable Shadow DOM widget (`/widget/v1/embed.js`), `#37` `@voxdesk/web-sdk`, `@voxdesk/node-sdk`, and `voxdesk` Python SDK.
  - **Live Supervisor Operations (`G5`)**: `#31` Real-time supervisor live listen (`MonitorBus`), whisper-to-AI guidance injection, and live human takeover with automatic rollback.
  - **Quality & Experimentation Loop (`G7`)**: `#28` Multi-turn LLM `SimulatedCaller` & one-click PII-redacted regression test creation from production calls, `#29` Live inbound A/B traffic splitting & statistical winner promotion, `#30` Custom analytics dashboards & scheduled CSV reports, `#32` Conductor AI copilot.
- **License Model**: Commercial Source License (`LICENSE` Option B) with SaaS sublicensing rights.

### Band C — Full Enterprise Suite, Visual Flow Builder & Exclusive/Full Due-Diligence Pack (`≈ $60,000`)
- **Required Gates**: `Band B + G6 + G8 + G9` (all green)
- **Everything in Band B, plus**:
  - **Visual Conversation-Flow Builder (`G6`)**: `#8` Full 6-node visual conversation-flow builder (React Flow UI + `FlowRunner` + Pipecat `FlowProcessor`).
  - **Enterprise CRM & Automation Suite (`G8`)**: `#33` Real Salesforce OAuth 2.0 + PKCE + SOQL + Contact/Lead/Task/Case/Opportunity writeback and HubSpot/GHL/Jobber adapters, `#38` Durable automation connectors.
  - **Enterprise Identity, Scale & Due-Diligence Proof (`G9`)**: `#20` STIR/SHAKEN number trust profiles & KYC registration, `#36` OIDC + PKCE and SAML 2.0 SSO + SCIM 2.0 user/group provisioning (`infra/keycloak/`), `#39` Production Helm chart (`infra/helm/voxdesk/`) with HPA/PDB, graceful drain (`app/core/graceful_shutdown.py`), chaos test suite (`tests/resilience/test_chaos_calls.py`), measured capacity model (`docs/CAPACITY_MODEL.md`), scripted DR drill (`scripts/dr_drill.sh`), CycloneDX SBOMs (`sbom/`), and signed due-diligence archive (`dist/due-diligence-<date>.zip`).
- **License Model**: Buyer's choice of Perpetual Commercial Source License or Exclusive IP Assignment (`LICENSE` Option A) under an Asset Purchase Agreement.

---

## 3. Handoff, Escrow & Support Terms

1. **Pre-Closing Verification**: Buyer receives read access to the due-diligence
   pack (`dist/due-diligence-<date>.zip`) and may execute `make verify-sale` on a
   clean runner to independently verify all test suites, truth guards, migrations,
   SBOMs, and SHA-256 checksums before releasing escrow funds.
2. **Deliverables at Closing**:
   - Full git repository (`voxdesk-call`) at head migration `0062_drop_pcap_artifacts`.
   - Signed due-diligence bundle (`dist/due-diligence-<date>.zip` + `dist/SHA256SUMS`).
   - Static OpenAPI 3.1 reference (`docs/api/`), CycloneDX SBOMs (`sbom/*.cdx.json`),
     and third-party license ledger (`THIRD_PARTY_LICENSES.md`).
3. **Included Handoff Support**:
   - **Band A**: 1x 90-minute live deployment & architecture handoff session + 14 days async Q&A.
   - **Band B**: 2x 90-minute handoff sessions (deployment + provider/webhook/widget integration) + 30 days async Q&A.
   - **Band C**: 4x 90-minute engineering walkthroughs (architecture, voice pipeline, flow builder, SSO/SCIM & Kubernetes/Helm ops) + 45 days priority async support.

---

## 4. Explicit Exclusions & Do-Not-Claim Disclosures

The following items are **excluded** from all tiers and are **never claimed as `LIVE`**:
1. **Third-Party Compliance Attestations (`Matrix #35` — `PLANNED`)**: While
   VoxDesk implements technical controls for PII redaction, encryption at rest,
   retention purges, and audit logging, the repository does **not** include an
   external auditor's SOC 2 Type II report, HIPAA certification, or ISO 27001
   certificate.
2. **Non-Twilio Carrier Live AI Media Streaming (`Matrix #19` — `NOT_CONFIGURED`)**:
   Live bi-directional AI voice media streaming (`/telephony/ws`) is verified on
   Twilio Media Streams; Telnyx, Vonage, SignalWire, and direct SIP RTP media
   bridging return `501 UNSUPPORTED_CAPABILITY` / `NOT_CONFIGURED` until carrier
   media bridge credentials are configured.
3. **Speech-to-Speech Default Production Path (`Matrix #3` — `API_ONLY`)**:
   The cascaded STT -> LLM -> TTS pipeline is `LIVE`; direct speech-to-speech
   (`OpenAI Realtime` / `Gemini Live`) is `API_ONLY` and requires external live
   provider keys.
4. **Live Third-Party Provider Subscriptions**: Buyer supplies their own Twilio,
   Deepgram, ElevenLabs, OpenAI/Anthropic/Gemini, Salesforce, and cloud hosting
   accounts.
