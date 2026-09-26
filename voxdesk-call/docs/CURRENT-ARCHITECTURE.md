# Current architecture — organization access

Organization is the parent of one or more tenants. Each tenant has environments
(development, staging, production). A user still has exactly one `users.tenant_id`.
Membership rows record that binding so it can be suspended or revoked without
deleting the user or flipping `is_active`.

Effective access is resolved server-side:

1. Explicit revoked or suspended membership denies, suspended for privileged
   actions only.
2. An explicit environment membership, capped so it cannot outrank the tenant role.
3. The tenant membership.
4. An organization owner or admin, for tenants in that organization only.
5. Otherwise deny. A missing membership row is the legacy single-tenant path.

`organization_id` and `environment_id` are not JWT claims. The current
environment, when one is selected, is a server-side row.

Roles are the existing `owner` / `admin` / `manager` / `agent` / `viewer`
values. Conceptual names such as `organization_owner` are aliases only.
Permissions are the existing `Permission` enum. There is no second RBAC engine.

Quota resolution reads organization, tenant and environment rows and the
existing billing entitlement. A hierarchy value may tighten a billing cap. It
may not raise one. A missing limit is unknown, not zero.

Identity policy remains the base. Scope overlays may only make mandatory
controls stricter. Child policies cannot weaken a parent MFA, SSO, password,
API-key or service-account restriction.
