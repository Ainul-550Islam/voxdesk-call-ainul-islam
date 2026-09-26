# Organization, tenant, and environment

## What this batch is

The enumerated target list contains 29 paths, not 30. No filler file was added. Organization, tenant, environment, membership, and quota rows already exist in migrations `0017`, `0018`, and `0019`. That batch left Alembic at `0020_durable_enterprise_operations` and did not add a migration. A later AI governance batch added `0021_ai_governance` for prompt and evaluation rows, not for the organization hierarchy.

`Tenant` remains the workspace boundary. There is no second workspace table and no second ownership key on business rows.

## Hierarchy

Organization → tenant → environment (`development`, `staging`, `production`).

The authenticated principal supplies organization, tenant, actor, and an optional environment. A body or query tenant id that disagrees with the authenticated tenant is a boundary miss (404). It is not applied.

## Lifecycle

Organization and tenant statuses remain `active`, `suspended`, `read_only`, and `deleted`. Environment statuses remain `active`, `suspended`, and `archived`.

`deleted` and `archived` change a status column. They do not delete calls, users, invoices, or the row itself. Production cannot be archived or duplicated. The existing `is_active` login flag is not flipped by these transitions.

## Promotion

`POST /api/tenants/{tenant_id}/environments/{environment_id}/promotion-plan` returns a plan. The only legal steps are development → staging and staging → production. The plan does not copy configuration, does not copy secrets, does not overwrite production, and does not execute a deployment. Recording a release version on the existing deployment endpoint is still a record, not a cluster rollout.

## Settings and flags

`GET /api/tenants/{tenant_id}/settings` returns non-secret metadata. `crm_api_key` and other withheld fields are not included.

Feature flags are a closed catalog evaluated in process. Privileged flags (`cross_environment_debug`, `residency_override`) stay off. Client override fields are rejected. Evaluation does not write a flag row and does not change product switches such as `outbound_enabled`. There is no flag table; inventing one would have required a migration this schema does not need for organization or environment persistence.

## Quotas

Limit checks go through `app.tenancy.quota` to `app.quotas`, which already consults billing. This batch does not add a meter, a price, or a Stripe write. Unknown limits stay unknown. Negative usage is rejected before the resolver. A hard limit is denied when supplied usage is above it.

`compare_and_reserve` is a single `UPDATE ... WHERE column + adding <= limit` against the non-billing integer `max_call_attempts`. It is not called by a route and it does not touch `minutes_used`. That is the race-safe admission primitive. Billing remains the voice meter.

## Regions

No physical region is configured. `placement` is `unconfigured`. `physical_residency_proven` is always false. `knowledge_s3_region` is not treated as residency. A supported label in an explicit catalog is still not stored and still not proof of where bytes sit.
