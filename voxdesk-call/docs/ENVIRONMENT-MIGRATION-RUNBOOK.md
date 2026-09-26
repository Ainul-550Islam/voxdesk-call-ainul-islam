# Environment scope migration

Revision `0019_environment_scope_business_resources` revises
`0018_organization_memberships_quotas`. Take a backup before applying it.
Restore drills use `RESTORE_TARGET_DB` and must not set
`RESTORE_ALLOW_OVERWRITE` against the live database.

## Order

1. Confirm `alembic heads` is a single head and `alembic current` is `0018`
   or already `0019`.
2. Apply `alembic upgrade head` under the existing migration lock.
3. The upgrade adds a nullable `environment_id`, backfills each row to its
   own tenant's production environment, then adds indexes and foreign keys.
4. The column becomes `NOT NULL` only for a table whose unbound count is zero.
   A tenant with no production environment is reported by the unbound count
   and is not given a newly invented environment.

## Dry run

Run the verification helper after a restore into a scratch database. It counts
unbound rows, tenant/environment mismatches, and tenants missing a production
environment. It does not delete or rewrite ids.

## Rollback

`alembic downgrade 0018` drops the new foreign keys, indexes, and
`environment_id` columns. It does not drop calls, leads, or any other business
row, and it does not remove the `auditaction` labels added for the new events.

## After the upgrade

- Confirm unbound counts are zero.
- Confirm no row's environment belongs to a different tenant.
- Place one production retrieval and confirm a staging document is absent.
- Confirm an existing tenant-only lead import still returns 201 and the new
  row's environment is that tenant's production environment.
- Confirm billing totals are unchanged aside from the new attribution column.
