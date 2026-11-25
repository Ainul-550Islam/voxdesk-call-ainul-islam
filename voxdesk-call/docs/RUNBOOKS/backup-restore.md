# Runbook: Backup and restore drill (`backup-restore.md`)

Quarterly, and after any data-loss event. A backup that has never been restored
is a hypothesis. Two ways to run this: the automated round trip (fast, in CI),
and the operator drill (writes `evidence/dr/dr_drill_report.json` with measured
RTO/RPO). Do both; they answer different questions.

## 0. What "good" looks like (measured on this tree)

From `evidence/dr/dr_drill_report.json` — re-read the file instead of trusting
this table:

| Quantity | Measured | Key |
|---|---|---|
| Drill status | **PASS** | `drill_status` |
| Schema at source and after restore | `0095_calls_number_width` | `alembic_revision_source` / `_restored` |
| Public tables restored | **362** | `restored_public_tables` |
| Dump size | 2,946,994 B (≈ 2.8 MiB) | `backup_size_bytes` |
| Backup duration | **3.71 s** | `metrics.backup_duration_seconds` |
| Dump verification (`scripts/backup_verify.sh`) | **0.046 s** | `metrics.backup_verify_duration_seconds` |
| RPO / RTO | **3.87 s / 4.16 s** (`2026-11-23T09:14:58Z`) | `metrics.measured_rpo_seconds` / `_rto_seconds` |
| Rows lost | **0** | `metrics.rpo_data_loss_rows` |

## 1. Fast path — the automated round trip (≈ 1 minute)

```bash
export DATABASE_URL="postgresql+asyncpg://voxdesk:voxdesk-ci@127.0.0.1:5432/voxdesk_test"
python3 -m pytest tests/deployment/test_backup_restore_scripts.py -q
```

The test creates two throwaway databases, seeds `dr_probe`, runs
`scripts/backup.sh`, restores into the second database, and compares rows.

## 2. Operator drill — measured RTO/RPO

```bash
export DATABASE_URL="postgresql+asyncpg://voxdesk_dr:voxdesk_dr_pass@127.0.0.1:5432/voxdesk_dr_source"
export PGPASSWORD="voxdesk_dr_pass"
bash scripts/dr_drill.sh                     # → evidence/dr/dr_drill_report.json
jq '{drill_status, alembic_revision_restored, restored_public_tables, metrics}' \
   evidence/dr/dr_drill_report.json
```

The drill provisions its own isolated databases (`voxdesk_dr_source`,
`voxdesk_dr_target`), migrates the source to head, seeds a known row set, then
runs backup → verify → restore and compares row counts. It accepts
`--output <path>` and `--backup-dir <dir>` when the defaults do not fit.

**If it stops with `residency check unavailable: password authentication failed`
or `backup refused: region pin not honoured`:** `scripts/backup.sh` calls
`scripts/backup_target_check.py`, which connects with the *application's*
`DATABASE_URL`, not with the drill's `PG*` variables. Export `DATABASE_URL`
for the drill database as shown above (measured failure: the drill aborted at
step 1/3 until this was set). A refusal is correct behaviour — a dump is a copy
of customer data and must not be written where no region was proven.

## 3. Restoring onto a running deployment

1. Stop the writer that would otherwise recreate the rows you are about to
   restore over:
   ```bash
   kubectl -n voxdesk scale deploy/voxdesk-scheduler --replicas=0
   ```
2. Restore with an explicit target database. `RESTORE_TARGET_DB` is mandatory —
   `scripts/restore.sh` refuses to guess, and the guard fires *before* the dump is
   read, so a forbidden target reports `BLOCKED` rather than a dump error:
   ```bash
   RESTORE_TARGET_DB=voxdesk_restore \
   PGHOST=db PGPORT=5432 POSTGRES_USER=voxdesk PGPASSWORD=… \
     scripts/restore.sh evidence/dr/backups/voxdesk-<stamp>.dump
   ```
3. Verify the restored database before pointing anything at it:
   ```bash
   psql "$RESTORED_URL" -Atc "select version_num from alembic_version;"       # 0095_calls_number_width
   psql "$RESTORED_URL" -Atc "select count(*) from information_schema.tables where table_schema='public';"  # 362
   pg_restore --list evidence/dr/backups/voxdesk-<stamp>.dump | wc -l          # archive entry count
   ```
4. Bring the API up against it, then the scheduler. Watch
   [`webhook-backlog.md`](webhook-backlog.md) for side effects that were queued
   before the restore and would now fire twice.

## 4. Restore targets and schedules in the chart

- Backup: `templates/backup-cronjob.yaml`, a CronJob with a persistent volume
  claim (`backup.persistence`), default `0 2 * * *` UTC, 14-day retention by
  `scripts/backup.sh`.
- Secrets never live in `values.yaml`: the CronJob reads `POSTGRES_*` and
  `DATABASE_URL` from `voxdesk-runtime` (or an External Secret).
- Restore is **manual by design**. An automated restore is an automated
  data-loss event.

## 5. Write it down

Fill `evidence/dr/dr_drill_report.json` into the change record: date, dump sha256,
RTO/RPO, row counts, and anything that surprised you. The drill exists so the
number in the incident report is a measurement rather than a hope.
