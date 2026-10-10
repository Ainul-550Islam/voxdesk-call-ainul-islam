# CRM integration audit and regression record

This document records the CRM integration review and the controls that keep
calls, leads, and appointments safe to deliver to external systems. The
operating contract is in `docs/CRM-INTEGRATIONS.md`; this file is the audit
trail for the design decisions behind that contract.

## Scope and evidence

The reviewed surfaces are:

- `app/integrations/crm/base.py`
- `app/integrations/crm/models.py`
- `app/integrations/crm/crypto.py`
- `app/integrations/crm/errors.py`
- `app/integrations/crm/events.py`
- `app/integrations/crm/hooks.py`
- `app/integrations/crm/mapping.py`
- `app/integrations/crm/retry.py`
- `app/integrations/crm/service.py`
- `app/integrations/crm/registry.py`
- providers `ghl.py`, `hubspot.py`, `jobber.py`, and `webhook.py`
- `app/api/integration_routes.py` and `app/api/crm_webhook_routes.py`
- migration `0006_crm_integrations.py`
- CRM contract, provider, credentials, idempotency, isolation, and wiring tests

The repository commit audited for this record is
`1126370ce324097914be3642331024f37834ee57`.
No CRM provider API was contacted with real credentials. HTTP behavior is
verified with scripted transports and internal reference implementations.

## Finding summary

| ID | Finding | Risk before the fix | Current control | Regression coverage |
|---|---|---|---|---|
| F1 | Lifecycle delivery was coupled to the call webhook | A slow CRM or outage could delay the telephony response | Event recording and delivery are separate transactions and worker paths | CRM wiring and service tests |
| F2 | Duplicate callbacks could create duplicate contacts or notes | A provider retry could multiply external records | Derived idempotency keys and tenant-scoped unique constraints | `tests/test_crm_idempotency.py` |
| F3 | Credentials were at risk of leaking or crossing tenants | A token could appear in logs/API/audit or decrypt in another tenant | AES-GCM envelope with tenant/provider AAD, allowlists, and redaction | `tests/test_crm_credentials.py` |
| F4 | Provider-specific errors bypassed retry policy | A timeout could become a permanent failure, or a 401 could loop forever | One normalized error taxonomy with retry classification | contract tests |
| F5 | Provider capabilities were hand-maintained | A declared operation could fall through to a stub or `AttributeError` | Registry-driven capability contract | `tests/test_crm_contract.py` |
| F6 | Contact identity was global or weakly normalized | One tenant could match another tenant's contact | tenant-salted identity hash and tenant/provider lookup | tenant-isolation and mapping tests |
| F7 | Inbound webhook authentication was incomplete | An attacker could forge a tenant-addressable write path | raw-body signature verification, timestamp tolerance, and receipts | webhook security tests |
| F8 | Provider payload quirks were treated as HTTP success | Jobber or a generic endpoint could reject a mutation inside 200 | Adapter checks envelopes, user errors, and external ids | provider tests |

## F1 — record first, deliver later

A call, lead, or appointment creates a normalized `CrmEvent` in the caller's
transaction. The hook never performs external HTTP and never waits for the
provider. A corresponding `CrmSync` row records delivery state. The scheduler
claims pending work and the CRM service performs decryption, mapping, request,
retry, and persistence.

This split protects telephony latency and preserves the business fact across a
process crash. Failure to deliver is visible as a sync state and does not erase
the event. The hook is intentionally non-raising on optional CRM work so an
external outage cannot turn a successful call into a provider retry storm.

## F2 — idempotency and duplicate delivery

The idempotency key is derived from the event type and business entity rather
than generated for each attempt. It is unique within a tenant. An external
provider may still retry a request after accepting it, so the adapter and
service also preserve the external contact link and treat the provider's
existing-record response as reconciliation, not a new fact.

The database uniqueness constraint is the final protection. A pre-insert query
is only an optimization and is not considered a race-proof guarantee.

