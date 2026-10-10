# VoxDesk — Static API Reference (`v0.4.0`)

Generated deterministically by `scripts/build_api_docs.sh` from `app.main:app.openapi()`.

## Summary Metrics

- **OpenAPI Version**: `3.1.0`
- **Total Unique Paths**: `969`
- **Total HTTP Operations**: `1166`
- **Total Tag Groups**: `122`
- **Machine-Readable Schema**: [`openapi.json`](openapi.json) (mirrored at [`../../contracts/openapi.json`](../../contracts/openapi.json))
- **Static HTML Reference (Redoc / Scalar + Offline Explorer)**: [`index.html`](index.html)

### Operations by HTTP Method

| HTTP Method | Operation Count |
|---|---:|
| `DELETE` | 54 |
| `GET` | 497 |
| `PATCH` | 55 |
| `POST` | 546 |
| `PUT` | 14 |

## Tag Inventory

| Tag | Operations | Sample Endpoints |
|---|---:|---|
| `Telephony Runtime` | 18 | `GET /api/v1/telephony/calls`, `POST /api/v1/telephony/calls/outbound`, `GET /api/v1/telephony/calls/{call_id}` (+15 more) |
| `Telephony Webhooks` | 4 | `POST /api/v1/telephony/webhooks/{provider}/dtmf`, `POST /api/v1/telephony/webhooks/{provider}/inbound`, `POST /api/v1/telephony/webhooks/{provider}/recording` (+1 more) |
| `ab-testing` | 56 | `GET /api/ab-testing/experiments`, `GET /api/ab-testing/experiments`, `POST /api/ab-testing/experiments` (+53 more) |
| `agent-builder` | 11 | `GET /api/v1/agents`, `POST /api/v1/agents`, `GET /api/v1/agents/{agent_id}` (+8 more) |
| `agent-catalog` | 3 | `GET /api/agents/models`, `GET /api/agents/tools/catalog`, `GET /api/agents/voices` |
| `agent-flow` | 6 | `GET /api/agents/flow/schema`, `GET /api/agents/{agent_id}/flow`, `PUT /api/agents/{agent_id}/flow` (+3 more) |
| `agent-lifecycle` | 7 | `POST /api/v1/agents/import`, `GET /api/v1/agents/lifecycle/health`, `DELETE /api/v1/agents/{agent_id}` (+4 more) |
| `agent-state` | 5 | `POST /api/agent-state/accept`, `POST /api/agent-state/complete`, `POST /api/agent-state/heartbeat` (+2 more) |
| `agent-test` | 4 | `GET /api/v1/agent-tests/{session_id}`, `POST /api/v1/agent-tests/{session_id}/events`, `GET /api/v1/agent-tests/{session_id}/health` (+1 more) |
| `agent-versions` | 3 | `GET /api/agents/{agent_id}/environments`, `POST /api/agents/{agent_id}/promote`, `GET /api/agents/{agent_id}/versions/diff` |
| `agents` | 10 | `GET /api/agents`, `POST /api/agents`, `GET /api/agents/{agent_id}` (+7 more) |
| `ai-evals` | 5 | `GET /api/tenants/{tenant_id}/ai/evals/datasets`, `POST /api/tenants/{tenant_id}/ai/evals/datasets`, `POST /api/tenants/{tenant_id}/ai/evals/runs` (+2 more) |
| `ai-governance` | 12 | `GET /api/ai/costs`, `GET /api/ai/health`, `GET /api/ai/models` (+9 more) |
| `ai-prompts` | 7 | `GET /api/tenants/{tenant_id}/ai/prompts`, `POST /api/tenants/{tenant_id}/ai/prompts`, `POST /api/tenants/{tenant_id}/ai/prompts/{prompt_key}/publish` (+4 more) |
| `analytics` | 4 | `GET /api/analytics/calls`, `GET /api/analytics/conversion`, `GET /api/analytics/overview` (+1 more) |
| `analytics-dashboards` | 10 | `GET /api/v1/analytics/dashboards`, `POST /api/v1/analytics/dashboards`, `GET /api/v1/analytics/dashboards/shared/{share_token}` (+7 more) |
| `anomaly-agent` | 2 | `POST /api/anomaly/analyze`, `GET /api/anomaly/{execution_id}` |
| `api` | 22 | `GET /api/calls/{call_id}`, `GET /api/calls/{call_id}/transcript`, `GET /api/calls/{call_id}/transfer` (+19 more) |
| `api-tools` | 3 | `GET /api/api-tools`, `POST /api/api-tools`, `POST /api/api-tools/{tool_id}/execute` |
| `appointments` | 9 | `GET /api/appointments`, `POST /api/appointments`, `GET /api/appointments/` (+6 more) |
| `audit` | 1 | `GET /api/v1/audit/events` |
| `audit-compatibility` | 2 | `GET /api/audit-trail/events`, `GET /api/security/audit/events` |
| `auth` | 9 | `POST /auth/login`, `POST /auth/logout`, `POST /auth/logout-all` (+6 more) |
| `automations` | 13 | `GET /api/automations`, `POST /api/automations`, `GET /api/automations/actions` (+10 more) |
| `batch-calls` | 18 | `GET /api/batch-calls`, `POST /api/batch-calls`, `GET /api/batch-calls/stats/overview` (+15 more) |
| `billing` | 11 | `GET /api/billing`, `GET /api/billing/`, `POST /api/billing/cancel` (+8 more) |
| `billing-license` | 2 | `POST /api/billing/license/issue`, `POST /api/billing/license/verify` |
| `calendar` | 8 | `GET /api/calendar/integrations`, `PUT /api/calendar/integrations/{provider}`, `DELETE /api/calendar/integrations/{provider}` (+5 more) |
| `call-simulation` | 5 | `GET /api/simulations`, `POST /api/simulations`, `GET /api/simulations/{sim_id}` (+2 more) |
| `calls-control` | 20 | `GET /api/calls`, `POST /api/calls`, `POST /api/calls/bulk` (+17 more) |
| `calls-search-export` | 26 | `POST /api/calls/consent/check`, `GET /api/calls/dnc`, `POST /api/calls/dnc` (+23 more) |
| `campaigns` | 14 | `GET /api/campaigns`, `POST /api/campaigns`, `GET /api/campaigns/{campaign_id}` (+11 more) |
| `channels` | 2 | `POST /channels/message`, `POST /channels/status` |
| `compliance` | 17 | `POST /api/compliance/checks`, `GET /api/compliance/findings`, `GET /api/compliance/findings/{finding_id}` (+14 more) |
| `compliance-templates` | 3 | `GET /api/compliance/industry-templates`, `POST /api/compliance/industry-templates/frameworks/{framework_id}/activate`, `POST /api/compliance/industry-templates/{template_key}/frameworks` |
| `conductor` | 20 | `GET /api/v1/conductor/context`, `GET /api/v1/conductor/proposals`, `POST /api/v1/conductor/proposals` (+17 more) |
| `conductor-reviews` | 4 | `POST /api/v1/conductor/reviews/proposals/{proposal_id}/accept-safe`, `POST /api/v1/conductor/reviews/proposals/{proposal_id}/changes/{change_id}/accept`, `GET /api/v1/conductor/reviews/proposals/{proposal_id}/grouped-changes` (+1 more) |
| `conductor-webhooks` | 1 | `POST /api/v1/conductor/webhooks/events` |
| `connectors` | 9 | `GET /api/connectors`, `POST /api/connectors`, `GET /api/connectors/providers` (+6 more) |
| `conversation-intelligence` | 11 | `GET /api/conversations/{call_id}/coaching`, `GET /api/conversations/{call_id}/compliance`, `GET /api/conversations/{call_id}/evidence` (+8 more) |
| `crm-writeback` | 14 | `POST /api/crm/backfill`, `POST /api/crm/calls/{call_id}/disposition`, `POST /api/crm/calls/{call_id}/fields` (+11 more) |
| `deployment` | 13 | `GET /api/deployment/revisions/{revision_id}`, `POST /api/deployment/revisions/{revision_id}/approve`, `POST /api/deployment/revisions/{revision_id}/deploy` (+10 more) |
| `deployment-runtime` | 6 | `POST /api/deployment/revisions/{revision_id}/apply`, `POST /api/deployment/revisions/{revision_id}/preflight`, `GET /api/deployment/revisions/{revision_id}/status` (+3 more) |
| `enterprise-security` | 1 | `GET /api/v1/enterprise-security/posture` |
| `enterprise-security-compatibility` | 1 | `GET /api/enterprise-security/posture` |
| `environment-access` | 8 | `GET /api/tenants/{tenant_id}/access/current`, `POST /api/tenants/{tenant_id}/access/current`, `GET /api/tenants/{tenant_id}/access/environments` (+5 more) |
| `environment-exports` | 2 | `GET /api/tenants/{tenant_id}/environments/{environment_id}/exports`, `GET /api/tenants/{tenant_id}/environments/{environment_id}/exports/{resource_type}` |
| `environment-resources` | 5 | `GET /api/tenants/{tenant_id}/environments/{environment_id}/resources`, `POST /api/tenants/{tenant_id}/environments/{environment_id}/resources/leads`, `GET /api/tenants/{tenant_id}/environments/{environment_id}/resources/{resource_type}` (+2 more) |
| `environments` | 11 | `GET /api/tenants/{tenant_id}/environments`, `POST /api/tenants/{tenant_id}/environments`, `GET /api/tenants/{tenant_id}/environments/{environment_id}` (+8 more) |
| `evaluations` | 7 | `GET /api/v1/evaluations/rules`, `POST /api/v1/evaluations/rules`, `GET /api/v1/evaluations/rules/{rule_id}` (+4 more) |
| `evidence` | 7 | `GET /api/governance/evidence`, `POST /api/governance/evidence/package`, `POST /api/governance/evidence/verify` (+4 more) |
| `forecasting` | 2 | `POST /api/forecast`, `GET /api/forecast/{execution_id}` |
| `gdpr` | 3 | `POST /api/gdpr/erasure`, `GET /api/gdpr/export`, `GET /api/gdpr/status` |
| `governance` | 16 | `POST /api/governance/evaluate`, `GET /api/governance/policies`, `POST /api/governance/policies` (+13 more) |
| `health` | 3 | `GET /health`, `GET /health/dependencies`, `GET /health/ready` |
| `identity` | 78 | `GET /api/api-keys`, `POST /api/api-keys`, `GET /api/api-keys/scopes` (+75 more) |
| `inbox` | 17 | `GET /api/inbox/counts`, `GET /api/inbox/threads`, `POST /api/inbox/threads` (+14 more) |
| `insight` | 2 | `POST /api/insight/analyze`, `GET /api/insight/{execution_id}` |
| `integrations` | 10 | `GET /api/integrations/crm`, `GET /api/integrations/crm/`, `POST /api/integrations/crm/inbound/{provider}/{token}` (+7 more) |
| `jobs` | 9 | `GET /api/jobs`, `GET /api/jobs/dlq`, `GET /api/jobs/metrics` (+6 more) |
| `knowledge` | 9 | `GET /api/knowledge/documents`, `POST /api/knowledge/documents`, `GET /api/knowledge/documents/{document_id}` (+6 more) |
| `knowledge-base-collections` | 12 | `GET /api/kb/collections`, `POST /api/kb/collections`, `GET /api/kb/collections/{collection_id}` (+9 more) |
| `lead-activities` | 4 | `PATCH /api/tenants/{tenant_id}/lead-tasks/{task_id}`, `GET /api/tenants/{tenant_id}/leads/{lead_id}/activities`, `POST /api/tenants/{tenant_id}/leads/{lead_id}/activities` (+1 more) |
| `lead-import` | 2 | `GET /api/tenants/{tenant_id}/lead-exports`, `POST /api/tenants/{tenant_id}/lead-imports` |
| `lead-segments` | 4 | `GET /api/tenants/{tenant_id}/lead-segments`, `POST /api/tenants/{tenant_id}/lead-segments`, `GET /api/tenants/{tenant_id}/lead-segments/{segment_id}` (+1 more) |
| `leads` | 11 | `POST /api/tenants/{tenant_id}/lead-merges`, `GET /api/tenants/{tenant_id}/lead-records`, `POST /api/tenants/{tenant_id}/lead-records` (+8 more) |
| `legal-agent` | 11 | `GET /api/legal/clause-library`, `GET /api/legal/clause-library/{clause_key}`, `GET /api/legal/playbooks` (+8 more) |
| `live-monitoring` | 19 | `GET /api/calls/monitor/config`, `GET /api/calls/monitor/health`, `DELETE /api/calls/monitoring/idempotency/cache` (+16 more) |
| `mcp` | 5 | `GET /api/mcp/servers`, `POST /api/mcp/servers`, `POST /api/mcp/servers/{server_id}/discover` (+2 more) |
| `messaging-sms` | 4 | `POST /messaging/sms/send`, `POST /messaging/sms/status`, `POST /messaging/sms/twilio` (+1 more) |
| `model-registry` | 15 | `GET /api/governance/models`, `POST /api/governance/models`, `GET /api/governance/models/approved` (+12 more) |
| `multichannel` | 10 | `GET /api/channels`, `POST /api/channels`, `POST /api/channels/send` (+7 more) |
| `notifications` | 13 | `GET /api/notifications`, `POST /api/notifications`, `POST /api/notifications/preferences/check` (+10 more) |
| `number-trust` | 4 | `GET /api/phone-numbers/{number_id}/trust-profile`, `POST /api/phone-numbers/{number_id}/trust-profile/refresh`, `GET /api/v1/telephony/phone-numbers/{number_id}/trust-profile` (+1 more) |
| `organization-membership` | 11 | `POST /api/organizations/{organization_id}/invitations`, `POST /api/organizations/{organization_id}/invitations/accept`, `POST /api/organizations/{organization_id}/invitations/{invitation_id}/resend` (+8 more) |
| `organizations` | 9 | `GET /api/organizations`, `POST /api/organizations`, `GET /api/organizations/{organization_id}` (+6 more) |
| `outbox` | 5 | `GET /api/outbox`, `POST /api/outbox/test`, `GET /api/outbox/{event_id}` (+2 more) |
| `parity` | 3 | `GET /api/v1/parity/capabilities`, `GET /api/v1/parity/e2e/inspect`, `GET /api/v1/parity/integrations` |
| `phone-numbers` | 12 | `GET /api/phone-numbers`, `POST /api/phone-numbers/provision`, `POST /api/phone-numbers/search` (+9 more) |
| `phone-numbers-lifecycle` | 5 | `PATCH /api/phone-numbers/{number_id}`, `DELETE /api/phone-numbers/{number_id}`, `POST /api/phone-numbers/{number_id}/configure` (+2 more) |
| `post-call-analysis` | 21 | `GET /api/analysis/backfill`, `POST /api/analysis/backfill`, `POST /api/analysis/backfill/preview` (+18 more) |
| `public-home` | 6 | `GET /api/v1/public/analytics/summary`, `GET /api/v1/public/health`, `GET /api/v1/public/home` (+3 more) |
| `public-site` | 5 | `POST /api/v1/public/contact-sales`, `GET /api/v1/public/site/manifest`, `GET /api/v1/public/site/pricing` (+2 more) |
| `public-use-cases` | 4 | `GET /api/v1/public/use-cases`, `GET /api/v1/public/use-cases/categories`, `GET /api/v1/public/use-cases/health` (+1 more) |
| `public-webhooks` | 1 | `POST /public/webhooks/{endpoint_id}` |
| `public-widget` | 7 | `GET /api/v1/public/widget/config`, `GET /api/v1/public/widget/embed.js`, `POST /api/v1/public/widget/sessions` (+4 more) |
| `public-widget-keys` | 6 | `GET /api/v1/public-keys`, `POST /api/v1/public-keys`, `GET /api/v1/public-keys/{key_id}` (+3 more) |
| `qa` | 25 | `GET /api/qa/agents/{agent_id}/compare-versions`, `GET /api/qa/agents/{agent_id}/summary`, `POST /api/qa/calibration` (+22 more) |
| `queues` | 18 | `GET /api/queues`, `POST /api/queues`, `GET /api/queues/{queue_id}` (+15 more) |
| `recordings` | 7 | `GET /api/recordings`, `POST /api/recordings`, `POST /api/recordings/purge` (+4 more) |
| `retell-parity` | 39 | `GET /api/agent-transfers`, `POST /api/agent-transfers`, `POST /api/agent-transfers/{transfer_id}/transition` (+36 more) |
| `retention` | 9 | `GET /api/retention/policies`, `POST /api/retention/policies`, `GET /api/retention/policies/{policy_id}` (+6 more) |
| `reviews` | 9 | `GET /api/reviews`, `POST /api/reviews`, `GET /api/reviews/{case_id}` (+6 more) |
| `risk` | 6 | `GET /api/governance/risk`, `POST /api/governance/risk/assess`, `POST /api/governance/risk/{assessment_id}/approve` (+3 more) |
| `roi` | 11 | `GET /api/roi/baselines`, `POST /api/roi/baselines`, `POST /api/roi/calculate` (+8 more) |
| `routing` | 8 | `POST /api/routing/assign-next`, `GET /api/routing/decisions/{decision_id}`, `POST /api/routing/preview` (+5 more) |
| `salesforce` | 27 | `GET /api/integrations/salesforce/accounts`, `GET /api/integrations/salesforce/api-limits`, `POST /api/integrations/salesforce/bulk/writeback` (+24 more) |
| `scim` | 15 | `GET /scim/v2/{connection_id}/Groups`, `POST /scim/v2/{connection_id}/Groups`, `GET /scim/v2/{connection_id}/Groups/{group_id}` (+12 more) |
| `security` | 7 | `GET /api/security/approvals`, `POST /api/security/approvals`, `POST /api/security/approvals/{approval_id}/decision` (+4 more) |
| `skills` | 8 | `GET /api/skills`, `POST /api/skills`, `PATCH /api/skills/{skill_id}` (+5 more) |
| `specialized-agents` | 4 | `GET /api/specialized-agents`, `GET /api/specialized-agents/{agent_type}`, `POST /api/specialized-agents/{agent_type}/execute` (+1 more) |
| `supervisor` | 8 | `POST /api/supervisor/agents/{user_id}/state`, `POST /api/supervisor/assignments/{assignment_id}/reassign`, `POST /api/supervisor/assignments/{assignment_id}/release` (+5 more) |
| `team` | 6 | `GET /api/team/audit`, `GET /api/team/users`, `POST /api/team/users` (+3 more) |
| `telephony` | 5 | `POST /telephony/ivr`, `POST /telephony/outbound-answer`, `POST /telephony/status` (+2 more) |
| `telnyx-voice` | 1 | `POST /telephony/telnyx/voice` |
| `tenant-admin` | 8 | `GET /api/organizations/{organization_id}/tenants`, `POST /api/organizations/{organization_id}/tenants`, `GET /api/tenants/{tenant_id}/hierarchy` (+5 more) |
| `tenant-membership` | 10 | `POST /api/tenants/{tenant_id}/invitations`, `POST /api/tenants/{tenant_id}/invitations/accept`, `POST /api/tenants/{tenant_id}/invitations/{invitation_id}/revoke` (+7 more) |
| `tenant-security` | 4 | `POST /api/tenants/{tenant_id}/feature-flags/evaluate`, `POST /api/tenants/{tenant_id}/residency`, `GET /api/tenants/{tenant_id}/security/posture` (+1 more) |
| `tenant-usage` | 2 | `GET /api/tenants/{tenant_id}/usage`, `POST /api/tenants/{tenant_id}/usage/check` |
| `testing-calls` | 3 | `GET /api/v1/testing/calls/readiness`, `GET /api/v1/testing/calls/runs`, `GET /api/v1/testing/calls/runs/{run_id}` |
| `testing-phone-calls` | 2 | `POST /api/v1/testing/phone-calls/run`, `GET /api/v1/testing/phone-calls/{run_id}` |
| `testing-simulation` | 20 | `GET /api/v1/testing/cases`, `POST /api/v1/testing/cases`, `GET /api/v1/testing/cases/{case_id}` (+17 more) |
| `testing-web-calls` | 3 | `POST /api/v1/testing/web-calls/sessions`, `GET /api/v1/testing/web-calls/sessions/{run_id}`, `POST /api/v1/testing/web-calls/sessions/{run_id}/events` |
| `tool-registry` | 8 | `GET /api/agents/{agent_id}/tools`, `POST /api/agents/{agent_id}/tools`, `GET /api/agents/{agent_id}/tools/{tool_id}` (+5 more) |
| `transfer-control` | 26 | `GET /api/calls/transfers/analytics/summary`, `POST /api/calls/transfers/bulk`, `DELETE /api/calls/transfers/cache` (+23 more) |
| `translation-agent` | 2 | `POST /api/translation/jobs`, `GET /api/translation/jobs/{execution_id}` |
| `voice-catalog` | 3 | `GET /api/voices/catalog`, `POST /api/voices/clone`, `GET /api/voices/providers` |
| `web-call-transport` | 1 | `POST /telephony/web/offer` |
| `web-calls-live` | 6 | `POST /api/public/web-calls`, `POST /api/web-calls`, `GET /api/web-calls/{call_id}` (+3 more) |
| `webhooks-lifecycle` | 33 | `GET /api/webhooks`, `POST /api/webhooks`, `POST /api/webhooks/bulk/delete` (+30 more) |
| `workflow-events` | 8 | `GET /api/workflows/calls/{call_id}/context`, `GET /api/workflows/calls/{call_id}/executions`, `POST /api/workflows/calls/{call_id}/outcome-writeback` (+5 more) |
| `workflows` | 16 | `GET /api/workflows`, `POST /api/workflows`, `GET /api/workflows/{workflow_id}` (+13 more) |
