# Environment resource scoping

`tenant_id` remains the authoritative isolation key. `environment_id` is an
additional boundary on eight business resources:

- Call (`calls`)
- Lead (`leads`)
- Appointment (`appointments`)
- KnowledgeDocument (`knowledge_documents`, and the denormalised chunk column)
- Automation (`automations`, and automation runs)
- Notification (`notifications`)
- Inbox (`inbox_thread_states`)
- UsageEvent (`usage_events`)

These stay at their current scope: Subscription, BillingPlan, invoices,
payment receipts, SSOConnection, IdentityPolicy, APIKey, ServiceAccount,
Organization, Tenant, Domain, CRM credentials, and calendar credentials.

## Legacy requests

A tenant-only create that does not name an environment is bound to that
tenant's active default production environment. The binder does not create an
environment and does not look at another tenant. If production is suspended or
archived, the insert is rejected.

An explicit `environment_id` is accepted only after the server has proved it
belongs to the same tenant and is active. A client id that names another
tenant is a 404, the same body as a missing row.

## Immutability

`environment_id` is set at insert and is not changed afterwards. Moving a
resource between environments is not part of this layer.

## Isolation

Every environment-scoped query names both `tenant_id` and `environment_id`.
Knowledge retrieval filters the chunk and the document. A production retrieval
does not return staging chunks. An automation whose payload names a different
target environment is cancelled before any action runs. Usage events record
the environment for attribution and do not change the tenant billing total.

## Policy

Suspended and archived environments reject new writes. Suspended environments
remain readable. Archived environments are readable by an owner or admin.
Production destructive actions require the owner role. Ordinary production
writes still follow the existing role permissions, so a manager can keep
creating leads. A child environment cannot weaken a mandatory parent identity
control.

## API

`GET /api/tenants/{tenant_id}/environments/{environment_id}/resources`
lists the supported types. The same prefix lists, reads, creates a lead, and
archives a lead or knowledge document. Exports live under `.../exports` and
are paginated, audited, and capped at 100 rows per page.
