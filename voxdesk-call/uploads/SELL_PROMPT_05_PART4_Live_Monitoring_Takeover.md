# SELL PROMPT 5 of 10 — PART 4: Live Monitoring and Takeover

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 5 of 10 — PART 4: LIVE MONITORING AND TAKEOVER   (Gate G5)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0), SELL PROMPT 3 sub-part 2D (tools, provider capability flags) and SELL PROMPT 2
#   sub-part 1E (durable idempotency / rate limit / audit) accepted.
# MISSION: Make live monitoring real: listen (audio + transcript fan-out), whisper-to-AI (guidance injected into the
#   agent context) and supervisor takeover through a provider TwiML replacement, with RBAC, audit and durable sessions.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# FACTS: `start_monitoring` writes a `LiveCallSession` row and an audit entry; it never joins audio (F-03). Design (all
#   real): *listen* = tee the pipeline's caller/agent PCM + transcript to a Redis fan-out and stream to authorized
#   supervisors; *whisper* = inject guidance into the AI's context (`LLMMessagesAppendFrame`), because the agent is not a
#   human participant; *takeover* = provider REST update replaces the call TwiML (`<Dial>`/`<Conference>`) which closes
#   the media stream, then bridges the supervisor.
# CLOSES facade rows: F-03 app/api/live_monitoring_routes.py::start_monitoring
# CLOSES unwired-table rows: W-09 LiveCallSession
# CLOSES Retell parity rows: #31 Live call monitoring / takeover
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
│   ├── agent/
│   │   ├── monitor_tap.py                 # [NEW] module — FrameProcessor placed in the pipeline: publishes caller/agent
│   │   │                                  #   PCM + transcript to monitor_bus only while sessions exist (zero overhead
│   │   │                                  #   otherwise); method inject_guidance(text) → LLMMessagesAppendFrame (+
│   │   │                                  #   LLMRunFrame) = whisper-to-AI
│   │   └── pipeline.py                    # [MODIFY] module — insert monitor_tap; on client disconnect check
│   │                                      #   `call.takeover_pending` so a takeover is NOT finalized as a hang-up
│   ├── api/
│   │   ├── ws/
│   │   │   └── monitor_ws.py              # [NEW] route — WebSocket `/ws/monitor/{call_id}`: short-lived token auth,
│   │   │                                  #   streams PCM16 + transcript JSON, closes when the call ends, max N supervisors
│   │   │                                  #   per call
│   │   └── live_monitoring_routes.py      # [MODIFY] route — start/stop session (permission `calls.monitor`, per-tenant,
│   │                                      #   correct AuditAction); modes listen | whisper_ai | takeover; idempotency + rate
│   │                                      #   limit via shared durable stores (1E); LiveCallSession durable with
│   │                                      #   heartbeat/expiry; delete the row-only logic
│   └── telephony/
│       ├── providers/
│       │   ├── base.py                    # [MODIFY] module — adapter method bridge_to(external_id, destination, *,
│       │   │                              #   whisper_text=None) + capability flag supports_takeover
│       │   └── twilio.py                  # [MODIFY] module — implement bridge_to via call update (TwiML replacement);
│       │                                  #   UnsupportedCapability on other providers until implemented
│       ├── monitor_bus.py                 # [NEW] module — Redis pub/sub fan-out of per-call audio frames + transcript
│       │                                  #   events (bounded queues, drop-oldest, no PII in logs); producers: pipeline tap;
│       │                                  #   consumers: monitor WebSocket
│       └── takeover.py                    # [NEW] module — fn takeover(call, supervisor_destination|webrtc_client): set
│                                          #   call.takeover_pending, provider REST update with new TwiML
│                                          #   (`<Dial>`/`<Conference>`), state-machine events
│                                          #   (takeover_started/completed/failed), recording continuity, rollback to the AI
│                                          #   when the supervisor leg fails
├── dashboard-next/
│   ├── app/
│   │   └── dashboard/
│   │       └── live/
│   │           └── page.tsx               # [NEW] ui-page — active calls (from the realtime gateway), Listen button
│   │                                      #   (WebAudio player), live transcript, whisper input, Takeover button with
│   │                                      #   confirmation + required note (audited)
│   └── components/
│       └── enterprise/
│           └── live-call-card.tsx         # [NEW] ui-component — one live call: status, duration, sentiment, controls
└── tests/
    ├── api/
    │   └── test_live_monitoring_authz.py  # [NEW] test — RBAC, tenant isolation, token expiry, audit actions correct
    └── telephony/
        ├── test_monitor_bus.py            # [NEW] test — fan-out, backpressure drop, no frames when no listeners
        └── test_takeover.py               # [NEW] test — takeover_pending prevents finalization; provider fake receives
                                           #   TwiML replacement; failure rolls back to the AI

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_4_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
python -c "from app.main import app; print('routes:', len(app.routes))"   # new routers registered, no /endpoint-N routes
pytest -q tests/telephony/test_monitor_bus.py
pytest -q tests/telephony/test_takeover.py
pytest -q tests/api/test_live_monitoring_authz.py
(cd dashboard-next && npx tsc --noEmit && npx vitest run)

# ACCEPTANCE: recorded demo — supervisor listens, whispers guidance that visibly changes the agent's next answer, then
#   takes over; audit trail contains correct actions.

# NEXT: SELL PROMPT 6 of 10 — PART 5: CONVERSATION-FLOW BUILDER. Start it only after every acceptance command above
#   passes and reports/PART_4_REPORT.md exists.
```
