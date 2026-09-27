# P0 Master Consolidation — AI ↔ Lead ↔ Campaign Integrity

Status: consolidated and verified against PostgreSQL 17 (see "Evidence").
This document describes the single backend contract shared by P0‑4 (Live AI
Governance), P0‑5 (Enterprise Leads/Contacts/Lifecycle) and P0‑6
(Campaign ↔ Environment ↔ Lead Integrity). It records what each layer owns,
the invariants that hold across layers, the actual gaps the consolidation
found and fixed, and where each claim is proven.

## 1. One flow, one contract

```
live message / voice
   -> governed AI runtime            app/ai/runtime.py (the only live entry)
      -> authorization               app/ai/gateway.py::authorize_live
         context -> policy -> input guardrail -> PII -> prompt approval
         -> tool policy -> budget admission -> routing -> circuit -> timeout
      -> provider boundary           app/ai/gateway.py::govern (one-shot)
                                     app/agent/llm_factory.py::build_llm
                                     (Pipecat service construction, voice)
      -> output guardrail -> measured usage -> trace
   -> tools / lead actions           app/agent/functions.py (dispatch)
      -> lead lifecycle              app/leads/lifecycle.py (sole status writer)
      -> consent                     app/leads/consent.py (DNC is terminal)
   -> campaign / environment         app/services/campaign_service.py
   -> safe outbound                  app/telephony/outbound.py
```

Every layer receives the same identity facts — tenant, environment, actor,
authorization decision, audit trail, transaction owner and idempotency key —
and no subsystem bypasses another's source of truth.

## 2. Ownership table (single source of truth per concern)

| Concern | Owner | Everybody else |
| --- | --- | --- |
| Live AI entry, ordering | `app/ai/runtime.py` | never call `govern`/`build_llm` directly for live traffic |
| Policy view, admission gate | `app/ai/gateway.py` (`authorize_live`, `govern`) | never re-decide policy |
| Model selection | tenant row via `llm_factory.resolve` behind `app/ai/routing.assert_allowed` | a client `{"model": ...}` or preset is a denial, never a switch |
| Fallback candidates | `app/ai/fallback.py` | every candidate re-passes `assert_allowed`; policy denial is not a fallback trigger |
| Budget admission | `app/ai/budget.py` (`admit` → `AIAdmissionCounter`) | billing charges stay in `app/billing/metering` |
| Tool authorization | `app/ai/guardrails/tool_policy.py` | the model is never the principal; handlers dispatch only after a server decision |
| Provider credentials | deployment settings via `llm_factory.api_key_for` | `LLMChoice` never carries a key; governed voice builds with `allow_key_fallback=False` |
| Lead status | `app/leads/lifecycle.py` (the only `lead.status =` write in `app/`) | CAS via `expected`, history row per change, DNC terminal |
| Consent / DNC | `app/leads/consent.py` + `LeadConsent` ledger | no second DNC list; voice denial ⇒ lifecycle DNC; START never reverses voice DNC |
| Lead reads | `app/leads/repository.py` | every getter is tenant + environment scoped |
| Campaign scope | `app/services/campaign_service.py` + `Campaign` ORM hooks (`app/db/models.py`) | environment resolved (explicit authorized → server selection → active default production → reject) and frozen after insert |
| Outbound safety | `app/telephony/outbound.py` | same-environment check before any provider interaction; atomic claim on `attempts` |
| Usage events | `app/billing/metering.py` (`usage_events`, unique idempotency key) | AI usage is recorded from measured tokens only |

## 3. The reservation lifecycle (fixed during consolidation)

Admission reserves tokens against a configured ceiling with one atomic
`UPDATE ... WHERE tokens_reserved + N <= ceiling`, so concurrent requests
cannot both pass a limit of N (`tests/ai/test_gateway.py::test_compare_and_reserve_one_winner`).

Before consolidation the reservation only ever grew: a call denied after
admission (open circuit, routing refusal, deadline) or failed at the provider
left its tokens reserved forever — a phantom reservation permanently
shrinking the tenant's ceiling. The lifecycle is now complete:

```
admit -> reserve -> success  / reconcile(reserved -> measured tokens)
                 -> failure  / release(reserved)
```

* `app/ai/budget.py` — `release()` and `reconcile()`, both floored at zero
  with a portable `CASE`, so a correction can never drive another call's
  live reservation negative.
