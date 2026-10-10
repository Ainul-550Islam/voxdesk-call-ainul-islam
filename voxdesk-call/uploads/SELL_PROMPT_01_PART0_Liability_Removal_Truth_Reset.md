# SELL PROMPT 1 of 10 — PART 0: Liability Removal & Truth Reset

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 1 of 10 — PART 0: LIABILITY REMOVAL & TRUTH RESET   (Gate G0)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: None - this is the FIRST prompt. Run STEP 0 first (python -c "from app.main import app;
#   print(len(app.routes))", alembic heads, pytest --collect-only, grep -rEn "Padding .* line [0-9]+" app services | wc
#   -l) and save reports/STEP0_BASELINE.md.
# MISSION: Remove everything that misrepresents the product and install permanent CI guards so it cannot come back.
#   Accepted when the repo has no filler engines, no template-clone routes, no padding lines and no fake-success markers
#   outside a shrink-only allowlist, and sales material is generated from evidence.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# GOAL: remove everything that misrepresents the product, install permanent CI guards so it cannot come back, and
#   replace the sales dossier with a generated, evidence-backed feature matrix.
# CLOSES: F-13, F-16, F-17, F-18, W-10 (decision), docs claims.  Do first. Nothing else may start before PART 0 is
#   green.
# TASK P0-01 Delete the three filler engine directories and every reference to them (compose, CI, scripts, docs).
# TASK P0-02 Delete the 95 template-clone modules in `app/api/` (see DELETE LIST at the end of this block) and
#   unregister them from `app/main.py`. Before deleting, record each module's `prefix=` and confirm no REAL router shares
#   it. Do not delete `retell_parity_routes.py` or `conductor_routes.py` (they are real).
# TASK P0-03 Strip all `Padding … line N` lines and banner comments from remaining files.
# TASK P0-04 Add permanent truth guards (scripts + tests + CI job). Known facade files (F-01…F-12) go into a shrink-only
#   allowlist: each PART removes its entries; CI fails if an allowlisted file no longer contains a marker (forces
#   allowlist cleanup).
# TASK P0-05 Remove the fallback secret in `issue_media_stream_token` (F-13).
# TASK P0-06 Archive misleading documents; generate README numbers and the feature matrix from code/tests.
# TASK P0-07 Repo hygiene: null-byte guard, `.gitattributes`, commit policy.
# TASK P0-08 Strip the generated tails from the shipped Vite dashboard (`dashboard/src`, 84 files, Appendix D): keep the
#   real component head of every file, remove `<stem>_real_N` / `<STEM>_CONST_N` symbols and hard-coded `verified: true,
#   real: true` flags; the build and tests must stay green.
# CLOSES facade rows: F-13 app/telephony/realtime.py::issue_media_stream_token | F-16 95 clone modules | F-17 3 filler
#   engines | F-18 dashboard/src/** (84 files, Appendix D)
# RULES (apply to every file below)
#   R1  Read every file fully before editing. Output the COMPLETE final content of every created/modified file.
#       Never write "...", "rest unchanged", "omitted for brevity".
#   R2  Extend REAL assets (tag [KEEP]); never build a parallel copy of something that already works.
#   R3  NO FAKE SUCCESS: do the real external effect or return NOT_CONFIGURED / UNSUPPORTED_CAPABILITY /
#       PENDING_PROVIDER (HTTP 501/409). Never record delivered/connected/success without proof.
#   R4  NO filler, padding, clones or line-count targets: no StructN, *_function_N, /endpoint-N, "Padding ... line
#       N" or "1050+ lines" banners.
#   R5  Durable multi-worker state only (Postgres / Redis / app/jobs / app/outbox). No process-local dict/list for
#       idempotency, rate limits, OAuth state, sessions, queues.
#   R6  Every outbound URL goes through app/core/ssrf.py. Secrets via app/security/secret_store.py or
#       app/integrations/crm/crypto.py (AES-GCM). No base64/reverse "encryption", no fallback secrets.
#   R7  Every query tenant-scoped (and environment-scoped where modelled). Cross-tenant = 404. Each new route ships
#       a two-tenant isolation test.
#   R8  Audit/outbox writes happen in the same transaction as the action. No `except Exception: pass` around
#       audit/outbox/webhook code.
#   R9  Every task ships unit + contract tests. External calls get a recording-fake test (asserts the real request)
#       AND an opt-in @pytest.mark.real_provider test (existing marker; runs only with VOXDESK_REAL_INTEGRATION=1). No
#       passing test = not LIVE.
#   R9b New tests/<dir>/ gets __init__.py; reuse the existing real_provider marker (never add a second `live`
#       marker); register only slow/docker in pytest.ini.
#   R10 Alembic: exactly ONE head (as shipped: 0045_request_idempotency_receipts). Use the next free revision, never
#       edit an applied migration, run `alembic heads` before and after.
#   R11 Docs contain only measured facts (scripts/repo_stats.py). Anything without an end-to-end/contract test is
#       documented as API_ONLY or PLANNED.
#   R12 One commit per task ID: "<TASK-ID>: <what>". No generated bulk. No null-byte files.
#   R13 After this part write reports/PART_<n>_REPORT.md: files changed (complete content), real command output,
#       test counts pass/fail/skip, residual gaps. Never claim "production ready".
#   R14 pipecat-ai is pinned at 0.0.94: inspect the installed package and use only classes that exist there.
#   R15 Register every new router/WebSocket in app/main.py, add an RBAC permission + isolation test, regenerate
#       contracts (make contracts-check). Never reuse an existing file name (ls app/api first).
# TAGS: [NEW] create | [MODIFY] read fully, change only what is described, keep other behaviour | [DELETE] remove +
#   every import/registration/compose/CI reference | [KEEP] REAL asset, extend only as stated | [VERIFY] inspect first,
#   record the decision in the report
# COMMENT FORMAT: # [TAG][sub-part] kind — what the file contains (classes / functions / behaviour / tests). Several
#   changes to one file are merged on one line as "sub-part: change || sub-part: change".
# ======================================================================================================================

voxdesk-call/
├── .github/
│   └── workflows/
│       ├── ci.yml                                    # [MODIFY] ci — add required job `truth-guards`:
│       │                                             #   scripts/verify_no_filler.py, verify_no_fake_success.py,
│       │                                             #   verify_no_null_bytes.py, strip_padding_markers.py --check, pytest
│       │                                             #   tests/truth
│       └── polyglot.yml                              # [MODIFY] ci — drop jobs for deleted dirs; keep gateway-go,
│                                                     #   media-engine-rs, control-plane, signal-go, media-plane, ops; fail a
│                                                     #   job if it ran 0 tests
├── app/
│   ├── api/
│   │   ├── <95 modules see DELETE LIST at the end of this block>.py  # [DELETE] route modules — template clones: /endpoint-N
│   │   │                                                             #   handlers returning hard-coded JSON, fake DB touch, no
│   │   │                                                             #   persistence, no tests, no frontend caller
│   │   └── __init__.py                               # [VERIFY] module — if it aggregates/re-exports routers, drop deleted
│   │                                                 #   ones
│   ├── telephony/
│   │   └── realtime.py                               # [MODIFY] module — issue_media_stream_token: delete the `or
│   │                                                 #   "voxdesk-media-stream-secret"` fallback; raise ConfigurationError
│   │                                                 #   when jwt_secret_key is unset (production already refuses default
│   │                                                 #   secrets); mock reply path is removed in PART 3
│   └── main.py                                       # [MODIFY] module — remove import + include_router for every deleted
│                                                     #   module; keep all real routers; no other behaviour change
├── contracts/                                        # [MODIFY] dir — regenerate OpenAPI/contract snapshots with the repo's
│                                                     #   existing tooling (`make contracts-check`,
│                                                     #   scripts/verify_contracts.py); the diff must contain only removals of
│                                                     #   the deleted routes
├── dashboard/
│   └── src/                                          # [MODIFY] dir — 84 files carry generated tails (<stem>_real_N
│                                                     #   functions, <STEM>_CONST_N constants with hard-coded `verified: true,
│                                                     #   real: true`; see Appendix D). Run scripts/strip_generated_tails.py;
│                                                     #   keep the real component head of each file; never delete a routed
│                                                     #   page; `npm run build` and vitest must stay green
│       └── __tests__/
│           └── no-generated-tails.test.ts            # [NEW] test — scans dashboard/src and dashboard-next for numbered
│                                                     #   generated definitions (`_real_\d+`, `_CONST_\d+`) and `verified:
│                                                     #   true, real: true` literals; fails if any exist
├── docs/
│   └── SALES/
│       └── FEATURE_MATRIX_VERIFIED.md                # [NEW] doc (generated) — the only feature table allowed in sales
│                                                     #   material; columns: feature, status, evidence, last verified date
├── realtime-engine/                                  # [DELETE] dir — generated filler (WebsocketStructN, *_function_N
│                                                     #   returning {"status":"ok"}, "// Padding … line N"); not imported by
│                                                     #   app/, not in docker-compose.prod.yml. Canonical realtime path =
│                                                     #   services/realtime/{gateway-go,media-engine-rs}
├── scripts/
│   ├── audit_missing_files.py                        # [MODIFY] script — stop requiring deleted dirs/Cargo.lock/config
│   │                                                 #   stubs; MUST NOT auto-create files to satisfy itself
│   ├── fake_success_allowlist.txt                    # [NEW] config — one path per line: the known facade files from the
│   │                                                 #   FACADE REGISTER at the end of this block (F-01…F-12). Each PART
│   │                                                 #   deletes its lines. CI fails when a listed file has no marker any
│   │                                                 #   more
│   ├── generate_feature_matrix.py                    # [NEW] script — reads feature_manifest.yaml + pytest junit xml;
│   │                                                 #   downgrades any LIVE feature whose evidence tests are
│   │                                                 #   missing/failing; writes docs/SALES/FEATURE_MATRIX_VERIFIED.md
│   ├── repo_stats.py                                 # [NEW] script — emits measured LOC/test/route counts excluding
│   │                                                 #   clones/generated; used to fill README, DUE_DILIGENCE and sales docs
│   │                                                 #   (no hand-typed numbers)
│   ├── strip_generated_tails.py                      # [NEW] script — TypeScript/JS tail stripper: removes top-level
│   │                                                 #   `export function <stem>_real_<N>(…)` / `export const
│   │                                                 #   <STEM>_CONST_<N> = …` and any numbered definitions sharing a stem
│   │                                                 #   (>=15 per stem); refuses to remove a symbol that is imported
│   │                                                 #   anywhere; `--check` (CI) / `--apply`; prints per-file
│   │                                                 #   removed/remaining lines and flags files whose remaining real code is
│   │                                                 #   < 10 lines
│   ├── strip_padding_markers.py                      # [NEW] script — removes lines matching ^\s*(#|//)\s*Padding\b.* and
│   │                                                 #   banner comments ("NO SKIP FULL CODE", "\d{3,}\+ lines") across app/
│   │                                                 #   services/ dashboard-next/ sdk/; idempotent; prints per-file counts;
│   │                                                 #   `--check` mode exits 1 if any remain (used by CI)
│   ├── verify_no_fake_success.py                     # [NEW] script — scans app/ (excluding tests) for fake-success
│   │                                                 #   phrases: "we simulate", "simulate token", "simulate http", "In
│   │                                                 #   production this would", "In a real implementation", "Real
│   │                                                 #   implementation would", "for now we mark", "for now, we return"; also
│   │                                                 #   flags `except Exception:\s*pass` within 5 lines of
│   │                                                 #   audit/outbox/webhook calls; reads scripts/fake_success_allowlist.txt
│   │                                                 #   (shrink-only)
│   ├── verify_no_filler.py                           # [NEW] script — flags source files with >=200 code lines whose
│   │                                                 #   distinct-line ratio (digits and string literals normalized) is <
│   │                                                 #   0.35; flags forbidden patterns: /endpoint-\d+, (Struct|Class)\d+\b,
│   │                                                 #   _function_\d+\b, _real_\d+, _CONST_\d+, `verified: true, real:
│   │                                                 #   true`, Padding … line N, "NO SKIP FULL CODE"; allowlist file for
│   │                                                 #   generated code (protobuf, lockfiles, locale tables, fixtures);
│   │                                                 #   `--report json`
│   └── verify_no_null_bytes.py                       # [NEW] script — fails if any tracked text file contains \x00 or is
│                                                     #   empty when it should not be (guard against the 755-file corruption
│                                                     #   incident)
├── tests/
│   └── truth/
│       ├── __init__.py                               # [NEW] test package
│       ├── feature_manifest.yaml                     # [NEW] config — machine-readable feature list: id, name, status (LIVE
│       │                                             #   | API_ONLY | PLANNED | NOT_CONFIGURED), evidence_tests[] (pytest
│       │                                             #   ids). Seed from the PARITY MATRIX at the end of this block; a status
│       │                                             #   of LIVE requires >=1 evidence test
│       ├── test_no_default_secrets.py                # [NEW] test — asserts no module falls back to a constant secret
│       │                                             #   (search for `or "` patterns next to jwt/secret/key), and that
│       │                                             #   production config validation refuses default JWT/secret values
│       ├── test_no_fake_success_markers.py           # [NEW] test — runs verify_no_fake_success with the shrink-only
│       │                                             #   allowlist; asserts allowlisted files still contain markers (forces
│       │                                             #   cleanup)
│       ├── test_no_filler.py                         # [NEW] test — runs verify_no_filler on app/, services/, dashboard/src
│       │                                             #   and dashboard-next/ and asserts zero findings
│       └── test_no_template_routes.py                # [NEW] test — imports app.main.app, asserts no route path matches
│                                                     #   r"/endpoint-\d+", no module under app/api contains banner strings,
│                                                     #   and route count is recorded in a snapshot
├── voxdesk-concurrency-engine/                       # [DELETE] dir — ConfigStructN clones (~38K lines, ~540 distinct); no
│                                                     #   consumer. Real concurrency limiting is implemented in Python/Redis
│                                                     #   in PART 1B
├── voxdesk-native-audio-engine/                      # [DELETE] dir — process() multiplies PCM by 1.0 (no-op);
│                                                     #   third_party/* are 90-byte README placeholders. Real DSP, if wanted,
│                                                     #   comes from pipecat's own audio filters in PART 2G
├── .gitattributes                                    # [NEW] config — `* text=auto eol=lf` for py/ts/md/yml; binary markers
│                                                     #   for wav/png
├── docker-compose.full.yml                           # [MODIFY] config — remove services concurrency-engine,
│                                                     #   native-audio-engine, go-realtime-engine, rust-realtime-engine;
│                                                     #   `docker compose -f docker-compose.full.yml config -q` must pass
├── DOCKER_BUILD_SIMULATION.md                        # [MODIFY] doc — move to docs/archive/ (simulated build log, not
│                                                     #   evidence)
├── FILE_LIST.txt                                     # [DELETE] doc — stale inventory listing deleted dirs (regenerate via
│                                                     #   scripts/repo_stats.py if needed)
├── FINAL_PRODUCTION_READY.md                         # [MODIFY] doc — move to docs/archive/ with banner "historical, not a
│                                                     #   current status"
├── FULL_AUDIT_REPORT.md                              # [MODIFY] doc — move to docs/archive/ with banner "historical"; keep
│                                                     #   the null-byte incident note (it is part of due diligence)
├── Makefile                                          # [MODIFY] config — add `verify-truth` (the five guard commands
│                                                     #   above); later PARTs extend `verify-sale`
├── README.md                                         # [MODIFY] doc — replace stale numbers (145 tests / 5,100 lines) with
│                                                     #   output of scripts/repo_stats.py; add "Status per feature" link to
│                                                     #   FEATURE_MATRIX_VERIFIED.md; remove any capability not marked LIVE
└── SALES_PACKAGE_20K_60K_GAP_CLOSURE.md              # [DELETE] doc — superseded; contradicted by code (claims such as 'no
                                                      #   fake data', 'encrypted token storage' and '40/40 gaps closed' are
                                                      #   contradicted by the code). Move to docs/archive/ only if you keep
                                                      #   it, with a banner "historical, inaccurate"

# ======================================================================================================================
# DELETE LIST — the 95 template-clone modules in app/api/ (run the safety check first; stop if any line fails)
# ======================================================================================================================
grep -l '"/endpoint-0"' app/api/*.py | wc -l                                      # expect 95
grep -L 'NO SKIP FULL CODE' $(grep -l '"/endpoint-0"' app/api/*.py)                  # expect empty
git ls-files app/api | grep -E 'retell_parity_routes|conductor_routes'               # these two are REAL: they must NOT be deleted
# Before deleting a module, grep for a REAL router that shares its prefix=; keep the real one and note it in the report.
# A — dialing & campaigns (12) | real equivalent to KEEP: app/telephony/outbound.py, app/services/campaign_service.py,
#   leads/contacts modules | capability closed by: PART 1B, 6
git rm app/api/{auto_dialer_routes,predictive_dialer_routes,dialer_optimization_routes,call_scheduling_routes,lead_distribution_routes,lead_routing_routes,lead_qualification_routes,lead_scoring_advanced_routes,lead_enrichment_routes,contact_enrichment_routes,email_campaign_routes,campaign_analytics_routes}.py
# B — numbers, SIP, routing, transfer (7) | real equivalent to KEEP:
#   app/telephony/{number_provisioning,sip,transfer,transfer_service}.py, app/contact_center/* | capability closed by:
#   PART 2D, 2E, 2F
git rm app/api/{number_pool_routes,number_porting_routes,sip_trunk_routes,telephony_advanced_routes,call_routing_advanced_routes,call_transfer_advanced_routes,call_escalation_routes}.py
# C — speech & voice (11) | real equivalent to KEEP: app/voice/*, app/tts/*, app/agent/stt*.py,
#   app/telephony/transcription.py (voice biometrics: no real equivalent — drop) | capability closed by: PART 2C, 2G, 1D
git rm app/api/{speech_to_text_routes,text_to_speech_routes,voice_activity_routes,voice_analytics_routes,voice_cloning_routes,voice_biometrics_routes,live_transcription_routes,realtime_transcription_routes,transcription_advanced_routes,speech_analytics_routes,multilingual_support_routes}.py
# D — analytics & intelligence (32) | real equivalent to KEEP: app/services/analytics_service.py, app/anomaly/*,
#   app/qa/{sentiment,topics}.py, app/observability/cost.py | capability closed by: PART 1C, 6
git rm app/api/{advanced_analytics_routes,ai_insights_routes,call_analytics_routes,conversation_analytics_routes,conversation_intelligence_routes,customer_insights_routes,customer_journey_routes,customer_segmentation_routes,channel_analytics_routes,disposition_analytics_routes,enterprise_reporting_routes,interaction_analytics_routes,ivr_analytics_routes,knowledge_analytics_routes,omnichannel_analytics_routes,performance_benchmark_routes,predictive_analytics_routes,realtime_dashboard_routes,revenue_analytics_routes,revenue_optimization_routes,sales_analytics_routes,team_analytics_routes,usage_analytics_routes,webhook_analytics_routes,workflow_analytics_routes,sentiment_advanced_routes,emotion_detection_routes,intent_detection_routes,risk_assessment_routes,fraud_detection_routes,cost_optimization_routes,call_intelligence_routes}.py
# E — coaching, QA, monitoring (17) | real equivalent to KEEP: app/qa/*, app/contact_center/*, app/anomaly/* |
#   capability closed by: PART 4, 6
git rm app/api/{agent_assist_routes,agent_collaboration_routes,agent_evaluation_routes,agent_performance_routes,agent_training_routes,call_coaching_routes,call_feedback_routes,call_quality_routes,call_scoring_routes,call_tagging_routes,call_disposition_routes,call_optimization_routes,quality_assurance_routes,realtime_coaching_routes,sales_coaching_routes,realtime_alerts_routes,realtime_monitoring_routes}.py
# F — compliance & redaction (6) | real equivalent to KEEP: app/gdpr/*, app/compliance/*,
#   app/telephony/{consent,recording_policy}.py | capability closed by: PART 1D, 7
git rm app/api/{call_compliance_routes,compliance_call_routes,compliance_recording_routes,compliance_gdpr_routes,call_redaction_routes,conversation_redaction_routes}.py
# G — billing, integrations, data (10) | real equivalent to KEEP: billing routes/services, app/integrations/*,
#   app/services/{notification,automation,workflow}_service.py | capability closed by: PART 1F, 6, 9
git rm app/api/{billing_metering_routes,enterprise_billing_routes,marketplace_billing_routes,integration_marketplace_routes,integration_health_routes,crm_sync_routes,data_export_routes,data_import_routes,notification_advanced_routes,workflow_automation_advanced_routes}.py
# total modules listed: 95

# ======================================================================================================================
# FORBIDDEN MARKERS — enforced by scripts/verify_no_filler.py and scripts/verify_no_fake_success.py
# ======================================================================================================================
#   Padding … line N                 NO SKIP FULL CODE               \d{3,}\+ lines
#   /endpoint-\d+                    (Struct|Class)\d+\b             _function_\d+\b
#   _real_\d+ / _CONST_\d+ symbols   verified: true, real: true   (hard-coded verification flags)
#   we simulate                      simulate token                  simulate http
#   In production this would         In a real implementation        Real implementation would
#   for now we mark                  for now, we return              except Exception:\s*pass   (near audit/outbox/webhook code)
#   fallback secrets: `or "<constant>"` next to jwt/secret/key settings

# ======================================================================================================================
# FACADE REGISTER — seed for scripts/fake_success_allowlist.txt (each later PART removes its own entries)
# ======================================================================================================================
# F-01 app/api/webhook_lifecycle_routes.py::_dispatch_webhook | today: **No HTTP request.** Marks delivery delivered /
#   200 / "ok" for any https:// URL not containing "fail"/"invalid"; no SSRF guard; duplicates
#   WebhookSubscription/WebhookDelivery | required: Delegate to app/webhooks/* (real signed delivery, SSRF guard, retries,
#   DLQ) | closed by PART 1A
# F-02 app/api/salesforce_routes.py | today: OAuth callback fabricates tokens from a hash of the code; every data
#   endpoint returns [] via _simulate_salesforce_query; "encryption" = reverse + base64; state in in-memory _oauth_states
#   | required: Real Salesforce provider in app/integrations/crm/providers/ | closed by PART 1F
# F-03 app/api/live_monitoring_routes.py::start_monitoring | today: Writes a LiveCallSession row + audit entry; no audio
#   leg; process-local _idempotency_cache/_rate_buckets; wrong AuditAction; audit errors swallowed | required: Real
#   listen/whisper-to-AI/takeover | closed by PART 4, 1E
# F-04 app/api/batch_call_routes.py::start_batch | today: Sets status = RUNNING; nothing reads BatchRecipient |
#   required: Batch = real campaign engine run | closed by PART 1B
# F-05 app/telephony/realtime.py::RealtimeVoiceSessionOrchestrator.handle_ws_message, mounted at
#   app/api/v1/telephony_routes.py /calls/{call_id}/media-stream | today: "Agent reply" is a template string; "audio" is
#   the first 160 bytes of that text padded with 0x7f | required: Delete or attach to the real pipeline | closed by PART 3
# F-06 app/api/outbound_call_routes.py web-call token path | today: Issues token (fallback tok_{uuid}_{call_id} on
#   import failure); **no browser client exists anywhere** | required: Real web-call transport + SDK | closed by PART 3
# F-07 app/api/crm_writeback_routes.py | today: "We simulate successful write"; mappings not persisted | required: Real
#   write-back through app/integrations/crm | closed by PART 1F
# F-08 app/api/ab_testing_routes.py | today: Metrics are stored values; promote publishes nothing; assignments never
#   read at call time | required: Real assignment + computed metrics | closed by PART 1G
# F-09 app/api/retention_routes.py | today: Purge is simulated; real purge is app/core/retention.py::purge_expired_calls
#   (scheduler) | required: Policy drives real purge | closed by PART 1D
# F-10 app/api/post_call_analysis_routes.py | today: Backfill = status flip, no worker; no automatic run at call end |
#   required: POST_CALL job pipeline | closed by PART 1C
# F-11 app/api/transfer_control_routes.py | today: Whisper/briefing never sent through the provider | required: Real
#   briefing leg | closed by PART 2D
# F-12 app/api/multichannel_routes.py, app/integrations/connector.py | today: Receipts empty; "Real implementation would
#   sync to provider" | required: Real channel adapters or remove | closed by PART 6
# F-13 app/telephony/realtime.py::issue_media_stream_token | today: Falls back to constant secret
#   "voxdesk-media-stream-secret" when JWT secret is unset | required: Hard fail without secret | closed by PART 0
# F-14 enterprise route modules | today: Module-level _idempotency_cache, _rate_buckets, _oauth_states dicts (break with
#   >1 worker, leak memory) | required: Durable shared stores | closed by PART 1E
# F-15 app/api/call_simulation_routes.py | today: allow_mock_fallback=True: runs "pass" against a mock when no LLM key |
#   required: Mock only by explicit flag; excluded from KPIs | closed by PART 6
# F-16 95 clone modules | today: see 1.2 | required: delete | closed by PART 0
# F-17 3 filler engines | today: see 1.2 | required: delete | closed by PART 0
# F-18 dashboard/src/** (84 files, Appendix D) | today: Generated functions/constants claim verified: true, real: true
#   and backend: 'GET /api/…' without calling anything | required: Strip generated tails; remove hard-coded verification
#   flags; npm run build + vitest green | closed by PART 0

# ======================================================================================================================
# PARITY MATRIX — seed for tests/truth/feature_manifest.yaml (status = today; closed by = PART / sub-part)
# ======================================================================================================================
# #1 End-to-end latency | UNVERIFIED | closed by 2A, 2B
# #2 Turn-taking & interruption controls | PARTIAL | closed by 2B
# #3 LLMs and speech-to-speech | PARTIAL | closed by 2C
# #4 STT / TTS provider choice | PARTIAL | closed by 2C
# #5 Voice cloning / voice library | UNVERIFIED | closed by 2C
# #6 Voice controls (speed, volume, pronunciation, boosted keywords, ambient sound, denoise) | PARTIAL | closed by 2G
# #7 Multilingual / language switching | PARTIAL | closed by 2G
# #8 Visual conversation-flow builder | MISSING | closed by 5
# #9 Agent versions / publish / rollback | PARTIAL | closed by 2E
# #10 Agent bound to phone number | MISSING | closed by 2E
# #11 Custom functions (HTTP tools) | FACADE | closed by 2D
# #12 MCP tools | MISSING | closed by 2D
# #13 Cold/warm transfer + AI briefing to human | PARTIAL | closed by 2D
# #14 Agent-to-agent transfer | REAL (verify e2e) | closed by 2E
# #15 IVR navigation (press digits on external phone trees) | MISSING | closed by 2D
# #16 DTMF | PARTIAL | closed by 2D
# #17 Voicemail detection + message | PARTIAL | closed by 2D, 1B
# #18 Numbers: buy / import / SIP trunk | PARTIAL | closed by 2E, 2F
# #19 Multi-carrier live media | PARTIAL | closed by 2F
# #20 Branded caller ID / verified numbers | MISSING | closed by 7
# #21 Batch calls / power dialer | PARTIAL | closed by 1B
# #22 Web calls, widget, web SDK | MISSING | closed by 3
# #23 Chat / SMS agents | UNVERIFIED | closed by 6
# #24 Knowledge base (RAG) | PARTIAL | closed by 2E
# #25 Webhooks (call started/ended/analyzed) | PARTIAL | closed by 1A
# #26 Post-call analysis (summary, sentiment, custom fields) | MISSING | closed by 1C
# #27 Automated QA on all calls | PARTIAL | closed by 1C, 6
# #28 Simulation testing & regression from production calls | PARTIAL | closed by 6
# #29 A/B test agent versions | FACADE | closed by 1G
# #30 Analytics & custom dashboards | PARTIAL | closed by 6
# #31 Live call monitoring / takeover | FACADE | closed by 4
# #32 AI copilot (Conductor) | UNVERIFIED | closed by 6
# #33 CRM (Salesforce/HubSpot) | PARTIAL | closed by 1F
# #34 Calendar booking | REAL (verify) | closed by —
# #35 HIPAA / SOC 2 Type II / GDPR / ISO 27001 | MISSING (non-code) | closed by 7
# #36 SSO / SCIM / RBAC / PII redaction | PARTIAL | closed by 1D, 7
# #37 SDKs | MISSING | closed by 3
# #38 Automation connectors | MISSING | closed by 3
# #39 Scale & reliability evidence | MISSING | closed by 8
# #40 Billing & metering | REAL (verify) | closed by —
# #41 Contact memory & dynamic variables | REAL (verify) | closed by 2E

# ======================================================================================================================
# STRIP LIST — generated-tail files in dashboard/src (scripts/strip_generated_tails.py finds them itself; this list is for verification)
# ======================================================================================================================
#   dashboard/src/pages/product/customer-service/CustomerServiceChannels.tsx  total=1728  generated=1486  remaining=242
#   dashboard/src/config/useCaseNavigation.ts  total=1267  generated=1188  remaining=79
#   dashboard/src/config/useCaseRoutes.ts  total=1142  generated=1089  remaining=53
#   dashboard/src/pages/agents/CreateAgentPage.tsx  total=1328  generated=1068  remaining=260
#   dashboard/src/pages/product/customer-service/CustomerServiceHero.tsx  total=1331  generated=1038  remaining=293
#   dashboard/src/pages/product/voice-agents/VoiceAgentsBuilderPreview.tsx  total=984  generated=874  remaining=110
#   dashboard/src/pages/product/customer-service/CustomerServicePage.tsx  total=1375  generated=864  remaining=511
#   dashboard/src/pages/industries/IndustriesHero.tsx  total=674  generated=660  remaining=14
#   dashboard/src/pages/industries/IndustriesGrid.tsx  total=674  generated=660  remaining=14
#   dashboard/src/pages/industries/IndustriesCTA.tsx  total=674  generated=660  remaining=14
#   dashboard/src/pages/industries/IndustriesBenefits.tsx  total=674  generated=660  remaining=14
#   dashboard/src/pages/product/telemarketing/TelemarketingSecurity.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingScheduling.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingQualification.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingLifecycle.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingFollowUp.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingFAQ.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingDeveloper.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingCampaigns.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingCRM.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/telemarketing/TelemarketingAnalytics.tsx  total=675  generated=658  remaining=17
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterSecurity.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterReschedule.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterReminders.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterQualification.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterLifecycle.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterHowItWorks.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterHero.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterFAQ.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterDeveloper.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterCapabilities.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterCTA.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterBooking.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterAnalytics.tsx  total=674  generated=652  remaining=22
#   dashboard/src/pages/product/answering-service/AnsweringServiceSecurity.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceRouting.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceLifecycle.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceHero.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceFAQ.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceDeveloper.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceCustomVoice.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceCapabilities.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceCTA.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceCRM.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceBooking.tsx  total=682  generated=632  remaining=50
#   dashboard/src/pages/product/answering-service/AnsweringServiceAnalytics.tsx  total=682  generated=632  remaining=50
#   dashboard/src/types/use-case-filter.ts  total=1020  generated=622  remaining=398
#   dashboard/src/pages/product/telemarketing/TelemarketingPage.tsx  total=695  generated=596  remaining=99
#   dashboard/src/pages/product/appointment-setter/AppointmentSetterPage.tsx  total=696  generated=586  remaining=110
#   dashboard/src/pages/product/answering-service/AnsweringServicePage.tsx  total=701  generated=580  remaining=121
#   dashboard/src/pages/industries/IndustriesPage.tsx  total=699  generated=576  remaining=123
#   dashboard/src/pages/product/customer-service/CustomerServiceHandoff.tsx  total=704  generated=558  remaining=146
#   dashboard/src/pages/product/customer-service/CustomerServiceKnowledge.tsx  total=707  generated=548  remaining=159
#   dashboard/src/api/use-cases.ts  total=961  generated=522  remaining=439
#   dashboard/src/pages/use-cases/UseCasesPage.tsx  total=722  generated=518  remaining=204
#   dashboard/src/pages/use-cases/UseCasesDetailPage.tsx  total=729  generated=512  remaining=217
#   dashboard/src/hooks/useUseCases.ts  total=962  generated=505  remaining=457
#   dashboard/src/components/use-cases/UseCaseDetailSkeleton.tsx  total=753  generated=482  remaining=271
#   dashboard/src/components/use-cases/UseCaseMetricPreview.tsx  total=752  generated=482  remaining=270
#   dashboard/src/pages/use-cases/UseCaseCTA.tsx  total=753  generated=479  remaining=274
#   dashboard/src/components/use-cases/UseCaseSkeleton.tsx  total=754  generated=478  remaining=276
#   dashboard/src/components/use-cases/UseCaseEmptyState.tsx  total=754  generated=478  remaining=276
#   dashboard/src/pages/use-cases/UseCaseHero.tsx  total=755  generated=475  remaining=280
#   dashboard/src/pages/use-cases/UseCaseCapabilities.tsx  total=755  generated=475  remaining=280
#   dashboard/src/components/use-cases/UseCaseFilterBar.tsx  total=756  generated=473  remaining=283
#   dashboard/src/pages/use-cases/UseCaseIntegrationPreview.tsx  total=757  generated=471  remaining=286
#   dashboard/src/components/use-cases/UseCaseFAQ.tsx  total=757  generated=471  remaining=286
#   dashboard/src/components/use-cases/UseCaseProcessTimeline.tsx  total=757  generated=470  remaining=287
#   dashboard/src/components/use-cases/UseCaseGrid.tsx  total=758  generated=469  remaining=289
#   dashboard/src/pages/use-cases/UseCaseExampleCall.tsx  total=757  generated=469  remaining=288
#   dashboard/src/pages/use-cases/UseCaseWorkflow.tsx  total=759  generated=467  remaining=292
#   dashboard/src/components/use-cases/UseCaseCategoryBadge.tsx  total=757  generated=467  remaining=290
#   dashboard/src/pages/use-cases/UseCasesHero.tsx  total=761  generated=463  remaining=298
#   dashboard/src/components/use-cases/UseCaseCard.tsx  total=762  generated=462  remaining=300
#   dashboard/src/components/use-cases/UseCaseSearchInput.tsx  total=759  generated=462  remaining=297
#   dashboard/src/pages/product/customer-service/CustomerServiceAnalytics.tsx  total=745  generated=462  remaining=283
#   dashboard/src/hooks/useUseCaseCategories.ts  total=756  generated=455  remaining=301
#   dashboard/src/hooks/useUseCaseDetail.ts  total=761  generated=436  remaining=325
#   dashboard/src/pages/product/voice-agents/VoiceAgentsLifecycle.tsx  total=815  generated=332  remaining=483
#   dashboard/src/hooks/useAgentTest.ts  total=317  generated=290  remaining=27
#   dashboard/src/types/use-case.ts  total=932  generated=240  remaining=692
#   dashboard/src/types/agent-test.ts  total=507  generated=240  remaining=267
#   dashboard/src/api/agent-test.ts  total=264  generated=240  remaining=24
#   dashboard/src/tests/use-cases.test.tsx  total=300  generated=227  remaining=73

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_0_REPORT.md
# ======================================================================================================================
make verify-truth
python -c "from app.main import app; print(len(app.routes))"          # BEFORE vs AFTER; expect a drop of the clone routes (≈7.7K)
alembic heads                                                         # exactly one head
docker compose -f docker-compose.prod.yml config -q && docker compose -f docker-compose.full.yml config -q
python scripts/strip_generated_tails.py --check dashboard/src       # 0 generated tails remain
(cd dashboard && npm ci && npm run build && npx vitest run)          # the shipped Vite UI still builds and passes after the strip
grep -rEn "realtime-engine|voxdesk-concurrency-engine|voxdesk-native-audio-engine" . --include=* -l | grep -v docs/archive   # empty
grep -rEn "Padding .* line [0-9]+" app services | wc -l               # 0
pytest -q tests/truth

# MUST NOT: delete real routers; delete `services/` Rust/Go/C++ code; weaken a guard to make it pass; keep a clone "for
#   later"; delete a routed dashboard page (strip generated tails only).

# NEXT: SELL PROMPT 2 of 10 — PART 1: WIRE THE CONTROL PLANE TO THE RUNTIME. Start it only after every acceptance
#   command above passes and reports/PART_0_REPORT.md exists.
```
