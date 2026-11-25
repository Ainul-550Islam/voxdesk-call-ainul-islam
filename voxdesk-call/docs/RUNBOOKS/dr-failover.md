# Runbook: Disaster Recovery — database and region failover (`dr-failover.md`)

Alert links that land here: `VoxDeskDatabaseDown`, `VoxDeskSchedulerDown`, and
any paging that says the primary database or the primary region is gone.
Latency-only symptoms belong in [`high-latency.md`](high-latency.md); a database
that is up but refusing writes belongs in [`db-failover.md`](db-failover.md).

## 0. Numbers this runbook is written against (measured, not promised)

Taken from `scripts/dr_drill.sh` on this tree — the drill is the measurement, so
re-read `evidence/dr/dr_drill_report.json` rather than trusting this table:

| Quantity | Measured | Where it comes from |
|---|---|---|
| RPO (data written before the dump, lost on failover) | **3.87 s** | `metrics.measured_rpo_seconds` |
| RTO (dump → target verified and serving) | **4.16 s** | `metrics.measured_rto_seconds` |
| Backup duration (2.9 MB dump, 362 tables at head `0095`) | **3.71 s** | `metrics.backup_duration_seconds` |
| Dump verification (`pg_restore --list`) | **0.046 s** | `metrics.backup_verify_duration_seconds` |
| Rows lost across failover in the drill | **0** | `metrics.rpo_data_loss_rows` |
| Schema after restore | head `0095_calls_number_width`, **362** public tables | `alembic_revision_restored`, `rendered_public_tables` |

These are single-host numbers on a 2 GB sandbox; treat them as a floor, not as
an SLA for a production region failover. Two consecutive runs of the same drill
produced RPO/RTO of 3.77 s / 3.69 s and 3.87 s / 4.16 s (the table above is the
second one, `2026-11-23T09:14:58Z`), so read them as ±15 % point estimates. The
RTO that matters in an incident is the one you just measured on your own
infrastructure — run the drill quarterly and record the date and both numbers.

## 1. Decide which failure you have

| Symptom | Failure | Go to |
|---|---|---|
| `voxdesk_db_up == 0`, API `/health/ready` → 503 `checks.database.ok == false`, both pods and the database in one region | database instance lost | §2 |
| Database healthy, but the whole region is unreachable (API pods gone, ingress 502/504, cloud status page red) | region lost | §3 |
| Database up but writes fail (`read-only transaction`, `too many connections`) | not DR — ordinary failover | [`db-failover.md`](db-failover.md) |

## 2. Database failover (same region)

1. **Freeze the writers before promoting anything.** A scheduler that keeps
   claiming jobs against a dying primary produces duplicate side effects
   (webhooks, CRM writes) that outlive the incident:
   ```bash
   kubectl -n voxdesk scale deploy/voxdesk-scheduler --replicas=0
   kubectl -n voxdesk scale deploy/voxdesk-api --replicas=0
   ```
2. **Promote the standby** (managed Postgres: the provider's promote action;
   self-hosted: `pg_ctl promote -D $PGDATA`). Confirm the new primary accepts
   writes and is not still in recovery:
   ```bash
   psql "$VOXDESK_PG_URL" -Atc "select pg_is_in_recovery();"   # expect: f
   psql "$VOXDESK_PG_URL" -Atc "select version_num from alembic_version;"  # expect: 0095_calls_number_width
   ```
3. **Point the platform at the new primary.** `DATABASE_URL` comes from the
   `voxdesk-runtime` Secret (or the External Secret that renders it). Change it
   there, never in a Deployment spec:
   ```bash
   kubectl -n voxdesk create secret generic voxdesk-runtime \
     --from-literal=DATABASE_URL="$NEW_DATABASE_URL" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
4. **Bring the API back first, the scheduler second.** The API is stateless and
   its readiness probe runs `SELECT 1`; a scheduler started against an
   unfinished promotion is how duplicate deliveries happen:
   ```bash
   kubectl -n voxdesk scale deploy/voxdesk-api --replicas=<n>
   kubectl -n voxdesk rollout status deploy/voxdesk-api --timeout=5m
   kubectl -n voxdesk scale deploy/voxdesk-scheduler --replicas=1
   ```
5. **Prove it, don't assume it.** `/health/ready` must be 200 with
   `checks.database.ok == true`; one call must be created and read back:
   ```bash
   curl -fsS "$PUBLIC_BASE_URL/health/ready" | jq '.checks.database'
   ```
6. **Drain the outbox backlog** created while the database was down — see
   [`webhook-backlog.md`](webhook-backlog.md). Stuck side effects do not clear
   themselves.

## 3. Region failover

1. Confirm the decision against a second source (cloud status page, provider
   health API). A single monitoring view in the failing region is the least
   reliable witness that the region is down.
2. Bring up the passive region's release: same chart, same image digest
   (`image.digest`, not `image.tag`), `infra/helm/voxdesk/values-prod.yaml` plus
   the passive region's overlay. The migration Job is a `pre-install,pre-upgrade`
   hook, so it runs before the API starts and refuses to hand over a half-migrated
   schema.
3. Restore the last verified dump, or promote the cross-region replica if one
   exists. Restoring is the honest path when replication lag is unknown:
   ```bash
   scripts/restore.sh "$(ls -t evidence/dr/backups/*.dump | head -1)"
   ```
   See [`backup-restore.md`](backup-restore.md) for the verification steps and
   measured timings.
4. Re-point DNS at the passive ingress, keeping TTL low during the operation.
   Certificates are region-local: check the new region's certificate before
   cutting DNS, not after (§[`certificate-rotation.md`](certificate-rotation.md)).
5. Expect **RPO ≥ replication lag** for this path. State the number you accepted
   in the incident log — "no data lost" is a claim that needs a row count, and
   the drill reports `rpo_data_loss_rows: 0` only because it seeds known rows.

## 4. After the incident

- Re-run the drill and store the new report: `bash scripts/dr_drill.sh` →
  `evidence/dr/dr_drill_report.json`. A restore that has not been re-measured
  since the failover is a hypothesis.
- Record: what failed, the failover path taken, measured RTO/RPO, and every
  duplicate or missing side effect you found.
- If a step in this runbook was wrong or missing, fix the runbook in the same
  change as the code — the next page is not the time to discover a stale command.
