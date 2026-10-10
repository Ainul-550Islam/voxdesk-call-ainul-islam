# SELL PROMPT 6 of 10 — PART 5: Conversation-Flow Builder

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 6 of 10 — PART 5: CONVERSATION-FLOW BUILDER   (Gate G6)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0) and SELL PROMPT 3 sub-part 2E (RuntimeConfig + AgentVersion-driven runtime)
#   accepted.
# MISSION: Ship the conversation-flow builder: validated flow model, FlowRunner wired into the voice pipeline, React
#   Flow canvas with validation, agent detail pages with versions, publish and rollback.
# ORDER OF WORK: Write ADR-002 (flow engine choice) first, then backend, then UI.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# FACTS: `app/builder/node.py` defines a node/edge data contract with DAG validation; `workflow_executor.py` is not
#   called by any call path; neither dashboard has a canvas library. `pipecat-ai-flows` is not installed.
# ADR: `docs/adr/ADR-002-flow-engine.md` decides between (a) custom `FlowRunner` reusing the existing node contract
#   (default recommendation) and (b) adding `pipecat-ai-flows` if it is compatible with the pinned Pipecat. Record
#   versions, trade-offs, test plan.
# CLOSES unwired-table rows: W-12 AgentTool, WorkflowTrigger, KnowledgeCollection
# CLOSES Retell parity rows: #8 Visual conversation-flow builder
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
│   │   └── flow_processor.py         # [NEW] module — pipecat FrameProcessor adapter: swaps the system prompt/tool list on
│   │                                 #   node transitions (LLMMessagesUpdateFrame), forwards user turns to FlowRunner
│   ├── api/
│   │   └── agent_flow_routes.py      # [NEW] route — GET/PUT draft flow, validate, text-only simulate (scripted user turns
│   │                                 #   through FlowRunner), publish via the existing AgentVersion path
│   ├── builder/
│   │   ├── flow_runner.py            # [NEW] module — class FlowRunner: current node, builds LLM messages/tools for the
│   │   │                             #   node, evaluates edges after each turn (deterministic equations first; prompt
│   │   │                             #   conditions judged by a small fast model with timeout via an injected judge), updates
│   │   │                             #   dynamic variables, triggers function/transfer/DTMF/SMS nodes; fully testable without
│   │   │                             #   an LLM
│   │   ├── flow_validation.py        # [NEW] module — graph validation (reachability, dead ends, unique ids, undefined
│   │   │                             #   variables, unreachable globals) returning structured errors with node ids; used by
│   │   │                             #   UI + publish gate
│   │   ├── node.py                   # [MODIFY] module — node types: start, conversation (prompt + node tools), logic_split
│   │   │                             #   (equation | prompt condition), function (HTTP tool ref), transfer (cold/warm),
│   │   │                             #   press_digit, send_sms, extract_variables, end, subagent (agent ref); edge
│   │   │                             #   conditions; global nodes; per-node model/voice overrides; JSON-Schema export
│   │   └── workflow_executor.py      # [MODIFY] module — keep for non-voice workflow automations; rename docstring to avoid
│   │                                 #   confusion with FlowRunner
│   └── domain/
│       └── agent_models.py           # [VERIFY] schema — AgentVersion config gains `mode: single_prompt | flow` + `flow`
│                                     #   object; publish validates via flow_validation
├── dashboard-next/
│   ├── app/
│   │   └── dashboard/
│   │       └── agents/
│   │           ├── [id]/
│   │           │   ├── flow/
│   │           │   │   └── page.tsx  # [NEW] ui-page — React Flow canvas: palette, drag/drop nodes, edge condition editor,
│   │           │   │                 #   node inspector (prompt, tools, variables), mini-map, validation panel, undo/redo,
│   │           │   │                 #   autosave draft, publish
│   │           │   └── page.tsx      # [NEW] ui-page — agent detail tabs: Config | Flow | Versions (diff, publish,
│   │           │                     #   rollback) | Tools | Knowledge | Test
│   │           └── page.tsx          # [NEW] ui-page — multi-agent list (create/duplicate/archive). `/dashboard/agent` is
│   │                                 #   the only existing agent page: if it already manages several agents, extend it
│   │                                 #   instead and record the decision in the PART report
│   ├── components/
│   │   └── flow/
│   │       ├── EdgeCondition.tsx     # [NEW] ui-component — edge condition editor (equation builder / prompt condition)
│   │       ├── Inspector.tsx         # [NEW] ui-component — right-hand node inspector (prompt, tools, variables, overrides)
│   │       ├── NodeCard.tsx          # [NEW] ui-component — node renderer per node type
│   │       ├── Palette.tsx           # [NEW] ui-component — draggable node palette
│   │       └── ValidationPanel.tsx   # [NEW] ui-component — lists flow_validation errors, click-to-focus node
│   ├── lib/
│   │   ├── api.ts                    # [MODIFY] module — typed client calls for flow/agents/versions endpoints
│   │   └── flow-schema.ts            # [NEW] module — TS types + zod schema mirroring the backend JSON Schema; round-trip
│   │                                 #   fixtures shared with backend tests
│   ├── tests/
│   │   └── flow-editor.test.tsx      # [NEW] test — add/connect/delete nodes, validation display, schema round-trip
│   └── package.json                  # [MODIFY] config — add `@xyflow/react` and an auto-layout lib (dagre/elkjs)
├── docs/
│   └── adr/
│       └── ADR-002-flow-engine.md    # [NEW] doc — decision record (see above)
└── tests/
    ├── agent/
    │   └── test_flow_processor.py    # [NEW] test — prompt/tool swapping through a fake pipeline
    └── builder/
        ├── test_flow_runner.py       # [NEW] test — transitions with fake judge, variable updates, function/transfer/DTMF
        │                             #   nodes
        └── test_flow_validation.py   # [NEW] test — valid/invalid graphs, structured errors

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_5_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
python -c "from app.main import app; print('routes:', len(app.routes))"   # new routers registered, no /endpoint-N routes
pytest -q tests/builder/test_flow_validation.py
pytest -q tests/builder/test_flow_runner.py
pytest -q tests/agent/test_flow_processor.py
(cd dashboard-next && npx vitest run tests/flow-editor.test.tsx)
(cd dashboard-next && npx tsc --noEmit && npx vitest run)

# ACCEPTANCE: a flow built in the UI, published, and bound to a number drives a real call through ≥3 nodes (conversation
#   → function → transfer) — recorded, with node-transition events in the call timeline.

# NEXT: SELL PROMPT 7 of 10 — PART 6: TESTING, QA, ANALYTICS AND MESSAGING. Start it only after every acceptance command
#   above passes and reports/PART_5_REPORT.md exists.
```
