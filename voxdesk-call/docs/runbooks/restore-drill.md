# Runbook — backup and restore drill

Quarterly, or after any data-loss event. A backup that has never been restored
is a hypothesis. This repository's own test does the whole round trip, so the
drill can start there.

## Fast path — the automated round trip

```sh
export DATABASE_URL="postgresql+asyncpg://voxdesk:voxdesk@127.0.0.1:5432/voxdesk_test"
python3 -m pytest tests/deployment/test_backup_restore_scripts.py -q
```

That test creates two throwaway databases, seeds `dr_probe`, runs
`scripts/backup.sh`, restores into the second database, and compares the rows.

**If it fails with `residency check unavailable: database "voxdesk" does not
exist`,** you are missing `DATABASE_URL`. `scripts/backup.sh` calls
`scripts/backup_target_check.py` first, which reads residency pins from the
database; with no URL it cannot run and the backup is refused by design. This
is fail-closed and correct — set the variable, do not work around the check.

## Manual drill

```sh
# 1. Back up. Refuses to write if the region pin cannot be honoured.
POSTGRES_DB=appdb PGHOST=127.0.0.1 sh scripts/backup.sh /tmp/drill
ls -la /tmp/drill/voxdesk-*.dump

# 2. The dump must be readable BEFORE you trust it. backup.sh does this itself
#    and exits non-zero if it fails; do it again if the file was moved.
pg_restore --list /tmp/drill/voxdesk-*.dump | head -20

# 3. Restore into a scratch database, never into production.
createdb -h 127.0.0.1 -U voxdesk drill_restore
pg_restore -h 127.0.0.1 -U voxdesk -d drill_restore --clean --if-exists \
  /tmp/drill/voxdesk-*.dump

# 4. Prove the data, not the exit code.
psql -h 127.0.0.1 -U voxdesk -d drill_restore -Atc \
  "select count(*) from calls; select max(created_at) from calls;"

# 5. Tear down.
dropdb -h 127.0.0.1 -U voxdesk drill_restore
```

## What "success" means

- The dump lists tables (`pg_restore --list` is not empty).
- Row counts and the newest `created_at` in the restore match the source within
  the age of the dump.
- Migrations in the restore are at the expected head — check
  `alembic_version`, because a dump from an older schema needs a migrate step
  before the app will boot against it.

## Retention

`backup.sh` keeps the last 14 local dumps and can sync to an S3-compatible
remote when `RCLONE_REMOTE` is set. Retention is not a substitute for a drill:
14 unreadable dumps is zero backups.

## Write down

- Time taken for dump and for restore (that is your RTO, measured, not
  estimated).
- Schema head of the dump and of the target.
- Anything that had to be done by hand — that is a missing automation.
