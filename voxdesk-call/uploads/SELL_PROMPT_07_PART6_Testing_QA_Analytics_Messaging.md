# SELL PROMPT 7 of 10 — PART 6: Testing, QA, Analytics and Messaging

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 7 of 10 — PART 6: TESTING, QA, ANALYTICS AND MESSAGING   (Gate G7 and part of G8)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0), SELL PROMPT 2 sub-part 1C (post-call pipeline) and SELL PROMPT 3 sub-part 2A
#   (latency stats) accepted.
# MISSION: Close the quality loop: LLM-played-caller simulations, regression tests created from real calls, automatic
#   QA, analytics and custom dashboards, SMS/chat messaging, and the dashboard pages for webhooks, tests, experiments,
#   tools and batch calls.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# CLOSES facade rows: F-12 app/api/multichannel_routes.py, app/integrations/connector.py | F-15
#   app/api/call_simulation_routes.py
# CLOSES unwired-table rows: W-11 MessageChannel
# CLOSES Retell parity rows: #23 Chat / SMS agents | #27 Automated QA on all calls | #28 Simulation testing & regression
#   from production calls | #30 Analytics & custom dashboards | #32 AI copilot (Conductor)
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
├── app/
│   ├── api/
│   │   ├── analytics_dashboard_routes.py  # [NEW] route — CRUD + server-side aggregation queries (tenant-scoped,
│   │   │                                  #   size-limited)
│   │   ├── call_simulation_routes.py      # [MODIFY] route — real run lifecycle + results (per-turn transcript, verdicts,
│   │   │                                  #   cost); remove default `allow_mock_fallback=True` (explicit demo flag only)
│   │   ├── multichannel_routes.py         # [MODIFY] route — rewrite on top of app/messaging/* with real receipts, or
│   │   │                                  #   delete if SMS/WhatsApp adapters are out of scope (then remove MessageChannel)
│   │   └── simulation_routes.py           # [VERIFY] route — two simulation APIs exist; merge into one contract and keep
│   │                                      #   the other as a deprecated alias
│   ├── messaging/
│   │   ├── __init__.py                    # [NEW] package
│   │   └── sms.py                         # [NEW] module — Twilio Messaging webhook → chat agent → reply; STOP/HELP
│   │                                      #   keywords and opt-out → DNC (1B); delivery-status callbacks; signature
│   │                                      #   verification; NOT_CONFIGURED without credentials
│   ├── qa/
│   │   └── service.py                     # [MODIFY] service — ensure auto-review runs from POST_CALL (1C): sampling
│   │                                      #   policies (percentage, negative sentiment, transfers, long calls), rubric
│   │                                      #   scoring, results exposed to dashboard + webhooks
│   └── services/
│       ├── analytics_service.py           # [MODIFY] service — metrics: success rate, avg/p95 duration, cost per call
│       │                                  #   (app/ai/costs.py + telephony usage), latency p50/p95 (CallLatencyStat),
│       │                                  #   transfer/voicemail rate, disconnect reasons, sentiment; group by
│       │                                  #   agent/version/number/day
│       ├── chat_agent_service.py          # [VERIFY] service — confirm chat agents use the same AgentVersion/RuntimeConfig
│       │                                  #   path (2E); fix if they read Tenant fields
│       ├── conductor_evidence_service.py  # [VERIFY] service — conductor proposals must cite REAL call evidence and apply
│       │                                  #   to AgentVersion drafts; add a test that fabricated evidence is rejected
│       ├── custom_dashboard_service.py    # [NEW] service — saved dashboards: widgets (metric, dimension, filter, chart
│       │                                  #   type), per-tenant, shareable link, CSV export
│       ├── regression_from_calls.py       # [NEW] service — convert a real call (transcript + tool calls + outcome) into a
│       │                                  #   test case: caller turns as script, assertions drafted by an LLM and marked
│       │                                  #   `needs_review`, PII-redacted per policy
│       ├── simulation_caller.py           # [NEW] module — class SimulatedCaller (LLM via app/ai/gateway.py): persona,
│       │                                  #   goal, variables, interruption style, seed; transcript capture
│       └── simulation_service.py          # [MODIFY] service — add LLM-simulated-caller mode with success criteria + judge
│                                          #   verdict/rationale next to the existing scripted mode; every result carries
│                                          #   `is_mock_provider`; mocked runs are excluded from pass-rate/KPIs; runs through
│                                          #   JobType.EVALUATION
├── dashboard-next/
│   └── app/
│       └── dashboard/
│           ├── analytics/
│           │   └── custom/
│           │       └── page.tsx           # [NEW] ui-page — widget builder + saved dashboards
│           ├── batch-calls/
│           │   └── page.tsx               # [NEW] ui-page — batch create (CSV upload + variables), live progress, outcomes,
│           │                              #   pause/resume/cancel
│           ├── experiments/
│           │   └── page.tsx               # [NEW] ui-page — A/B experiments: variants, traffic split, computed metrics with
│           │                              #   confidence, promote
│           ├── tests/
│           │   └── page.tsx               # [NEW] ui-page — test suites, scenario editor (scripted or simulated caller),
│           │                              #   run results, "create regression test from this call" action on
│           │                              #   /dashboard/calls/[id]
│           ├── tools/
│           │   └── page.tsx               # [NEW] ui-page — tool registry: HTTP tool editor (schema, URL, headers via
│           │                              #   secrets), test-invoke, per-agent attachment
│           └── webhooks/
│               └── page.tsx               # [NEW] ui-page — subscriptions, deliveries (status/latency/response), test send,
│                                          #   retry, DLQ redrive, secret rotation
└── tests/
    ├── messaging/
    │   └── test_sms_flow.py               # [NEW] test — inbound SMS → chat agent → reply; STOP → DNC; signature failure
    │                                      #   rejected
    └── services/
        ├── test_custom_dashboards.py      # [NEW] test — aggregation correctness, tenant scoping, limits
        ├── test_regression_from_calls.py  # [NEW] test — real call → test case; PII redaction; `needs_review` flag
        └── test_simulation_caller.py      # [NEW] test — deterministic seed, goal/criteria judged, mock runs excluded from
                                           #   KPIs

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_6_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
python -c "from app.main import app; print('routes:', len(app.routes))"   # new routers registered, no /endpoint-N routes
pytest -q tests/services/test_simulation_caller.py
pytest -q tests/services/test_regression_from_calls.py
pytest -q tests/services/test_custom_dashboards.py
pytest -q tests/messaging/test_sms_flow.py
(cd dashboard-next && npx tsc --noEmit && npx vitest run)

# ACCEPTANCE: an LLM-caller suite runs against a published version and reports pass/fail with rationale; a real call
#   becomes a regression test; auto-QA runs on sampled real calls; one A/B experiment splits live traffic and shows
#   computed metrics; custom dashboard saved and shared.

# NEXT: SELL PROMPT 8 of 10 — PART 7: ENTERPRISE, COMPLIANCE AND SECURITY. Start it only after every acceptance command
#   above passes and reports/PART_6_REPORT.md exists.
```