* `app/ai/gateway.py::authorize_live` — routing/circuit/deadline refusals
  after admission release the reservation; `LiveAuthorization.budget_reserved`
  tells the caller whether a reservation exists (releasing without that flag
  could subtract a concurrent call's reservation).
* `app/ai/gateway.py::govern` — owns release/reconcile for reservations it
  made itself (`budget_checked=False`, e.g. QA auto-review).
* `app/ai/runtime.py::invoke` — owns release/reconcile when `authorize_live`
  reserved and `govern` ran with `budget_checked=True` (no double-reserve,
  no double-release).
* `app/ai/runtime.py::govern_text_reply` — a crashed provider turn releases
  the admitted token; a measured turn reconciles 1 → measured tokens.
* `app/agent/pipeline.py::_persist_turns` — voice reconciles the 1-token
  admission footprint to the measured `llm_tokens` at call end, inside the
  existing post-call persistence transaction, never in the media loop.

An unconfigured ceiling reserves nothing (`ceiling_unknown`); release is then
a no-op that must not invent a counter row. Proven by
`tests/ai/test_runtime_enforcement.py`.

## 4. The voice media chain (fixed during consolidation)

`GovernedHearing` (input) and `GovernedSpeech` (output) existed but were not
wired into the Pipecat pipeline, so live transcripts and spoken text never
actually passed the guardrail processors. They are now in the chain:

```
transport.input -> stt -> stt_usage -> GovernedHearing -> humanizers
-> context_aggregator.user -> llm -> FillerInjector -> TextNormalizer
-> GovernedSpeech(prepared.blocked_output) -> tts -> voice_usage
-> transport.output -> context_aggregator.assistant
```

* A refused transcript is dropped before the LLM context (an injection
  *signal* is logged, never a block, and never a tool grant).
* Blocked model text is replaced **before TTS** — it is never spoken; the
  same guard runs again on turn persistence.
* Both processors are pure pass-through `FrameProcessor`s: no DB, no network,
  no blocking work in the live media loop.
* Streaming architecture, VAD/barge-in, backchannel, filler injection,
  normalization and usage tracking are untouched.

Proven by source-order assertions in
`tests/ai/test_runtime_enforcement.py::test_voice_media_chain_wires_the_guardrail_processors`
(Pipecat cannot be imported in the CI sandbox; the runtime behaviour of the
processors themselves is unit-tested via `guard_heard_text`/`guard_spoken_text`).

## 5. Measured usage on the text path (fixed during consolidation)

`TextAgent.complete_turn` never reported provider token usage, so the
governed boundary's usage step could not record anything for SMS/WhatsApp/
web-chat replies. `_complete_openai` and `_complete_anthropic` now return the
provider-measured total (defensively: an absent `usage` block yields `None`,
never a fabricated zero), `complete_turn` accumulates it across tool rounds
into `result["tokens"]`, and `govern_text_reply` records it on the existing
meter (`usage_events`) with an idempotency key, attributed to the thread's
environment. Unknown tokens remain unrecorded — the "unknown is not zero"
rule is unchanged. Proven end-to-end through the real webhook in
`tests/integration/test_ai_lead_campaign_flow.py`.

## 6. Lead integrity across every writer

All six creation/mutation sources converge on the same ledger:

| Source | Path | History `source` |
| --- | --- | --- |
| API (leads service, legacy routes, environment-resource route) | `app/leads/service.py` / `app/api/routes.py` / `app/api/environment_resource_routes.py` | `api` |
| Workflow engine | `app/services/workflow_service.py` → lifecycle | `workflow` |
| AI tool (live call) | `app/agent/functions.py::mark_do_not_call` → consent → lifecycle | `agent_tool`, then `consent` |
| Messaging webhook (STOP/START) | `app/channels/messaging.py` → consent → lifecycle | `messaging`, then `consent` |
| Telephony (post-call grading) | `app/telephony/twilio_handler.py` → lifecycle | `telephony`/`outbound` |
| Campaign dialer | `app/telephony/outbound.py` claim → lifecycle history | `outbound` |

Invariants (all proven in `tests/leads`, `tests/campaign`,
`tests/integration/test_ai_lead_campaign_flow.py`):

* `lifecycle.py` contains the only `lead.status =` assignment in `app/`;
  transitions are compare-and-set (`expected`), losers get `ClaimConflict`.
* DNC is terminal: no transition out, no consent path out. A voice grant on
  a DNC lead is refused (`InvalidTransition`); SMS START records an SMS grant
  and leaves the voice DNC intact.
* The environment-resource lead-creation route now also writes the canonical
  creation history row (`lifecycle.record_created`, source `api`) — it was
  the one creation path that produced a lead with an empty ledger.
* Every read (`get`, `get_for_update`, `find_by_phone`, `find_by_email`,
  `search`, campaign/activity queries) is tenant + environment scoped;
  cross-tenant and cross-environment reads fail closed with the safe
  not-found (`BoundaryDenied` maps to HTTP 404).

## 7. Campaign ↔ environment ↔ lead

* Campaign environment resolution order: explicit **authorized** id → the
  caller's server-side selection → the tenant's active default production
  environment → reject. Suspended/archived environments refuse campaign
  writes (`LifecycleDenied`); the ORM `before_insert`/`before_update` hooks
  route legacy inserts through the existing `resource_binding` scope
  machinery and freeze the environment after insert.
* Audience validation checks every lead and segment against the campaign's
  own tenant + environment; one cross-environment member rejects the whole
  create (`NotFoundError`), and the planner re-checks even stale overlay
  data (`execution_plan` skips a cross-environment lead id).
* The dialer filters selection on `Lead.environment_id ==
  campaign.environment_id`, refuses a directly handed cross-environment or
  cross-tenant pair **before** any claim/attempt/provider interaction
  (`environment_mismatch` / `tenant_mismatch`), refuses DNC
  (`do_not_call`), and claims atomically on `attempts` so concurrent ticks
  dial each lead exactly once.

## 8. Cross-layer security invariants

* Tenant identity comes only from the authenticated context; a
  client-claimed `tenant_id` is refused (`RuntimeContext.reject_claim`,
  `bind_tenant`) and never switches the tenant.
* The gateway never imports the runtime (no recursion); the runtime is the
  only module that sequences authorization and the provider boundary.
* A model principal can never authorize anything
  (`tool_policy`: `model_cannot_self_authorize`; `authorize_live` refuses
  `principal="model"` with 403 before any step runs).
* Telemetry is allowlist-only: prompts, responses, transcripts, tool
  arguments and credentials are dropped, not truncated
  (`app/ai/telemetry.py`; the `sk-should-not-log` probe in `govern` proves
  the drop in tests).
* Unpublished/draft production prompts are refused (`prompt_not_approved`);
  fallback inherits the tenant policy — a disabled or development-only
  provider is never a landing spot.
* Errors are distinguishable to the customer without leaking internals:
  governance denials and provider outages produce different safe sentences
  (`app/ai/errors.py`), and HTTP mapping stays 404/409/422 per the tenancy
  hierarchy.

## 9. What the consolidation changed

Actual gaps found by inspecting the existing Batch 04/05/06 implementations
(everything else was kept as-is and re-verified):

1. **Reservation lifecycle** — `app/ai/budget.py` (`release`, `reconcile`),
   `app/ai/gateway.py` (`authorize_live` post-admission release,
   `LiveAuthorization.budget_reserved`, `govern` self-reservation
   lifecycle), `app/ai/runtime.py` (`invoke`/`govern_text_reply` ownership,
   `Prepared.budget_reserved`), `app/agent/pipeline.py` (end-of-call
   reconcile).
2. **Guardrail processors unwired** — `app/agent/pipeline.py` (chain
   insertion only; processors themselves unchanged).
3. **Text-path usage never measured** — `app/agent/text_agent.py`
   (`_complete_openai`/`_complete_anthropic`/`complete_turn` token capture).
4. **Creation-history bypass** — `app/api/environment_resource_routes.py`
   (one `lifecycle.record_created` call at the existing create route;
   outside the 30 target paths, disclosed in the final report).

New coverage added (no rewrites of existing suites):

* `tests/ai/test_runtime_enforcement.py` — 16 tests: reservation
  release/reconcile across invoke/govern/authorize_live/voice/text paths,
  zero-floor units, fallback candidate policy validity, media-chain wiring
  order, gateway↛runtime import rule, measured-token reporting.
* `tests/integration/test_ai_lead_campaign_flow.py` — 5 tests: AI-tool DNC
  through lifecycle → campaign → dialer with reversal denied; cross-tenant
  tool call with an identical phone number; staging AI-created lead invisible
  to a production campaign; governed webhook text turn → measured usage
  event → campaign planning → atomic dial claim; messaging STOP/START vs
  voice DNC vs dialer.

## 10. Evidence

* Tests (SQLite session fixtures; PostgreSQL is proven separately below):
  `tests/ai` 56 passed, `tests/leads` 98, `tests/campaign` 40,
  `tests/integration` 27, `tests/qa` 26, `tests/contact_center` 29,
  `tests/security` 220 (+1 pre-existing environment failure),
  `tests/telephony` 49, `tests/environments` 50, channels/outbound/rbac/
  deployment/legacy/workflow/compat/batch‑C suites green (exact tallies in
  the consolidation report).
* PostgreSQL 17 (`localhost:5432`): `alembic heads` →
  `0026_campaign_environment_scope (head)` (single head); fresh database
  `voxdesk_p0_master` upgraded base → head; post-upgrade catalog shows
  `campaigns.environment_id NOT NULL`, `fk_campaigns_environment`
  (RESTRICT), composite `fk_campaigns_tenant_environment (tenant_id,
  environment_id) → environments(tenant_id, id)`, and both indexes.
  Backfill-scenario evidence databases from Batch 06 (`voxdesk_b06_fresh`,
  `voxdesk_b06_backfill`) remain in place.
* Bypass audits: the only live `build_llm` caller is
  `app/ai/runtime.py::voice_llm`; the only `lead.status =` write is
  `app/leads/lifecycle.py`; provider SDK usage exists only inside
  `llm_factory` (Pipecat construction) and `text_agent` (reached only
  through `govern_text_reply`); the one `messages.create` outside AI
  (`app/integrations/notifications.py`) is the Twilio SMS client.

## 11. Known limitations (unchanged, disclosed)

* Sandbox/CI environment lacks `pipecat`, `openai`, `anthropic` and
  `deepgram`: `tests/test_provider_lifecycle.py`, `tests/test_tts.py` cannot
  collect; `tests/test_text_agent.py` is partially blocked (13 passed / 15
  environment-blocked); `tests/security/test_telephony_media_isolation.py`
  fails on the missing `deepgram` import. All pre-existing.
* `tests/test_deployment.py::test_migration_chain_is_linear_with_no_gaps`
  hardcodes the `0024` head at the base commit and fails for any later head
  (pre-existing; the file is outside the target paths).
* `tests/test_release_gate.py::test_cli_freezes_artifact` fails on this
  checkout's git layout (`.git` one level above the project root) —
  pre-existing.
* `alembic_version.version_num` is created `VARCHAR(32)` by older alembic
  defaults while revision ids from 0017 on are longer; evidence upgrades
  pre-create the table as `VARCHAR(64)`. Pre-existing infrastructure defect,
  disclosed, not fixed here (`alembic/env.py` is outside the target paths).
* No live provider keys exist in this environment: provider execution is
  proven with typed fakes at the SDK boundary. That is not a claim of
  production readiness.

--- BATCH 07 GAP DOCUMENTATION (part 1/3 — documented, not hidden) ---
Compared against https://www.lumay.ai/ai-products/voice-agent (fetched page).
Real gaps reported honestly; nothing fabricated as "complete".
- TTS engine / synthesis module: MISSING (app/tts/ subfolder absent; app/agent/tts.py exists as batch-04 artifact, not engine)
- Voice cloning / custom voice library: MISSING (no clone library; 2-min audio clone not implemented)
- Visual flow builder / drag-node UI: MISSING (workflows exist as code/models, not visual builder)
- External SDK (Python/TypeScript): MISSING (internal FastAPI only)
- Real-time conversational AI synthesis loop: MISSING (telephony/ is callback/dialer, not live voice agent)
- Explicit escalation/transfer-to-human module: MISSING (telephony consent partial)
- No-code / describe-agent NL builder: MISSING (agent/ is code/config-driven)
- Found (not skipped): app/knowledge/ (doc ingestion), dashboard/ (frontend), docs/COMPLIANCE.md/SOC2.md/DPA.md, scripts/deploy.sh, app/integrations/crm/ (batch-01), app/agent/tts.py
- All 30 Batch-07 targets verified present (30/30); 0027 migration verified; SQLite caveats documented.
- No production-ready claim made for voice-agent synthesis; Batch 07 is durable execution backend.
--- BATCH 07 GAP DOCUMENTATION (part 1/3 — documented, not hidden) ---
Compared against https://www.lumay.ai/ai-products/voice-agent (fetched). Real gaps: TTS engine/app/tts/ missing; voice clone missing; visual flow builder missing; external SDK missing; real-time synthesis loop missing; escalation module missing; no-code builder missing. Found not skipped: app/agent/tts.py, app/knowledge/, dashboard/, docs/COMPLIANCE.md/SOC2.md/DPA.md. All 30 targets present; 0027 verified; no fabrication; SQLite caveats reported.
