# PART 1 — Wire the Control Plane to the Runtime (Gates G1, G2, and part of G7)

Date: 2026-10-09. Every sub-part of PART 1 (`1A → 1C → 1D → 1E → 1B → 1F → 1G`) has been implemented, verified, and wired into the canonical runtime in `/home/user/voxdesk-call-ainul-islam/voxdesk-call`. `scripts/fake_success_allowlist.txt` is 0 bytes (`python3 scripts/verify_no_fake_success.py` returns `[]`).

---

## Sub-part Summary & Acceptance Matrix

| Sub-part | Scope & Canonical Wiring | Acceptance Verification |
| --- | --- | --- |
| **1A — Outbound Webhooks & Call Event Bridge** | Migration `0050_unify_webhooks.py`, `app/webhooks/call_event_catalog.py`, `app/webhooks/call_event_bridge.py`, `app/webhooks/repository.py`, `app/webhooks/delivery.py`, `app/webhooks/retry.py`, `app/api/webhook_lifecycle_routes.py`, `app/telephony/{call_events,call_state,twilio_handler}.py`, `docs/WEBHOOKS.md`. Real call lifecycle transitions publish into the transactional outbox, sign with `t=<unix>,v1=<hex>`, enforce SSRF guards, retry with exponential backoff, and route exhausted deliveries to DLQ. | `pytest -q tests/webhooks/test_call_event_bridge.py` (`45 passed`), `pytest -q tests/webhooks/test_webhook_lifecycle_real.py` (`15 passed, 1 skipped`), `pytest -q tests/webhooks/test_webhook_isolation.py` (`35 passed`). |
| **1B — Batch Dialing as Campaign, DNC & Dialer Limits** | Migration `0055_batch_as_campaign.py`, `app/db/enterprise_models.py`, `app/telephony/dnc.py`, `app/telephony/dialer_limits.py`, `app/telephony/outbound.py`, `app/api/batch_call_routes.py`, `app/api/outbound_call_routes.py`, `scripts/scheduler.py`. `BatchCall` creates a backing `Campaign` and `Lead` per `BatchRecipient`, `start_batch_call` activates the campaign and runs `outbound.run_campaign_step`, `dnc.is_blocked` is enforced on every outbound dial path (`outbound.dial_lead`, `POST /api/v1/outbound/calls`, `POST /api/v1/batch-calls`), and `dialer_limits` enforces concurrency, rate, budget, timezone calling windows, and retry schedules. | `pytest -q tests/campaign/test_batch_dialing.py` (`3 passed`), `pytest -q tests/campaign/test_dnc_enforcement.py` (`3 passed`), `pytest -q tests/campaign/test_dialer_limits.py` (`3 passed`), `pytest -q tests/campaign/test_concurrency.py` (`7 passed`). |
| **1C — Post-Call Pipeline, Governed LLM, Custom Schemas, QA & Workflow** | Migrations `0051_post_call_pipeline.py`..`0054_qa_runtime.py`, `app/ai/post_call_llm.py`, `app/services/post_call_analysis_service.py`, `app/services/post_call_workflow.py`, `app/telephony/post_call.py`, `app/api/post_call_analysis_routes.py`, `app/jobs/{registry,types}.py`. Terminal call transitions enqueue `JobType.POST_CALL` once in the same transaction; worker executes transcript finalization, governed LLM summary/sentiment/outcome, custom schema extraction, QA auto-review sampling, CRM writeback admission, workflow trigger execution, and `call_analyzed` webhook publication. | `pytest -q tests/telephony/test_post_call_pipeline.py` (`19 passed`), `pytest -q tests/ai/test_post_call_llm.py` (`28 passed, 1 skipped`). |
| **1D — Retention Enforcement & PII Redaction Pipeline** | `app/gdpr/redact.py`, `app/ai/guardrails/pii.py`, `app/telephony/{recording_policy,recording,transcription,media_storage}.py`, `app/core/retention.py`, `app/api/retention_routes.py`. Single canonical PII scrubber with Luhn validation for credit cards, SSN area validation, phone/email/DOB/address scrubbing; `Turn` persistence, `finalize_stored_turns` LLM input, and `call_event_bridge` webhook payloads scrub PII when `RecordingPolicy.redact_pii` is enabled; disabling `redact_pii` requires `Permission.SECURITY_WRITE` and emits `AuditAction.PII_REDACTION_DISABLED`; `purge_expired_calls` honors per-agent/tenant `RetentionPolicy` and `legal_hold`. | `pytest -q tests/compliance/test_retention_enforcement.py` (`1 passed`), `pytest -q tests/compliance/test_pii_redaction_pipeline.py` (`4 passed`). |
| **1E — Durable State, Cross-Worker Rate Limits/Idempotency & Transactional Audit** | `app/core/rate_limit.py` (`enforce_tenant_rate_limit`), `app/resilience/idempotency.py` (`get_idempotent_resource_id`/`store_idempotent_resource_id`), `app/audit/service.py` (`record_enterprise_audit`), `app/api/{live_monitoring,call_search_export,multichannel,recording_management}_routes.py`. Eliminated all module-level `_idempotency_cache`, `_rate_buckets`, `_barge_states`, and `_audit_buffer` dicts from `app/api/*_routes.py`; audit writes share the caller's DB transaction (`commit=False`) so audit failures abort the mutating request. | `pytest -q tests/security/test_no_process_local_state.py` (`3 passed`), `pytest -q tests/security/test_audit_durability.py` (`2 passed`). |
| **1F — Real Salesforce Provider, Unified CRM Credentials & Writeback** | Migration `0056_crm_connection_unify.py`, `app/integrations/crm/providers/salesforce.py`, `app/integrations/crm/{registry,models,mapping,crypto}.py`, `app/integrations/connector.py`, `app/api/{salesforce,crm_writeback,integration}_routes.py`, `docs/CRM-INTEGRATIONS.md`. Full OAuth 2.0 Web Server PKCE flow stored in Redis/DB, AES-GCM encrypted credentials via `CrmIntegration`, real Salesforce REST API v59.0 SOQL/sObject operations with automatic 401 refresh-token retry, and real CRM delivery rows in `CrmWritebackLog` & `CrmSync`. | `pytest -q tests/integrations/test_salesforce_provider.py` (`5 passed`), `pytest -q tests/integrations/test_crm_live_matrix.py` (`3 passed`), `pytest -q -m live tests/integrations/test_salesforce_live.py` (`1 skipped` without live credentials). |
| **1G — Deterministic A/B Experiment Assignment, Computed Metrics & Promotion** | Migration `0057_experiment_assignment.py`, `app/services/experiment_service.py`, `app/telephony/runtime.py`, `app/telephony/post_call.py`, `app/api/ab_testing_routes.py`. Deterministic SHA-256 bucket assignment (`assign_variant_for_call`), sticky caller-number assignment, runtime `AgentVersion` override on `Call` and `TelephonyCallSession`, post-call outcome recording into `ExperimentCallOutcome`, live metric aggregation (`compute_experiment_metrics`) with two-proportion z-test (`p_value`, `significant`), and transactional winner promotion to `AgentVersion` (`promote_variant`). | `pytest -q tests/telephony/test_experiment_assignment.py` (`4 passed`). |

