# CRM Integrations

VoxDesk syncs call outcomes, leads, appointments, and custom field values into
each tenant's CRM without blocking the live telephony pipeline.

## Supported Providers & Capability Matrix

| Provider | Enum value | Credentials | Required config | Capabilities | Live Verification Suite |
|---|---|---|---|---|---|
| GoHighLevel (v2 API) | `gohighlevel` | `access_token` (Private Integration Token) | `location_id` (required), `calendar_id` (optional), `base_url` (optional) | `upsert_contact`, `create_contact`, `update_contact`, `get_contact`, `create_note`, `create_activity`, `create_appointment`, `add_tags`, `add_custom_fields`, `health_check` | `tests/integrations/test_crm_live_matrix.py` (`GHL_SANDBOX_*`) |
| HubSpot (v3 Private App) | `hubspot` | `access_token` (`pat-...`) | `base_url` (optional) | `upsert_contact`, `create_contact`, `update_contact`, `get_contact`, `create_note`, `create_activity`, `add_custom_fields`, `health_check` | `tests/integrations/test_crm_live_matrix.py` (`HUBSPOT_SANDBOX_*`) |
| Jobber (GraphQL API) | `jobber` | `access_token`, `refresh_token` (optional) | `api_version` (optional, default `2023-11-15`), `base_url` (optional) | `upsert_contact`, `create_contact`, `update_contact`, `get_contact`, `create_note`, `create_activity`, `add_custom_fields`, `health_check` | `tests/integrations/test_crm_live_matrix.py` (`JOBBER_SANDBOX_*`) |
| Salesforce (REST API v59.0 + OAuth2 PKCE) | `salesforce` | `access_token`, `refresh_token` (optional), `client_id` (optional), `client_secret` (optional) | `instance_url` (required, HTTPS `*.salesforce.com` / `*.my.salesforce.com`), `api_version` (optional, default `v59.0`), `login_url` (optional) | `upsert_contact`, `create_contact`, `update_contact`, `get_contact`, `create_note`, `create_activity`, `add_custom_fields`, `health_check` | `tests/integrations/test_salesforce_live.py` (`SALESFORCE_SANDBOX_*`) |
| Generic Webhook | `webhook` | `signing_secret` (optional, HMAC-SHA256) | `url` (required) | `upsert_contact`, `create_contact`, `update_contact`, `create_note`, `create_activity`, `create_appointment`, `add_tags`, `add_custom_fields`, `health_check` | `tests/integrations/test_crm_live_matrix.py` |

Unsupported capability calls fail cleanly with `CrmCapabilityUnsupported` — no
silent no-ops and no fabricated external IDs.

## Salesforce Provider (`app/integrations/crm/providers/salesforce.py`)

- **OAuth2 Web-Server Flow + PKCE**: `POST /api/integrations/salesforce/oauth/authorize` generates an RFC 7636 S256 `code_verifier`/`code_challenge` pair and persists `SalesforceOAuthState` in PostgreSQL. `POST /api/integrations/salesforce/oauth/callback` exchanges the code + PKCE verifier at `{login_url}/services/oauth2/token` and seals the tokens with AES-256-GCM.
- **Automatic Token Refresh**: When a REST request returns HTTP 401 (`CrmAuthError`) and `refresh_token` + `client_id` + `client_secret` are available, `SalesforceProvider` automatically refreshes the access token and retries once.
- **Instance URL Validation**: `validate_salesforce_instance_url()` enforces HTTPS, blocks loopback/RFC1918/link-local addresses via `app.core.ssrf.validate_outbound_url`, and restricts hosts to `*.salesforce.com`, `*.my.salesforce.com`, and `*.force.com`.
- **Escaped SOQL**: All contact/lead/entity lookups escape `\`, `'`, `"`, `\n`, `\r`, `\t`, `\b`, and `\f` via `escape_soql_literal()` before building SOQL queries against `/services/data/v59.0/query`.
- **Call Logging as Task**: `create_activity()` creates a completed Salesforce `Task` sObject with `Subject`, `Description`, `CallDurationInSeconds`, `CallType` (`Inbound`/`Outbound`), `TaskSubtype="Call"`, and `WhoId`/`WhatId`.
- **Rate Limits**: HTTP 429 and Salesforce `REQUEST_LIMIT_EXCEEDED` error payloads are classified as `CrmRateLimited` (`retryable=True`).

## Security Model

### Credential Encryption at Rest

All provider credentials are encrypted before touching the database using
**AES-256-GCM** (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) with a
random 96-bit nonce per encryption operation:

- Associated authenticated data (AAD) binds every ciphertext to
  `tenant_id|provider|key_id`. A row copied across tenants or providers fails
  GCM tag verification on decrypt (`CrmDecorationError`).