## F3 and F6 — credentials, identity, and tenant isolation

Credential values are stored in an AES-256-GCM envelope. The key ring supports
rotation; new writes use the active key while older rows remain readable until
re-encrypted. Associated data binds the ciphertext to the tenant and provider,
so copying an encrypted value into another tenant's row fails authentication.

Secrets are not returned by configuration routes, written to audit details,
or included in normalized error messages. Allowed credential and config field
names are explicit. Adapters receive a `ProviderContext`, not a database
session, so a provider cannot query another tenant by construction.

Contact identity is normalized and tenant-salted before hashing. Two tenants
with the same phone or email therefore cannot collide in the local identity
link table. Every integration, event, sync, contact link, and webhook receipt
lookup includes the verified tenant id.

## F4 and F8 — normalized provider behavior

All adapters use the shared HTTP entry point and error taxonomy:

- 401 and 403 are non-retryable authentication failures;
- 422 is a non-retryable validation failure;
- 429, timeouts, connection errors, and 5xx failures are retryable;
- unsupported capabilities become a clean permanent unsupported result;
- a successful HTTP status without a usable external id is not success.

The GoHighLevel adapter always sends its required version and location
information and falls back from upsert where the endpoint is unavailable.
HubSpot uses email upsert only when email is a suitable unique property and
otherwise searches by phone before creating or updating. Jobber checks both
GraphQL `errors` and mutation `userErrors`, including a null mutation payload.
The generic webhook signs the timestamp and raw body and records a delivery
idempotency header.

These checks are inside the adapters so the service layer does not need a
separate vendor-specific branch for each provider.

## F5 — capability contract

`app/integrations/crm/registry.py` is the single provider registry. Contract
tests enumerate the registered provider enum, assert inheritance from
`CrmProvider`, confirm every declared capability overrides the base method, and
confirm every undeclared operation raises the normalized unsupported error.

Adding a provider requires the enum, migration parity, registry entry, and
configuration allowlists. The parameterized contract suite then covers its
scoping, error normalization, timeout, and secret behavior without relying on
a second manually edited provider list.

## F7 — inbound webhooks and replay protection

Inbound routes identify the integration through a routing token, then verify
the provider-specific signature over the raw request body. A body-supplied
`tenant_id` is not trusted for routing. Rejected signatures return a uniform
failure response and do not reveal which integration exists.

A durable receipt keyed by tenant, provider, and provider event id makes a
repeated delivery harmless. Provider webhooks are not allowed to mutate
arbitrary VoxDesk records: the currently supported behavior is deliberately
limited and records a verified receipt before any permitted action.

The generic webhook provider's signature is locally verified. Other vendor
webhook schemes are fail-closed until their public-key or secret provisioning
is implemented and tested against the real provider.

## Live-verification boundary

The following have not been live-tested with real credentials:

- GoHighLevel location token exchange and contact upsert availability;
- HubSpot portal-specific unique-property behavior;
- Jobber GraphQL schema, scopes, throttling, and mutation behavior;
- delivery to a real Zapier, Make, n8n, or bespoke webhook receiver;
- inbound webhook delivery from any provider;
- PostgreSQL migrations and provider-side rate limits.

What the local suite does verify is request construction, payload mapping,
secret containment, error classification, retries, tenant isolation,
idempotency including timeout-after-acceptance scenarios, capability parity,
and the configuration API. Local verification is not a substitute for a
staging tenant and a controlled live smoke test.

## Verification checklist

Before a CRM change is merged, verify that:

- the call/webhook path only records an event and does not wait on a provider;
- every retry uses the same business-derived idempotency key;
- unique constraints are present in the migration;
- all credential fields are encrypted and redacted;
- every lookup includes the verified tenant id;
- adapters have no database handle;
- capability declarations and enum migration values are in parity;
- 401/422 do not retry while timeout/429/5xx do;
- inbound signatures use the raw body and replay receipts are durable;
- no provider is marked live-verified without a controlled external test.