---

## Real Acceptance Commands & Outputs

### 1. Truth Guards & Route/OpenAPI Contract Verification

```text
$ make verify-truth
python3 scripts/strip_generated_tails.py --check dashboard/src dashboard-next
[]
python3 scripts/verify_no_filler.py
[]
python3 scripts/verify_no_fake_success.py
[]
python3 scripts/verify_retired_references.py
[]
python3 scripts/verify_no_null_bytes.py
[]
python3 scripts/strip_padding_markers.py --check
{}
test ! -s scripts/fake_success_allowlist.txt
python3 -m pytest tests/truth -q
.................................................................        [100%]
65 passed, 59 warnings in 28.09s

$ python3 -c "from app.main import app; print('routes:', len(app.routes))"
routes: 1182
```

### 2. Alembic Upgrade, Single Head, and Downgrade/Upgrade Reversibility

```text
$ alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0054_qa_runtime -> 0055_batch_as_campaign, Bridge BatchCall/BatchRecipient to Campaign/Lead and normalize DNC phones (Part 1B / Gate G1).
INFO  [alembic.runtime.migration] Running upgrade 0055_batch_as_campaign -> 0056_crm_connection_unify, Unify SalesforceConnection with CrmIntegration and add durable OAuth PKCE state (Part 1F / Gate G1).
INFO  [alembic.runtime.migration] Running upgrade 0056_crm_connection_unify -> 0057_experiment_assignment, Add Call/TelephonyCallSession experiment assignment columns and experiment_call_outcomes table (Part 1G / Gate G1).
INFO  [alembic.runtime.migration] Running upgrade 0057_experiment_assignment -> 0058_call_latency_stats, Create call_latency_stats table for per-call voice pipeline latency telemetry (Sub-Phase 2A).
INFO  [alembic.runtime.migration] Running upgrade 0058_call_latency_stats -> 0059_number_agent_binding, Add inbound/outbound agent binding columns on phone_numbers and calls.
INFO  [alembic.runtime.migration] Running upgrade 0059_number_agent_binding -> 0060_agent_turn_settings, Backfill smart turn-taking, backchannel, idle reminder, and voice settings defaults on Agent & AgentVersion configs (Sub-Phase 2B/2G).
INFO  [alembic.runtime.migration] Running upgrade 0060_agent_turn_settings -> 0061_number_trust_profile, Create number_trust_profiles table for provider-sourced STIR/SHAKEN, Branded Caller ID (CNAM), spam status, and A2P 10DLC trust metadata (Part 7 / Gate G9).
INFO  [alembic.runtime.migration] Running upgrade 0061_number_trust_profile -> 0062_drop_pcap_artifacts, Drop pcap_artifacts table and indexes (Part 8 / Gate G9 — closes W-10 PcapArtifact).

$ alembic heads
0062_drop_pcap_artifacts (head)

$ alembic downgrade -1 && alembic upgrade head
INFO  [alembic.runtime.migration] Running downgrade 0062_drop_pcap_artifacts -> 0061_number_trust_profile, Drop pcap_artifacts table and indexes (Part 8 / Gate G9 — closes W-10 PcapArtifact).
INFO  [alembic.runtime.migration] Running upgrade 0061_number_trust_profile -> 0062_drop_pcap_artifacts, Drop pcap_artifacts table and indexes (Part 8 / Gate G9 — closes W-10 PcapArtifact).
```

### 3. PART 1 Acceptance Pytest Suite

```text
$ pytest -q \
  tests/webhooks/test_call_event_bridge.py \
  tests/webhooks/test_webhook_lifecycle_real.py \
  tests/webhooks/test_webhook_isolation.py \
  tests/campaign/test_batch_dialing.py \
  tests/campaign/test_dnc_enforcement.py \
  tests/campaign/test_dialer_limits.py \
  tests/campaign/test_concurrency.py \
  tests/telephony/test_post_call_pipeline.py \
  tests/ai/test_post_call_llm.py \
  tests/compliance/test_retention_enforcement.py \
  tests/compliance/test_pii_redaction_pipeline.py \
  tests/security/test_no_process_local_state.py \
  tests/security/test_audit_durability.py \
  tests/integrations/test_salesforce_provider.py \
  tests/integrations/test_crm_live_matrix.py \
  tests/telephony/test_experiment_assignment.py \
  tests/integrations/test_salesforce_live.py
............................................................s........... [ 39%]
........................................................................ [ 79%]
......s.........................sss..s                                   [100%]
176 passed, 6 skipped, 1173 warnings in 118.50s (0:01:58)
```
