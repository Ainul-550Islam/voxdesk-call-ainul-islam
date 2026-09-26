# Tenant isolation

## Boundary

A caller can see a tenant only when that tenant is the authenticated tenant. Organization routes additionally require the caller's `organization_id`. An environment is visible only when `environment.tenant_id` is that same tenant.

Cross-organization, cross-tenant, and cross-environment lookups return 404 with `{"code": "not_found", "message": "Not found"}`. The body does not say which check failed.

A client-supplied tenant id, including `claimed_tenant_id` and a body `tenant_id`, cannot replace the authenticated tenant. A mismatch is the same 404.

## What is not isolated by a new table

Business rows already carry `tenant_id`, and environment-scoped rows already carry a composite key from migration `0019`. This batch does not add a parallel tenant id or copy those tables per environment.

## Membership and permissions

Membership remains the existing user, role, and membership rows. A tenant admin permission is not granted to every organization member. `tenant:update` is still required to suspend a tenant, change residency labels, or evaluate a flag write. A viewer or agent who lacks that permission gets 403 only after the boundary check has passed. A foreign tenant id never becomes a 403.

Machine credentials remain limited to the tenant and scopes the credential already has. This batch does not add environment API keys or environment service accounts.

## Secrets

Settings responses omit secret and contact fields. Promotion plans list withheld secret fields and set `copies_secrets` to false. Audit details for a refused region change store the label, the reason, `applied: false`, and `physical_residency_proven: false`. They do not store API keys, tokens, or a claim that data moved.

## Failure behavior

Suspended and read-only tenants cannot create environments. Reads of the caller's own environments still work. Deleted is terminal for lifecycle transitions and does not delete the row. Quota denial does not create a second billing adjustment.
