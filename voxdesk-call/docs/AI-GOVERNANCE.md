# AI governance

## What this is

`app.agent` still executes voice and text. `app.ai` is the control plane around that execution: model policy, deadlines, fallback, a process-local circuit breaker, provider-cost estimates, prompt versions, guardrails and evaluation scoring.

The enumerated tree names 27 files, not 30. No filler file was added.

## Implemented controls

* Routing uses `app.agent.llm_factory.resolve` and `PRESETS`. A missing policy row leaves that selection alone. An explicit allowlist refuses anything not listed. A disabled policy fails closed.
* Fallback walks the existing `FALLBACK_ORDER` and skips a provider the policy disabled or left off the allowlist. Authentication and invalid-request errors do not fall back. Voice gets one attempt and a 1500 ms budget. A provider timeout cannot exceed the parent deadline.
* The circuit breaker is per supported provider, with a closed/open/half-open probe. Unknown providers fail closed. Local state decides for this process. When `REDIS_URL` is set, open state is also written through the existing cache so another worker can adopt it. A cache miss is not an outage, and Redis is not required.
* Provider cost comes from `app.billing.cost`. Unknown prices stay unknown and are not reported as zero. `tenant_charge_usd` is always null here. Tenant usage, when recorded, goes through `app.billing.metering.record_usage` on `llm_token`. That is not a second meter.
* A token ceiling, when configured, is reserved with one `UPDATE ... WHERE tokens_reserved + n <= ceiling` on `ai_admission_counters`. No ceiling means unknown, and the call is not blocked by a made-up zero. Billing entitlement is still consulted via `check_metric`.
* Prompt versions are rows. Published bodies are not edited. Rollback points at an existing published version. Percentage rollout is a sha256 bucket of tenant, key and salt, so one tenant does not flip versions between calls. The code prompt `agent.system` is still built by `app.agent.prompts`. Publishing an override does not rewrite that function and does not switch the live voice pipeline.
* Input checks refuse empty, oversized and malformed text. A phrase such as "ignore previous instructions" is a signal, not a guarantee and not an authorization. Tool authority is `app.ai.guardrails.tool_policy` plus the existing permission enum. The model principal is always denied. The voice `dispatch` path still allowlists `DISPATCHABLE_TOOLS` and then asks the same policy; current agent tools stay allowed so calls do not change.
* PII redaction masks email, phone, SSN-shaped and card-shaped strings in telemetry. It is not a complete detector.
* Evaluation uses `app.evaluation.evaluator`. The HTTP run does not call a live provider. Missing outputs are a failed run with `score_invented: false`. Stored results keep case name and pass/fail, not the prompt.
* Audit uses the existing `AuditLog` actions `SECURITY_SETTINGS_CHANGED` and `AUTHZ_DENIED`. Detail carries keys, versions and checksums, not prompt bodies or API keys.

## Permissions reused

No new permission enum was added.

* Read policy, prompts and evals: `tenant:read`
* Create a draft or an evaluation: `tenant:update`
* Change model policy, publish or roll back a production prompt, retire a prompt: `security:settings` (owner, not admin or viewer)

A foreign tenant id is 404.

## Schema

`0021_ai_governance` revises `0020_durable_enterprise_operations`. It adds prompt, policy, evaluation and admission tables. It does not store provider API keys.

## Not claimed

This is not a SOC 2, ISO or HIPAA certification. Prompt-injection detection is not perfect. PII detection is not perfect. Hallucinations are not eliminated. A policy check is not a safety guarantee unless the code path actually refuses the action. The voice pipeline was not rewritten and is not forced through the gateway. Multi-worker circuit-breaker state is not shared unless a later change writes it to the configured cache. `alembic upgrade head` was not run in this environment because PostgreSQL was not available.
