# Prompt 3 backend surfaces

## Secret storage

`app/security/secret_store.py` is the provider-neutral boundary. Production
uses the existing AES-256-GCM key-ring implementation from
`app/auth/identity/secrets.py`; database records contain only opaque
`secret://` references, and the process refuses to create a reference when
the deployment key ring is absent. This is application-encrypted storage, not
an HSM or remote cloud-vault integration: an attacker who controls the API
process can read secrets by design. Deployments that require KMS/HSM custody
must replace `EncryptedSecretStore` behind the same interface. The in-memory
backend is test-only and is never selected by production configuration.

## Provider truth

The canonical connector registry wraps the existing CRM adapters
(GoHighLevel, HubSpot, Jobber and signed webhook) and the existing calendar
provider namespace. CRM dispatch and health use their real adapter contracts;
calendar operations continue to use the existing calendar service contract and
are not falsely advertised as connector operations. OAuth authorization-code
routes implement signed, expiring, one-time state with encrypted PKCE verifier,
refresh-token storage and revocation when a provider exposes a revoke URL.
Email adapters implemented here are SMTP, Resend, SendGrid and Mailgun; a
provider must be configured with an opaque secret reference before a send is
attempted.

The MCP client implements streamable HTTP JSON-RPC initialization, discovery,
normalized tool schemas, ping/health, bounded requests and tenant/RBAC routes.
API tools are persistent, tenant-scoped, HTTPS-only, JSON-Schema validated,
retry-bounded, audited and idempotent. URL ingestion validates DNS-resolved
redirect targets and reuses the existing extraction/chunking/embedding path;
source rows retain crawl status for recovery. Public webhooks verify a signed
freshness window and durable endpoint/event idempotency.

## Database and observability

Migration `0029_prompt3_surfaces` adds the durable Prompt 3 records and
PostgreSQL RLS policies. RLS reads the transaction-local `app.tenant_id`; an
unset value matches no tenant. `app/auth/dependencies.py` installs that value
only after authentication. Anonymous webhook delivery uses a separate narrow
endpoint-id lookup policy, then installs the endpoint tenant before reading or
writing receipts. Outbound connector, OAuth, MCP, API-tool and email calls use
`app/core/tracing.py`; OpenTelemetry spans become active when the optional
OpenTelemetry package is installed. The current repository environment does
not provide that optional package, so local verification records the
unavailable exporter/toolchain rather than claiming exported OTel traces.

## SDK and operations

`sdk/` is first-party only. `VoxDeskClient` maps connector listing, API-tool
creation/execution, MCP discovery, knowledge search/URL ingestion, OAuth start,
and existing call list/detail/transcript endpoints. Dataclass response models
are exported from `sdk.__init__`; no third-party API hostname is used.

`docker-compose.prod.yml`, `.github/workflows/backend-production.yml`, and
`infra/helm/voxdesk` describe the existing API, scheduler, PostgreSQL, Redis,
and observability dependencies without embedding credentials. Docker, Helm,
PostgreSQL execution, external provider exchanges, and real OTel exporter
behavior still require environment-specific verification.