- Envelope format: `v1:<key_id>:<urlsafe_b64(nonce || ciphertext || tag)>`.
- Key ring is configured through `CRM_ENCRYPTION_KEYS="k2:<b64>,k1:<b64>"` (32
  decoded bytes per key). The first entry is the active key for new writes;
  every listed key remains valid for decryption so operators can rotate keys
  without downtime.
- Plaintext credentials **never** appear in API responses (`CrmIntegrationResponse`
  exposes `has_credentials: bool` and `credentials_updated_at` only), exception
  messages (`safe_message()` scrubs every known secret token), or structured logs.

### Tenant Isolation

Every query against `crm_integrations`, `salesforce_connections`, `crm_writeback_logs`, and `crm_sync_logs` filters by `tenant_id` derived from the authenticated principal (`TenantContext`). Cross-tenant lookups return `404`.

## Reliability & Async Execution

### Call Flows Never Block on CRM

`app/telephony/call_lifecycle.py` and `app/api/lead_routes.py` schedule CRM work
with `asyncio.create_task(dispatch_crm_event_background(...))`. The background
task opens its own `AsyncSessionLocal()` session, isolates exceptions, and logs
failures without propagating them to Twilio or the HTTP caller.

### Retry & Exponential Backoff

`CrmSyncService._call_with_retry` retries only errors flagged `retryable=True`
(`CrmRateLimited` on HTTP 429 / `REQUEST_LIMIT_EXCEEDED`, `CrmUnavailable` on 5xx and network timeouts):

```
delay(attempt) = min(base * 2^(attempt-1), max_backoff) + uniform(0, base/2)
```

Auth (`401`/`403` after refresh attempt), validation (`400`/`422`), not-found (`404`), and unsupported-capability errors are **not** retried.

### Sync & Writeback Logging

Every attempt writes a `CrmSyncLog` / `CrmWritebackLog` row containing:
`tenant_id`, `integration_id`, `provider`, `event_type`, `operation`, `status` (`success` | `failed` | `skipped`), `call_id`, `lead_id`, `external_id`, `http_status`, `attempt_count`, `duration_ms`, and a secret-scrubbed `error_message`.

## Custom Field Mapping

Tenants configure `field_mappings` as `{ "<crm_field>": "<voxdesk_source>" }` via `PUT /api/integrations/crm/{provider}` or `POST /api/crm/mappings`. Allowed VoxDesk sources are validated at write time by `app/integrations/crm/mapping.py` (and optionally cross-checked against provider `describe()` metadata via `validate_field_mappings_against_describe()`):

`caller_number`, `callee_number`, `call_direction`, `call_status`, `call_duration_seconds`, `call_summary`, `call_sentiment`, `call_intent`, `call_id`, `lead_name`, `lead_phone`, `lead_email`, `lead_status`, `lead_source`, `lead_notes`, `appointment_scheduled_at`.

## API Endpoints

All endpoints require authentication (`Authorization: Bearer <jwt>` or `X-API-Key`).

| Method & Path | Permission | Description |
|---|---|---|
| `GET /api/integrations/crm` | `integration:read` | List tenant CRM integrations + recent 24h sync counts (no secrets) |
| `GET /api/integrations/crm/{provider}` | `integration:read` | Read a single provider's non-secret config and mappings |
| `PUT /api/integrations/crm/{provider}` | `integration:write` | Upsert credentials (re-encrypted), config, field mappings, and event subscriptions |
| `DELETE /api/integrations/crm/{provider}` | `integration:write` | Delete a provider integration and its sync logs |
| `POST /api/integrations/crm/{provider}/test` | `integration:write` | Live `health_check()` against the provider; returns `{connected, provider, latency_ms, message}` |
| `POST /api/integrations/crm/{provider}/sync` | `integration:sync` | Manually dispatch a `call.completed` or `lead.created` sync |
| `GET /api/integrations/crm/{provider}/logs` | `integration:read` | Recent `CrmSyncLog` entries (`status` filter, `limit`) |
| `POST /api/integrations/salesforce/connect` | `integration:write` | Connect Salesforce with AES-GCM encrypted tokens |
| `POST /api/integrations/salesforce/oauth/authorize` | `integration:write` | Start Salesforce OAuth2 + S256 PKCE flow |
| `POST /api/integrations/salesforce/oauth/callback` | `integration:write` | Complete OAuth2 + PKCE exchange and persist encrypted credentials |
| `POST /api/crm/calls/{call_id}/disposition` | `integration:sync` | Write call disposition to configured CRM provider |
| `POST /api/crm/calls/{call_id}/task` | `integration:sync` | Write follow-up task or note to configured CRM provider |
| `POST /api/crm/calls/{call_id}/fields` | `integration:sync` | Sync extracted conversation fields to configured CRM provider |
