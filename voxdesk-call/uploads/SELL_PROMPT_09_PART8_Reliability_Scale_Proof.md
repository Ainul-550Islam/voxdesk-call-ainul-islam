# SELL PROMPT 9 of 10 — PART 8: Reliability and Scale Proof

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 9 of 10 — PART 8: RELIABILITY AND SCALE PROOF   (Gate G9 (part))
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0), SELL PROMPT 3 sub-parts 2A (latency) and 2C (failover) accepted, so load and
#   chaos results are meaningful.
# MISSION: Produce real capacity, chaos and disaster-recovery evidence: load/capacity tests on the real voice path,
#   graceful drain, HPA/PDB settings, SLOs, runbooks, and the decision to delete the pcap facade.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# CLOSES unwired-table rows: W-10 PcapArtifact
# CLOSES Retell parity rows: #39 Scale & reliability evidence
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
│   │   └── pcap_routes.py        # [DELETE] route — no capture implementation exists (W-10); default decision = delete
│   │                             #   route + PcapArtifact model (migration drops table). Only keep if a real capture sidecar
│   │                             #   is built and tested in this PART
│   └── core/
│       └── graceful_shutdown.py  # [NEW] module — drain mode: stop accepting new calls, finish active calls (bounded wait),
│                                 #   flush outbox/jobs; wired into api and worker entrypoints
├── docs/
│   ├── RUNBOOKS/
│   │   ├── db-failover.md        # [NEW] doc — failover and restore steps
│   │   ├── high-latency.md       # [NEW] doc — dashboards to check, common causes, mitigations
│   │   ├── provider-outage.md    # [NEW] doc — STT/TTS/LLM/carrier outage: detection, failover, manual steps
│   │   └── webhook-backlog.md    # [NEW] doc — outbox/DLQ growth, redrive procedure
│   ├── CAPACITY_MODEL.md         # [NEW] doc — measured calls per vCPU/GB, bottlenecks (Silero/smart-turn CPU, websocket
│   │                             #   fan-out, DB), scaling guidance, hardware used; real numbers only
│   └── SLO-ALERTS.md             # [MODIFY] doc — SLOs: API availability, p95 e2e voice latency (from 2A), webhook delivery
│                                 #   success, post-call completion time
├── infra/
│   └── helm/                     # [MODIFY] infra — HPA on concurrent-calls metric, PodDisruptionBudget,
│                                 #   terminationGracePeriod matching drain, readiness gates (DB, Redis, provider health)
├── loadtest/
│   ├── locustfile.py             # [MODIFY] script — API mix scenarios (calls list, analytics, webhooks) plus
│   │                             #   `voice_ws_user`; safety guard from loadtest/safety.py stays mandatory
│   ├── safety.py                 # [KEEP] module — prevents load tests against production
│   ├── voice_capacity_test.py    # [NEW] script — ramps concurrent calls 10→N on one worker, records CPU/RAM/latency per
│   │                             #   call, finds the knee point; outputs the capacity model data
│   └── voice_ws_user.py          # [NEW] script — synthetic Twilio media-stream client with recorded caller audio against
│                                 #   the REAL /telephony/ws path (provider fakes allowed for cost; flagged in the report)
├── observability/
│   └── slos.yml                  # [MODIFY] config — SLO definitions matching the doc
├── scripts/
│   └── dr_drill.sh               # [NEW] script — scripted restore drill using backup.sh/restore.sh/backup_verify.sh;
│                                 #   records measured RPO/RTO
└── tests/
    └── resilience/
        └── test_chaos_calls.py   # [NEW] test — app/core/chaos.py injects provider/DB/Redis faults while calls run; assert
                                  #   call continuity or clean failure + correct events

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_8_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
pytest -q tests/resilience/test_chaos_calls.py

# ACCEPTANCE: `docs/CAPACITY_MODEL.md` generated from a real ramp test; chaos test green; DR drill log with measured
#   RPO/RTO; SLO dashboards live.

# NEXT: SELL PROMPT 10 of 10 — PART 9: PACKAGING AND SALES PROOF. Start it only after every acceptance command above
#   passes and reports/PART_8_REPORT.md exists.
```
