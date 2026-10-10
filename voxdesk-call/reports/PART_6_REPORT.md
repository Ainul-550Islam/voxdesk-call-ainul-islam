# PART 6 REPORT — Testing, QA, Analytics and Messaging (Gate G7 & Part of G8)

## 1. Executive Summary

PART 6 closes the quality, analytics, and messaging loop across the backend and Next.js dashboard:

- **Closes Facade Rows**:
  - `F-12` (`app/api/multichannel_routes.py`, `app/messaging/sms.py`): Wired `/api/channels/*` on top of `app/messaging/sms.py` with durable `MessageChannel` (`W-11`), `MessageWebhookReceipt`, `ChatConversation`, `ChatMessage`, and `DncEntry` (`1B`) persistence, while preserving fail-closed `501 NOT_CONFIGURED` behavior when provider credentials are omitted.
  - `F-15` (`app/api/call_simulation_routes.py`, `app/api/simulation_routes.py`): Unified simulation lifecycle & results into `/api/v1/testing` (`app/api/call_simulation_routes.py`) and retained `/api/simulations` (`app/api/simulation_routes.py`) as a deprecated alias delegating to the unified contract. Removed default `allow_mock_fallback=True` so simulated runs require real AI credentials unless an explicit `demo=True` flag is supplied, and excluded `is_mock_provider=True` runs from KPIs (`compute_simulation_kpis`).
- **Closes Unwired-Table Row**:
  - `W-11` (`MessageChannel`): Bound inbound/outbound SMS and WhatsApp routing, health checks, and webhook receipts to `MessageChannel`.
- **Closes Retell Parity Rows**:
  - `#23` **Chat / SMS agents**: `app/messaging/sms.py` + `app/services/chat_agent_service.py::resolve_chat_runtime_config` (builds `RuntimeConfig` from `ChatAgentVersion`, never reading `Tenant` fields, satisfying invariant `2E`), STOP/HELP keyword compliance -> `DncEntry` (`1B`), Twilio signature validation, and delivery-status callbacks.
  - `#27` **Automated QA on all calls**: `app/qa/service.py` extended with `evaluate_post_call_sampling_policy`, `admit_post_call_with_policies`, and `get_call_qa_dashboard_summary` so `POST_CALL` (`1C`) automatically evaluates percentage, negative sentiment, transferred call, and long-call policies, scores rubrics, and exposes QA results to dashboard & webhooks.
  - `#28` **Simulation testing & regression from production calls**: `app/services/simulation_caller.py` (`SimulatedCaller` via `app/ai/gateway.py` with persona, goal, variables, interruption style, deterministic seed, and transcript capture), `app/services/simulation_service.py` (`execute_simulated_caller_run`, judge verdict/rationale, `JobType.EVALUATION` handler, `is_mock_provider` tracking, KPI exclusion of mock runs), and `app/services/regression_from_calls.py` (`create_regression_test_from_call` with PII redaction via `app.compliance.pii.redact_text` and LLM-drafted assertions marked `needs_review=True`).
  - `#30` **Analytics & custom dashboards**: `app/services/analytics_service.py` (success rate, avg/p95 duration, cost per call combining `app/ai/costs.py` + telephony cost, latency p50/p95 from `CallLatencyStat`, transfer/voicemail rate, disconnect reasons, sentiment distribution, and group-by `agent`/`version`/`number`/`day`), `app/services/custom_dashboard_service.py`, and `app/api/analytics_dashboard_routes.py` (saved per-tenant custom dashboards, widget builder, shareable token links, and CSV export).
  - `#32` **AI copilot (Conductor)**: `app/services/conductor_evidence_service.py` (`validate_and_resolve_evidence` and `apply_evidence_backed_proposal_to_draft`) rejects fabricated call IDs, turn IDs, transcript quotes, and test runs, and applies verified proposals to `AgentVersion` drafts.

---

## 2. Scope Tree Verification (24 Target Files + Supporting Wiring)

