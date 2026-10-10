# SELL PROMPT 4 of 10 — PART 3: Web Calls, Widget and SDKs

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 4 of 10 — PART 3: WEB CALLS, WIDGET AND SDKs   (Gate G4 and part of G8)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0) and SELL PROMPT 3 sub-part 2E (RuntimeConfig resolver, number-to-agent binding)
#   accepted.
# MISSION: Make browser calling real: web-call API, browser transport, web/react/node/python SDKs, embeddable widget and
#   an n8n connector, and remove the fake media-stream path that answers with a template string and fake audio.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# FACTS: the web-call API only issues a token (with a `tok_{uuid}_{call_id}` fallback), no browser client exists
#   anywhere in the repo, and the generic `/calls/{call_id}/media-stream` WebSocket answers with a template string and
#   text bytes as "audio" (F-05, F-06). The widget service already returns `NOT_CONFIGURED` for voice (keep that honesty).
# DESIGN: MVP transport = Pipecat `FastAPIWebsocketTransport` + `ProtobufFrameSerializer` (PCM16 mono 16 kHz). Upgrade
#   path = `SmallWebRTCTransport` (`pipecat.transports.network.small_webrtc`). The browser side may build on Pipecat's
#   official JS client packages (`@pipecat-ai/client-js` + a websocket/small-webrtc transport package) — verify on npm,
#   pin versions, record licenses; otherwise implement the thin client below.
# CLOSES facade rows: F-05 app/telephony/realtime.py::RealtimeVoiceSessionOrchestrator.handle_ws_message, mounted at
#   app/api/v1/telephony_routes.py /calls/{call_id}/media-stream | F-06 app/api/outbound_call_routes.py web-call token
#   path
# CLOSES Retell parity rows: #22 Web calls, widget, web SDK | #37 SDKs | #38 Automation connectors
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
│   │   ├── v1/
│   │   │   └── telephony_routes.py       # [MODIFY] route — DELETE the `/calls/{call_id}/media-stream` handler that calls
│   │   │                                 #   RealtimeVoiceSessionOrchestrator.handle_ws_message (template reply + fake audio)
│   │   ├── outbound_call_routes.py       # [MODIFY] route — delete the old web-call token path (tok_{uuid} fallback);
│   │   │                                 #   delegate to web_call.create_web_call
│   │   ├── public_widget_routes.py       # [MODIFY] route — widget bootstrap endpoint returns the real web-call bootstrap
│   │   │                                 #   (public key + origin check) when configured, NOT_CONFIGURED otherwise
│   │   ├── web_call_live_routes.py       # [NEW] route — POST /api/web-calls (server key), POST /api/public/web-calls
│   │   │                                 #   (public key + Origin check), GET /api/web-calls/{id}, POST
│   │   │                                 #   /api/web-calls/{id}/end; registered in app/main.py
│   │   └── web_call_routes.py            # [KEEP] route — existing TEXT-based test-session API (prefix
│   │                                     #   /api/v1/testing/web-calls, backed by simulation_service); it is NOT a browser
│   │                                     #   call API. Do not extend it for live audio; keep its OpenAPI tag
│   │                                     #   `testing-web-calls`
│   ├── services/
│   │   ├── public_key_service.py         # [VERIFY] module — public keys scoped to agent + allowed origins + rate limits;
│   │   │                                 #   used by /api/public/web-calls
│   │   └── public_widget_service.py      # [MODIFY] module — voice mode returns the real web-call bootstrap when
│   │                                     #   configured; NOT_CONFIGURED otherwise
│   ├── telephony/
│   │   ├── media_gateway.py              # [VERIFY] module — keep only if wired to a real transport, otherwise delete
│   │   ├── realtime.py                   # [MODIFY] module — after removing the fabricated reply/audio keep only
│   │   │                                 #   bookkeeping that real code uses (verify with grep); if nothing uses it, delete
│   │   │                                 #   the file and its tests
│   │   ├── web_call.py                   # [NEW] module — create_web_call(session, tenant, agent_id, version, dynamic_vars,
│   │   │                                 #   metadata) → {call_id, access_token (short-lived single-use JWT bound to call_id
│   │   │                                 #   + origin), transport, url}; NO fallback tokens; per-public-key rate limit;
│   │   │                                 #   origin allowlist via app/services/public_origin_service.py; RuntimeConfig from
│   │   │                                 #   PART 2E resolver; creates Call(direction="web")
│   │   └── web_transport.py              # [NEW] module — browser transport: FastAPIWebsocketTransport +
│   │                                     #   ProtobufFrameSerializer at /telephony/web/ws; SmallWebRTC signaling at
│   │                                     #   /telephony/web/offer (phase 2); both call run_voice_agent(RuntimeConfig) with
│   │                                     #   the same latency/usage/guardrail wiring as phone calls
│   └── main.py                           # [MODIFY] module — register web_call_live_routes (and the monitor WebSocket in
│                                         #   PART 4); keep the existing web_call_router (testing API)
├── dashboard-next/
│   └── app/
│       └── dashboard/
│           └── agents/
│               └── [id]/
│                   └── test-call/
│                       └── page.tsx      # [NEW] ui-page — "Test your agent" browser call panel using @voxdesk/web-sdk (mic
│                                         #   permission, live transcript, latency badge)
├── integrations/
│   ├── n8n/                              # [NEW] dir — n8n community node package: Trigger node (subscribe/unsubscribe
│   │                                     #   through /api/webhooks), Action nodes (create call, create web call, create
│   │                                     #   batch, get call); real API calls only
│   └── zapier/                           # [NEW] dir — OPTIONAL Zapier app definition on the same API surface
├── sdk/
│   ├── node/
│   │   ├── src/
│   │   │   └── index.ts                  # [NEW] sdk — typed fetch client + verifyWebhookSignature() + pagination helpers
│   │   └── package.json                  # [NEW] config — `@voxdesk/node`: types generated from the OpenAPI
│   │                                     #   (openapi-typescript) + handwritten helpers
│   ├── python/
│   │   └── voxdesk/
│   │       └── client.py                 # [NEW] sdk — expanded Python client (sync + async): calls, agents, versions,
│   │                                     #   phone numbers, web-calls, batch, webhooks (+ signature verification helper),
│   │                                     #   retries/timeouts, pydantic models
│   ├── react/
│   │   ├── src/
│   │   │   └── index.tsx                 # [NEW] sdk — hook + component wrapping the web client
│   │   └── package.json                  # [NEW] config — `@voxdesk/react` (useVoxDeskCall hook + <VoxDeskCallButton/>)
│   ├── web/
│   │   ├── src/
│   │   │   ├── audio/
│   │   │   │   └── worklet-capture.ts    # [NEW] sdk — AudioWorklet capture (mono 16 kHz PCM16),
│   │   │   │                             #   echoCancellation/noiseSuppression constraints, jitter-safe playback queue
│   │   │   ├── transport/
│   │   │   │   ├── webrtc.ts             # [NEW] sdk — SmallWebRTC signaling client (phase 2)
│   │   │   │   └── ws-protobuf.ts        # [NEW] sdk — WebSocket transport speaking Pipecat protobuf frames (generate from
│   │   │   │                             #   the pipecat .proto; do not hand-roll)
│   │   │   └── index.ts                  # [NEW] sdk — class VoxDeskWebClient { startCall({accessToken}), stopCall(),
│   │   │                                 #   mute(), unmute(), sendDtmf(), on(event) }; events: call_started, call_ended,
│   │   │                                 #   agent_start_talking, agent_stop_talking, transcript_update, error
│   │   ├── tests/
│   │   │   └── client.test.ts            # [NEW] test — mocked WebSocket/AudioContext: event sequence, mute, reconnect,
│   │   │                                 #   teardown
│   │   └── package.json                  # [NEW] config — `@voxdesk/web-sdk` (ESM + UMD via tsup), TypeScript strict,
│   │                                     #   vitest
│   └── client.py                         # [MODIFY] sdk — keep as an import-compat shim re-exporting
│                                         #   sdk/python/voxdesk/client.py
├── tests/
│   └── telephony/
│       ├── test_no_mock_media_stream.py  # [NEW] test — the fake `/calls/{id}/media-stream` route and `handle_ws_message`
│       │                                 #   template reply no longer exist
│       ├── test_web_call_flow.py         # [NEW] test — create web call, open the WS with a test client, stream fixture
│       │                                 #   audio through provider fakes (marked is_mock_provider), assert transcript events
│       │                                 #   + CallLatencyStat rows + usage metering
│       └── test_web_call_security.py     # [NEW] test — token single-use/expiry, origin allowlist, rate limit, cross-tenant
│                                         #   404
└── widget/
    └── embed.js                          # [NEW] sdk — one-line embeddable widget: data-public-key / data-agent-id
                                          #   attributes, floating button, permission prompt, call UI, theming via data
                                          #   attributes; served by public_widget_service with a CSP-safe loader

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_3_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
python -c "from app.main import app; print('routes:', len(app.routes))"   # new routers registered, no /endpoint-N routes
pytest -q tests/telephony/test_web_call_flow.py
pytest -q tests/telephony/test_web_call_security.py
pytest -q tests/telephony/test_no_mock_media_stream.py
(cd sdk/web && npx vitest run)
(cd dashboard-next && npx tsc --noEmit && npx vitest run)

# ACCEPTANCE: recorded browser call to a published agent (transcript + usage + latency stat stored); widget works on a
#   static test page; Node + Python SDK smoke tests green; allowlist no longer lists `outbound_call_routes.py` web-call
#   path.

# NEXT: SELL PROMPT 5 of 10 — PART 4: LIVE MONITORING AND TAKEOVER. Start it only after every acceptance command above
#   passes and reports/PART_3_REPORT.md exists.
```
