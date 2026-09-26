# Environment resource scoping closeout

Restarted from the existing tree. The 30 named files were not recreated.
`tenant_id` stays mandatory. `environment_id` is an extra boundary on the eight
business resources only.

## Created

- `app/resources/`: `__init__.py`, `models.py`, `repository.py`, `service.py`, `access.py`, `lifecycle.py`, `serialization.py`, `registry.py`, `exceptions.py`
- `app/environments/`: `resource_types.py`, `resource_binding.py`, `resource_scope.py`, `resource_queries.py`, `resource_policy.py`, `resource_migration.py`, `resource_context.py`
- `app/api/environment_resource_routes.py`
- `app/api/environment_resource_export_routes.py`
- `tests/environments/`: the ten named modules
- `alembic/versions/0019_environment_scope_business_resources.py`
- `docs/ENVIRONMENT-RESOURCE-SCOPING.md`
- `docs/ENVIRONMENT-MIGRATION-RUNBOOK.md`

## Extended, not duplicated

- `app/db/models.py` — columns and composite foreign keys on the existing Call, Lead, Appointment, KnowledgeDocument, KnowledgeChunk, Automation, AutomationRun, NotificationRow, InboxThreadState, and UsageEvent models. No second ORM class for those resources. `uq_environments_tenant_identity` is the composite foreign-key target. Lead now has the same `(tenant_id, environment_id)` constraint as the other scoped tables.
- `app/main.py` — includes the two resource routers.
- `app/knowledge/vectorstore.py`, `retrieval.py`, `ingest.py`, `app/api/knowledge_routes.py` — retrieval and upload stay tenant-scoped and, when an environment is known, environment-scoped.
- `app/billing/metering.py` — optional environment attribution. Quantity, idempotency key, and the duplicate-row path are unchanged.
- `app/services/automation_service.py`, `app/domain/automation_models.py`, `app/services/enterprise_store.py` — a worker denies a payload whose environment claim differs from the automation's stored environment, then stamps the server tenant, organization, and environment onto the action payload.
- `tests/test_deployment.py`, `tests/test_enterprise_persistence.py` — head pin is `0019`.
- `docs/DEPLOYMENT.md`, `docs/IDENTITY-SECURITY.md`, `docs/ENTERPRISE-IDENTITY.md` — head and audit-label wording.

## Per-resource scope

| Resource | Before | After | Legacy create |
| --- | --- | --- | --- |
| Call | tenant only | tenant + environment | omitted id binds to the tenant's active production |
| Lead | tenant only | tenant + environment | same, including a tenant-only import |
| Appointment | tenant only | tenant + environment | same |
| KnowledgeDocument and chunks | tenant only | tenant + environment | omitted id uses production; retrieval returns nothing if that lookup is missing |
| Automation and runs | tenant only | tenant + environment | definition loaded from the row carries `environment_id` |
| Notification | tenant only | tenant + environment | in-memory retry does not accept an environment id |
| Inbox | tenant only | tenant + environment | copied from the same-tenant call when present |
| UsageEvent | tenant only | tenant + environment | attribution only; billing quantity is unchanged |

Not given `environment_id`: Subscription, billing plan, billing customer, payments, SSOConnection, IdentityPolicy, APIKey, ServiceAccount, Organization, Tenant, Domain, CRM credentials, calendar credentials.

## Resolution and policy

1. Explicit environment, only if it belongs to the same tenant.
2. The caller's selected active environment.
3. The tenant's active default production environment.
4. Otherwise reject.

No implicit environment is created. Another tenant's environment is not used. An archived or suspended environment accepts no new write and is not hard-deleted. `environment_id` is immutable after insert. Environment-scoped queries include both `tenant_id` and `environment_id`.

## Schema and backfill

Revision `0019_environment_scope_business_resources` revises `0018_organization_memberships_quotas`.

Tables: `calls`, `leads`, `appointments`, `knowledge_documents`, `knowledge_chunks`, `automations`, `automation_runs`, `notifications`, `inbox_thread_states`, `usage_events`.

Each gets nullable `environment_id`, a backfill to that tenant's `kind='production'` row, `ix_<table>_environment_id`, `ix_<table>_tenant_environment`, and `fk_<table>_environment`. PostgreSQL also gets `fk_<table>_tenant_environment` and `uq_environments_tenant_identity`. The column becomes `NOT NULL` only when that table's unbound count is zero. A tenant with no production row is left unbound. The migration does not invent an environment.

Backfill counts are computed at upgrade time by `backfill_environment_scope`. They are not hardcoded, because they depend on the live database. A second run updates nothing. Downgrade drops the new keys, indexes, and columns. It does not delete business rows. The composite foreign key is dropped only on PostgreSQL, where it was created.

## API authorization

Routes require a human session and the existing permission check. The path supplies tenant and environment. A body cannot choose a different pair.

- `GET /api/tenants/{tenant_id}/environments/{environment_id}/resources`
- `GET .../resources/{resource_type}`
- `GET .../resources/{resource_type}/{resource_id}`
- `POST .../resources/leads`
- `POST .../resources/{resource_type}/{resource_id}/archive`
- `GET .../exports` and `GET .../exports/{resource_type}`

A missing row and a cross-tenant id return the same closed error. Viewer write is denied. Export pages are capped at 100. Create and export emit `RESOURCE_BOUND` or `RESOURCE_EXPORTED`.

## Commands

Passed in this continuation:

- `python3 -c "import app.main"`
- `python3 -m pytest tests/environments tests/test_billing_metering.py::TestConcurrentMetering tests/test_knowledge_retrieval.py` — 62 passed
- `python3 -m pytest tests/environments/test_environment_resource_api.py tests/environments/test_legacy_tenant_compatibility.py` — 6 passed
- `python3 -m ruff check` on the files touched in this restart — passed
- `python3 -m alembic heads` — `0019_environment_scope_business_resources`

Not passed, and not claimed:

- `alembic upgrade` against PostgreSQL. `asyncpg` can be installed, but no PostgreSQL server was available in this sandbox.
- The full `pytest tests` suite. Voice extras such as `pipecat` and `deepgram` are not required for these checks and were not installed.

## Compatibility and indexes

Existing tenant-only creates still work. The insert listener binds production when `environment_id` is omitted or explicitly null. JWT claims are unchanged. Billing idempotency is unchanged. New indexes are the two per scoped table listed above, plus the environments unique constraint used by the composite foreign keys.

## Remaining gaps

- PostgreSQL upgrade has not been executed here, so live backfill counts are unknown until `alembic upgrade head` runs on a backup.
- Notification retry is tenant-keyed in memory and does not take a client environment id. The durable row is bound at insert and cannot be rebound.
- A production-id cache avoids a SQLite lock during concurrent usage inserts. An ORM status change clears it. A raw SQL status update does not, until the process restarts.
- No environment-scoped credentials, API keys, billing, KMS, regional routing, or dashboard redesign was added.
