# SELL PROMPT 2 of 10 — PART 1: Wire the Control Plane to the Runtime

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 2 of 10 — PART 1: WIRE THE CONTROL PLANE TO THE RUNTIME   (Gates G1, G2 and part of G7)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0) accepted: reports/PART_0_REPORT.md exists and `make verify-truth` is green.
# MISSION: Turn control-plane tables and endpoints into features that act on real calls: customer webhooks driven by the
#   call lifecycle, a real batch/power dialer with DNC and limits, an automatic post-call pipeline, enforced retention and
#   PII redaction, durable idempotency/rate-limit/audit state, a real Salesforce provider with real CRM write-back, and
#   A/B testing wired to routing.
# ORDER OF WORK: 1A -> 1C -> 1D -> 1E -> 1B -> 1F -> 1G. One commit per sub-part, one report section per sub-part. After
#   each sub-part delete its lines from scripts/fake_success_allowlist.txt.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# FACTS [1A]: real delivery exists (`app/webhooks/delivery.py`, signing, retry, SSRF guard) and the outbox exists
#   (`app/outbox`), but nothing in the call lifecycle publishes `call_started/call_ended/...` (outbox publishers found
#   only in `app/anomaly/alerting.py` and `app/api/outbox_routes.py`). `webhook_lifecycle_routes.py` fakes deliveries.
# DESIGN [1B]: do not build a second dialer. `BatchCall` becomes the user-facing view of the REAL campaign engine
#   (`app/telephony/outbound.py::run_campaign_tick`, TCPA window, backoff, claim-exactly-once guard in
#   `tests/campaign/test_concurrency.py`).
# CLOSES facade rows: F-01 app/api/webhook_lifecycle_routes.py::_dispatch_webhook | F-02 app/api/salesforce_routes.py |
#   F-03 app/api/live_monitoring_routes.py::start_monitoring | F-04 app/api/batch_call_routes.py::start_batch | F-07
#   app/api/crm_writeback_routes.py | F-08 app/api/ab_testing_routes.py | F-09 app/api/retention_routes.py | F-10
#   app/api/post_call_analysis_routes.py | F-14 enterprise route modules
# CLOSES unwired-table rows: W-01 BatchCall, BatchRecipient | W-02 CallPolicy | W-03 DncEntry | W-04 Experiment,
#   ExperimentVariant | W-05 RetentionPolicy | W-06 AnalysisSchema, AnalysisResult, BackfillJob | W-07 WebhookEndpoint,
#   WebhookDeliveryAttempt | W-08 SalesforceConnection, CrmWritebackLog
# CLOSES Retell parity rows: #17 Voicemail detection + message | #21 Batch calls / power dialer | #25 Webhooks (call
#   started/ended/analyzed) | #26 Post-call analysis (summary, sentiment, custom fields) | #27 Automated QA on all calls |
#   #29 A/B test agent versions | #33 CRM (Salesforce/HubSpot) | #36 SSO / SCIM / RBAC / PII redaction
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
├── alembic/
│   └── versions/
│       ├── <next>_batch_as_campaign.py       # [NEW][1B] migration — Campaign.kind, pacing/limits JSON, retry policy JSON,
│       │                                     #   voicemail_action; BatchCall.campaign_id; DncEntry unique index
│       ├── <next>_crm_connection_unify.py    # [NEW][1F] migration — migrate SalesforceConnection/CrmWritebackLog into
│       │                                     #   generic tables (idempotent), drop duplicates
│       ├── <next>_experiment_assignment.py   # [NEW][1G] migration — Call.experiment_variant_id + indexes
│       ├── <next>_post_call_pipeline.py      # [NEW][1C] migration — new columns/indexes for the above +
│       │                                     #   `post_call_step_runs` table (call_id, step, status, error, started_at,
│       │                                     #   finished_at)
│       └── <next>_unify_webhooks.py          # [NEW][1A] migration — copy rows from the duplicate tables into
│                                             #   WebhookSubscription/WebhookDelivery (idempotent), then drop them; downgrade
│                                             #   recreates empty tables
├── app/
│   ├── ai/
│   │   ├── guardrails/
│   │   │   └── pii.py                        # [MODIFY][1D] module — delegate to the unified detector (keep public function
│   │   │                                     #   names)
│   │   └── post_call_llm.py                  # [NEW][1C] module — structured-output calls through app/ai/gateway.py
│   │                                         #   (budget, timeout, fallback, circuit breaker): summarize(),
│   │                                         #   classify_sentiment(), extract_fields(schema, transcript) with JSON-schema
│   │                                         #   validation + bounded retry; provider missing → step status `not_configured`
│   │                                         #   (never fabricated output)
│   ├── api/
│   │   ├── *_routes.py                       # [MODIFY][1E] route modules — (enterprise modules: outbound_call,
│   │   │                                     #   transfer_control, live_monitoring, agent_lifecycle, phone_number_lifecycle,
│   │   │                                     #   recording_management, batch_call, post_call_analysis, ab_testing, pcap,
│   │   │                                     #   retention, webhook_lifecycle, salesforce, crm_writeback, knowledge_base,
│   │   │                                     #   call_simulation, agent_version, tool_registry, workflow_event, multichannel,
│   │   │                                     #   call_search_export) delete module-level `_idempotency_cache`,
│   │   │                                     #   `_rate_buckets`, `_oauth_states` dicts; use idempotent() + Redis rate
│   │   │                                     #   limiter + DB/Redis OAuth state with TTL
│   │   ├── ab_testing_routes.py              # [MODIFY][1G] route — thin routes over the service; results are computed, not
│   │   │                                     #   stored; delete "return stored metrics" and non-publishing promote
│   │   ├── batch_call_routes.py              # [MODIFY][1B] route — real endpoints: create (CSV/JSON recipients +
│   │   │                                     #   per-recipient variables), validate (E.164, dedupe), preview,
│   │   │                                     #   start/pause/resume/cancel with an explicit state machine, live progress from
│   │   │                                     #   DB, recipients with outcomes, export. `start` activates the campaign and
│   │   │                                     #   returns real state; delete the status-flip-only handler
│   │   ├── crm_writeback_routes.py           # [MODIFY][1F] route — persisted mapping CRUD, dry-run preview using real
│   │   │                                     #   describe(), manual re-sync of a call, write-back log of REAL attempts;
│   │   │                                     #   delete simulated success
│   │   ├── outbound_call_routes.py           # [MODIFY][1B] route — use dnc.is_blocked and DialerLimiter for API-created
│   │   │                                     #   calls; keep real Twilio create_outbound path
│   │   ├── post_call_analysis_routes.py      # [MODIFY][1C] route — thin routes over the service; backfill creates jobs and
│   │   │                                     #   returns job ids; status reads real job state; remove status-flip logic
│   │   ├── retention_routes.py               # [MODIFY][1D] route — CRUD for RetentionPolicy + `preview` (dry-run counts) +
│   │   │                                     #   `run` (enqueues a real purge job); delete the simulated purge
│   │   ├── salesforce_routes.py              # [MODIFY][1F] route — OAuth start/callback (state + PKCE verifier stored in
│   │   │                                     #   DB/Redis with TTL), disconnect, test-connection, describe objects, sample
│   │   │                                     #   query; DELETE `_simulate_salesforce_query`, fabricated tokens,
│   │   │                                     #   reverse+base64 helper
│   │   └── webhook_lifecycle_routes.py       # [MODIFY][1A] route — rewrite as a thin API over
│   │                                         #   WebhookSubscription/WebhookDelivery + app/webhooks/*:
│   │                                         #   create/list/get/update/delete subscription, rotate-secret, send-test (REAL
│   │                                         #   signed HTTP via delivery.py; response shows actual status/latency/body
│   │                                         #   snippet), list deliveries, retry, replay, DLQ list + redrive. DELETE
│   │                                         #   `_dispatch_webhook` and every simulated-success path
│   ├── core/
│   │   ├── rate_limit.py                     # [KEEP][1E] module — extend with `rate_limit(key, limit, window)` backed by
│   │   │                                     #   Redis if not already; ALL enterprise routes use it
│   │   └── retention.py                      # [MODIFY][1D] module — purge_expired_calls honors per-tenant/per-agent
│   │                                         #   RetentionPolicy (separate days for transcripts / recordings / analysis;
│   │                                         #   `zero_retention` mode; legal-hold flag); emits audit + metric
│   │                                         #   `voxdesk_retention_purged_total{kind}`; global setting stays the default
│   ├── db/
│   │   └── enterprise_models.py              # [MODIFY][1A,1B,1C] model — 1A: remove WebhookEndpoint and
│   │                                         #   WebhookDeliveryAttempt (duplicates of WebhookSubscription/WebhookDelivery)
│   │                                         #   || 1B: BatchCall.campaign_id link; CallPolicy fields consumed by
│   │                                         #   dialer_limits; DncEntry unique (tenant_id, e164, channel) || 1C:
│   │                                         #   AnalysisResult unique (schema_id, call_id, schema_version);
│   │                                         #   BackfillJob.job_id → jobs table; indexes
│   ├── gdpr/
│   │   └── redact.py                         # [MODIFY][1D] module — merge with app/ai/guardrails/pii.py into ONE PII
│   │                                         #   detector (regex + optional Presidio), entity types configurable; fn
│   │                                         #   redact_transcript(turns, entities)
│   ├── integrations/
│   │   ├── crm/
│   │   │   ├── providers/
│   │   │   │   └── salesforce.py             # [NEW][1F] module — class SalesforceProvider(CrmProvider): OAuth2 web-server
│   │   │   │                                 #   flow + PKCE, refresh, instance_url handling (prod/sandbox/My Domain), pinned
│   │   │   │                                 #   REST API version; find contact/lead by phone/email (escaped SOQL),
│   │   │   │                                 #   create/update Lead/Contact, log call as Task (Subject, Description=summary,
│   │   │   │                                 #   CallDurationInSeconds, CallType, WhoId/WhatId), create Case, link
│   │   │   │                                 #   Opportunity; 401 → refresh, 429/REQUEST_LIMIT_EXCEEDED → backoff; field
│   │   │   │                                 #   errors surfaced; instance_url validated https + salesforce/force domains
│   │   │   │                                 #   through app/core/ssrf.py
│   │   │   ├── crypto.py                     # [KEEP][1F] module — AES-GCM envelope used for access/refresh tokens (never
│   │   │   │                                 #   base64/reverse)
│   │   │   ├── mapping.py                    # [MODIFY][1F] module — persisted per-tenant field mappings (CRM object/field
│   │   │   │                                 #   ↔ call/contact fields) validated against provider describe() metadata
│   │   │   ├── models.py                     # [MODIFY][1F] model — generic CrmConnection (provider, encrypted tokens,
│   │   │   │                                 #   instance_url, scopes, status, last_error); replaces SalesforceConnection
│   │   │   └── registry.py                   # [MODIFY][1F] module — register provider key "salesforce"
│   │   └── connector.py                      # [MODIFY][1F] module — remove "Real implementation would sync to provider"
│   │                                         #   placeholder; dispatch to the registry or return NOT_CONFIGURED
│   ├── jobs/
│   │   ├── registry.py                       # [MODIFY][1C] module — register POST_CALL → PostCallPipeline; idempotency key
│   │   │                                     #   via app/jobs/idempotency.py (add post_call_key(call_id))
│   │   └── types.py                          # [MODIFY][1C] module — add JobType.POST_CALL
│   ├── resilience/
│   │   └── idempotency.py                    # [KEEP][1E] module — REAL request-idempotency service on
│   │                                         #   RequestIdempotencyReceipt (migration 0045; tenant + environment scoped).
│   │                                         #   Expose/extend it as a FastAPI dependency `idempotent()` (Idempotency-Key +
│   │                                         #   request hash → stored response; 409 on same key with a different body; TTL)
│   │                                         #   and use it in every enterprise route instead of `_idempotency_cache`
│   ├── services/
│   │   ├── experiment_service.py             # [NEW][1G] service — create experiment on an agent/number: variants →
│   │   │                                     #   AgentVersion ids + weights; deterministic assignment hash(call_or_contact_id
│   │   │                                     #   + experiment_id) with sticky-per-contact option; guardrails (min sample, <=4
│   │   │                                     #   variants); metrics computed from calls (success rate, duration, cost,
│   │   │                                     #   sentiment, transfer rate, latency p50/p95) with confidence interval;
│   │   │                                     #   promote(winner) → publish AgentVersion through agent_service with audit
│   │   └── post_call_analysis_service.py     # [NEW][1C] service — AnalysisSchema CRUD (field types: text, enum, boolean,
│   │                                         #   number, list; descriptions, examples), run_for_call(), backfill(query)
│   │                                         #   creating REAL jobs (batched, rate-limited), cost estimate preview, results
│   │                                         #   query/export
│   ├── telephony/
│   │   ├── call_events.py                    # [VERIFY][1A] module — read first; if it already centralizes state-change
│   │   │                                     #   events, publish from here instead of duplicating
│   │   ├── call_state.py                     # [MODIFY][1A,1C] module — 1A: after each committed transition
│   │   │                                     #   (ringing/in_progress/completed/failed/transferred) call publish_call_event;
│   │   │                                     #   keep existing transition rules and idempotency || 1C: on transition to a
│   │   │                                     #   terminal state enqueue POST_CALL in the same transaction as the outbox event
│   │   ├── dialer_limits.py                  # [NEW][1B] module — class DialerLimiter: Redis-backed concurrency semaphores
│   │   │                                     #   + token bucket per tenant / from-number / campaign with TTL leases so
│   │   │                                     #   crashed workers release slots; fn effective_policy(tenant, campaign, number)
│   │   │                                     #   merging CallPolicy rows
│   │   ├── dnc.py                            # [NEW][1B] module — single source of truth: async fn is_blocked(session,
│   │   │                                     #   tenant_id, e164, *, channel) consulting DncEntry, Lead.do_not_call,
│   │   │                                     #   consent/opt-out, optional external scrub adapter interface; used by
│   │   │                                     #   outbound.py, batch routes, campaign tick, `mark_do_not_call` tool and API
│   │   │                                     #   call creation
│   │   ├── outbound.py                       # [MODIFY][1B] module — extend run_campaign_tick for `kind='batch'` campaigns:
│   │   │                                     #   pacing (max_calls_per_minute, max_concurrency), per-recipient dynamic
│   │   │                                     #   variables, retry schedule, voicemail action; KEEP existing TCPA window,
│   │   │                                     #   backoff, DNC logic and the claim-once guarantee
│   │   │                                     #   (tests/campaign/test_concurrency.py must stay green)
│   │   ├── post_call.py                      # [NEW][1C] module — class PostCallPipeline.run(call_id): idempotent steps
│   │   │                                     #   with per-step status rows: (1) finalize transcript
│   │   │                                     #   (telephony/transcription.py), (2) LLM summary + sentiment +
│   │   │                                     #   outcome/disposition, (3) custom AnalysisSchema extraction → AnalysisResult,
│   │   │                                     #   (4) enqueue QA_AUTO_REVIEW using app/qa/sampling.py, (5) publish
│   │   │                                     #   call_analyzed (1A), (6) CRM write-back (1F), (7) workflow triggers; one
│   │   │                                     #   failing step never blocks the others; retry-safe
│   │   ├── recording.py                      # [MODIFY][1D] module — audio-redaction hook interface (provider adapter);
│   │   │                                     #   `NOT_CONFIGURED` if none; never claim redaction when disabled
│   │   ├── recording_policy.py               # [MODIFY][1D] module — `effective()` consumed by retention and
│   │   │                                     #   recording-start decision; keep consent integration
│   │   ├── runtime.py                        # [MODIFY][1G] module — at call creation resolve the agent version through
│   │   │                                     #   experiment_service.assign() when an active experiment exists; store
│   │   │                                     #   `experiment_variant_id` on Call
│   │   ├── transcription.py                  # [MODIFY][1D] module — apply per-agent `pii_redaction` before persisting
│   │   │                                     #   turns and before sending to LLM analysis/webhooks; raw stored only if policy
│   │   │                                     #   allows
│   │   └── twilio_handler.py                 # [MODIFY][1A] module — in status-callback finalization (next to
│   │                                         #   crm_hooks.on_call_completed) publish call_ended once; keep `call.crm_synced`
│   │                                         #   semantics; replay-safe
│   └── webhooks/
│       ├── call_event_bridge.py              # [NEW][1A] module — fn publish_call_event(session, call, event, extra=None):
│       │                                     #   writes an OutboxEvent in the SAME transaction as the state change;
│       │                                     #   idempotency key `call:{id}:{event}:v1`; fans out to every enabled
│       │                                     #   WebhookSubscription whose event filter matches; never swallows errors
│       ├── call_event_catalog.py             # [NEW][1A] module — canonical customer-facing event names + versioned JSON
│       │                                     #   Schemas: call_started, call_ended, call_analyzed,
│       │                                     #   transfer_started/completed/failed, voicemail_detected, dtmf_received,
│       │                                     #   recording_ready, batch_started/completed, agent_published; fn
│       │                                     #   validate_payload(event, payload); single source for docs + publisher
│       ├── delivery.py                       # [KEEP][1A] module — REAL signed delivery with SSRF guard, no redirects;
│       │                                     #   extend only: per-subscription timeout/headers, `X-VoxDesk-Event`,
│       │                                     #   `X-VoxDesk-Signature`, `X-VoxDesk-Timestamp`
│       ├── repository.py                     # [MODIFY][1A] module — subscription CRUD gains `event_types` (JSON array
│       │                                     #   filter), `enabled`, `description`, rotate_secret(); secrets sealed with
│       │                                     #   existing seal_secret()
│       └── retry.py                          # [KEEP][1A] module — exponential backoff + jitter + max attempts; make
│                                             #   `dead_lettered` visible to the API
├── docs/
│   ├── CRM-INTEGRATIONS.md                   # [MODIFY][1F] doc — matrix: provider × capability × "verified live on <date>"
│   │                                         #   (blank until a live run exists)
│   └── WEBHOOKS.md                           # [NEW][1A] doc — event catalog, payload examples, signature verification
│                                             #   snippets (Python/Node), retry schedule, DLQ semantics
├── scripts/
│   └── scheduler.py                          # [MODIFY][1B] script — campaign tick loop drives batch-kind campaigns;
│                                             #   heartbeat metric `voxdesk_scheduler_tick_seconds{loop}`; graceful shutdown
├── tests/
│   ├── ai/
│   │   └── test_post_call_llm.py             # [NEW][1C] test — JSON-schema validation, retry, `not_configured` path, cost
│   │                                         #   recorded
│   ├── campaign/
│   │   ├── test_batch_dialing.py             # [NEW][1B] test — batch start dials recipients through the provider fake;
│   │   │                                     #   pacing respected; retries scheduled; double-dial impossible under
│   │   │                                     #   concurrency
│   │   ├── test_dialer_limits.py             # [NEW][1B] test — per-tenant/number concurrency caps; lease expiry after
│   │   │                                     #   worker crash
│   │   └── test_dnc_enforcement.py           # [NEW][1B] test — DNC in DncEntry blocks batch, API call, campaign tick and
│   │                                         #   tool path alike
│   ├── compliance/
│   │   ├── test_pii_redaction_pipeline.py    # [NEW][1D] test — transcript/webhook/analysis payloads contain no raw PII
│   │   │                                     #   when enabled
│   │   └── test_retention_enforcement.py     # [NEW][1D] test — policy days honored per kind; zero-retention; legal hold;
│   │                                         #   real rows deleted (not just flagged)
│   ├── integrations/
│   │   ├── test_crm_real_provider_matrix.py  # [NEW][1F] test — `@pytest.mark.real_provider` HubSpot / GoHighLevel / Jobber
│   │   │                                     #   round trips; results recorded in docs/CRM-INTEGRATIONS.md with date
│   │   ├── test_salesforce_provider.py       # [NEW][1F] test — respx-recorded contract: real URLs/params/headers, token
│   │   │                                     #   refresh, error mapping, SOQL escaping; asserts no fabricated data
│   │   └── test_salesforce_real_provider.py  # [NEW][1F] test — `@pytest.mark.real_provider` sandbox round trip (skipped
│   │                                         #   without VOXDESK_REAL_INTEGRATION=1 and SF_* env)
│   ├── security/
│   │   ├── test_audit_durability.py          # [NEW][1E] test — audit write failure fails/rolls back the action (or lands
│   │   │                                     #   in outbox); never silently dropped
│   │   └── test_no_process_local_state.py    # [NEW][1E] test — static scan: no module-level mutable dict/list named
│   │                                         #   *_cache/*_buckets/*_states in app/api; two-worker simulation proves
│   │                                         #   idempotency + rate limit hold across processes
│   ├── telephony/
│   │   ├── test_experiment_assignment.py     # [NEW][1G] test — deterministic split within tolerance over N calls; sticky
│   │   │                                     #   behaviour; promote changes the live version
│   │   └── test_post_call_pipeline.py        # [NEW][1C] test — terminal transition enqueues exactly one POST_CALL; steps
│   │                                         #   idempotent; step failure isolated; QA job enqueued per sampling policy
│   └── webhooks/
│       ├── test_call_event_bridge.py         # [NEW][1A] test — state transitions publish exactly one outbox event per
│       │                                     #   (call,event); replay does not duplicate; transaction rollback publishes
│       │                                     #   nothing
│       ├── test_webhook_isolation.py         # [NEW][1A] test — two tenants cannot read/replay each other's subscriptions
│       │                                     #   or deliveries (404)
│       └── test_webhook_lifecycle_real.py    # [NEW][1A] test — local HTTP receiver asserts signature/headers/body; failure
│                                             #   → retry → DLQ; private-IP / metadata URL rejected by SSRF guard; no code
│                                             #   path returns delivered without an HTTP response
└── <audit emitter module>                    # [VERIFY][1E] module — locate `AuditAction` + the audit emit function; make
                                              #   emits transactional (same session) or outbox-backed; remove `except
                                              #   Exception: pass`; add the missing actions (CALL_MONITOR_STARTED,
                                              #   WEBHOOK_REPLAYED, BATCH_STARTED, RETENTION_RUN, …) so no action is logged
                                              #   under the wrong label (`RESOURCE_EXPORTED` is used for monitoring today)

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_1_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
python -c "from app.main import app; print('routes:', len(app.routes))"   # new routers registered, no /endpoint-N routes
alembic upgrade head
alembic heads                                       # exactly ONE head
alembic downgrade -1 && alembic upgrade head           # migration is reversible
pytest -q tests/webhooks/test_call_event_bridge.py
pytest -q tests/webhooks/test_webhook_lifecycle_real.py
pytest -q tests/webhooks/test_webhook_isolation.py
pytest -q tests/campaign/test_batch_dialing.py
pytest -q tests/campaign/test_dnc_enforcement.py
pytest -q tests/campaign/test_dialer_limits.py
pytest -q tests/telephony/test_post_call_pipeline.py
pytest -q tests/ai/test_post_call_llm.py
pytest -q tests/compliance/test_retention_enforcement.py
pytest -q tests/compliance/test_pii_redaction_pipeline.py
pytest -q tests/security/test_no_process_local_state.py
pytest -q tests/security/test_audit_durability.py
pytest -q tests/integrations/test_salesforce_provider.py
pytest -q tests/telephony/test_experiment_assignment.py
VOXDESK_REAL_INTEGRATION=1 pytest -q -m real_provider tests/integrations/test_salesforce_real_provider.py        # needs real credentials (skipped otherwise)
VOXDESK_REAL_INTEGRATION=1 pytest -q -m real_provider tests/integrations/test_crm_real_provider_matrix.py        # needs real credentials (skipped otherwise)