| File | Action | Status |
|---|---|---|
| `app/api/analytics_dashboard_routes.py` | `[NEW]` CRUD + server-side aggregation queries, tenant-scoped, size-limited, share token, CSV export | Verified |
| `app/api/call_simulation_routes.py` | `[MODIFY]` Real run lifecycle + results: per-turn transcript, verdicts, cost; explicit `demo` flag only; regression-from-call & KPI endpoints | Verified |
| `app/api/multichannel_routes.py` | `[MODIFY]` Rewritten on top of `app/messaging/sms.py` with real `MessageWebhookReceipt` & `MessageChannel` (`W-11`) | Verified |
| `app/api/simulation_routes.py` | `[VERIFY]` Merged into unified contract as deprecated alias delegating to `call_simulation_routes` / `simulation_service` | Verified |
| `app/messaging/__init__.py` | `[NEW]` Messaging package exports | Verified |
| `app/messaging/sms.py` | `[NEW]` Twilio Messaging webhook -> chat agent -> reply; STOP/HELP -> DNC (`1B`); delivery callbacks; signature verification; `501 NOT_CONFIGURED` | Verified |
| `app/qa/service.py` | `[MODIFY]` Auto-review from `POST_CALL` (`1C`): percentage, negative sentiment, transfer, and long-call sampling policies; rubric scoring; dashboard summary | Verified |
| `app/services/analytics_service.py` | `[MODIFY]` Success rate, avg/p95 duration, cost per call (`app/ai/costs.py` + telephony), latency p50/p95 (`CallLatencyStat`), transfer/voicemail rate, disconnect reasons, sentiment, group by `agent`/`version`/`number`/`day` | Verified |
| `app/services/chat_agent_service.py` | `[VERIFY]` Added `resolve_chat_runtime_config` building `RuntimeConfig` from `ChatAgentVersion` (`2E`) | Verified |
| `app/services/conductor_evidence_service.py` | `[VERIFY]` Rejects fabricated call/turn/quote/test evidence (`validate_and_resolve_evidence`) and applies verified proposals to `AgentVersion` drafts (`apply_evidence_backed_proposal_to_draft`) | Verified |
| `app/services/custom_dashboard_service.py` | `[NEW]` Per-tenant saved dashboards (`CallPolicy(policy_type="custom_dashboard")`), widgets, shareable token links, and CSV export | Verified |
| `app/services/regression_from_calls.py` | `[NEW]` Converts a real `Call` into a PII-redacted `TestCase` + `EvaluationRule` assertions marked `needs_review=True` | Verified |
| `app/services/simulation_caller.py` | `[NEW]` `SimulatedCaller` (`app/ai/gateway.py`) with persona, goal, variables, interruption style, deterministic seed, and transcript capture | Verified |
| `app/services/simulation_service.py` | `[MODIFY]` LLM-simulated-caller mode with judge verdict/rationale, `is_mock_provider` tracking, mock exclusion from KPIs (`compute_simulation_kpis`), and `JobType.EVALUATION` handler | Verified |
| `dashboard-next/app/dashboard/analytics/custom/page.tsx` | `[NEW]` Custom analytics widget builder + saved dashboards + share link + CSV export | Verified |
| `dashboard-next/app/dashboard/batch-calls/page.tsx` | `[NEW]` Batch create (CSV upload + variables), live progress, outcomes, pause/resume/cancel | Verified |
| `dashboard-next/app/dashboard/experiments/page.tsx` | `[NEW]` A/B experiments: variants, traffic split, computed metrics with confidence, promote | Verified |
| `dashboard-next/app/dashboard/tests/page.tsx` | `[NEW]` Test suites, scenario editor (scripted or simulated caller), run results, regression test creation | Verified |
| `dashboard-next/app/dashboard/tools/page.tsx` | `[NEW]` Tool registry: HTTP tool editor (schema, URL, headers via secrets), test-invoke, per-agent attachment | Verified |
| `dashboard-next/app/dashboard/webhooks/page.tsx` | `[NEW]` Subscriptions, deliveries (status/latency/response), test send, retry, DLQ redrive, secret rotation | Verified |
| `dashboard-next/app/dashboard/calls/[id]/page.tsx` | `[MODIFY]` Added "Create Regression Test from this Call" action on `/dashboard/calls/[id]` | Verified |
| `tests/messaging/test_sms_flow.py` | `[NEW]` Inbound SMS -> chat agent -> reply; STOP -> DNC; signature failure & `NOT_CONFIGURED` rejected | Verified |
| `tests/services/test_custom_dashboards.py` | `[NEW]` Aggregation correctness, group-by dimensions, tenant scoping, widget limits, share token, CSV export, QA sampling & A/B live split | Verified |
| `tests/services/test_regression_from_calls.py` | `[NEW]` Real call -> test case; PII redaction; `needs_review` flag; Conductor fabricated evidence rejection | Verified |
| `tests/services/test_simulation_caller.py` | `[NEW]` Deterministic seed, goal/criteria judged, mock runs excluded from KPIs | Verified |

---

## 3. Acceptance Command Output

1. **`make verify-truth`**:
   - `strip_generated_tails.py --check`: `[]`
   - `verify_no_filler.py`: `[]`
   - `verify_no_fake_success.py`: `[]`
   - `verify_retired_references.py`: `[]`
   - `verify_no_null_bytes.py`: `[]`
   - `strip_padding_markers.py --check`: `{}`
   - `pytest tests/truth -q`: **61 passed**
2. **`python -c "from app.main import app; print('routes:', len(app.routes))"`**:
   - `routes: 1183`
3. **`pytest -q tests/services/test_simulation_caller.py`**:
   - **3 passed**
4. **`pytest -q tests/services/test_regression_from_calls.py`**:
   - **2 passed**
5. **`pytest -q tests/services/test_custom_dashboards.py`**:
   - **3 passed**
6. **`pytest -q tests/messaging/test_sms_flow.py`**:
   - **3 passed**
7. **`(cd dashboard-next && npx tsc --noEmit && npx vitest run)`**:
   - `tsc --noEmit`: **0 errors**
   - `vitest run`: **7 test files passed (85 tests)**