# ACCEPTANCE [1A]: a real local receiver gets `call_ended` after a Twilio-test call (or a replayed provider status
#   callback), signature verifies, retry and DLQ work, SSRF cases rejected, `scripts/fake_success_allowlist.txt` no longer
#   lists `webhook_lifecycle_routes.py`.
# ACCEPTANCE [1B]: a 20-recipient batch against Twilio test numbers dials with the configured pacing; adding a number to
#   DNC stops its call on every path; `tests/campaign/test_concurrency.py` still green.
# ACCEPTANCE [1C]: after a completed call the DB contains summary, sentiment, outcome and all custom fields; a
#   `call_analyzed` webhook arrives; a QA review run exists when sampled.
# ACCEPTANCE [1D]: retention days honored per kind (transcripts / recordings / analysis); zero-retention and legal hold
#   work; rows are really deleted; transcript/webhook/analysis payloads contain no raw PII when redaction is enabled.
# ACCEPTANCE [1E]: no module-level *_cache / *_buckets / *_states dicts remain in app/api; idempotency and rate limits
#   hold across two workers; an audit write failure fails the action or lands in the outbox, never silently dropped.
# ACCEPTANCE [1F]: Salesforce sandbox round trip (marker real_provider, VOXDESK_REAL_INTEGRATION=1) creates/updates a
#   Lead or Contact and logs a Task; no fabricated tokens or data anywhere; tokens stored with AES-GCM; the write-back log
#   contains only real attempts.
# ACCEPTANCE [1G]: deterministic traffic split within tolerance over N calls; sticky assignment works; promote changes
#   the live AgentVersion; metrics are computed from calls, not stored.

# NEXT: SELL PROMPT 3 of 10 — PART 2: VOICE RUNTIME PARITY. Start it only after every acceptance command above passes
#   and reports/PART_1_REPORT.md exists.
```
