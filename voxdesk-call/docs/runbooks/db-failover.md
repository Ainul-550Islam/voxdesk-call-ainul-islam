# Runbook: PostgreSQL Database Failover and Restore (`db-failover.md`)

## 1. Trigger Conditions & Alerts

| Signal | Threshold / Condition | Source |
|---|---|---|
| `VoxDeskDatabaseDown` | `voxdesk_db_up == 0` for `1m` | `observability/alerts.yml` |
| `/health/ready` | HTTP `503` with `checks.database.ok == false` | `app/core/health.py` |
| `health.database_check_failed` | Structured log event with `error_type` (`OperationalError`, `TimeoutError`, `InterfaceError`) | `app/core/health.py` |

When `/health/ready` returns `503`, Kubernetes readiness gates and upstream reverse proxies (Caddy / ingress) stop routing new traffic to the affected API pod while liveness (`/health`) stays `200 OK` so pods are not needlessly restart-looped during a database blip.

---

## 2. Immediate Triage (First 2 Minutes)

1. **Verify whether the issue is database unreachability vs. connection pool saturation**:
   ```bash
   pg_isready -h "${PGHOST:-db}" -U "${POSTGRES_USER:-voxdesk}" -d "${POSTGRES_DB:-voxdesk}"
   ```
2. **If `pg_isready` succeeds**, inspect active connections and long-running queries:
   ```sql
   SELECT state, wait_event_type, wait_event, count(*)
   FROM pg_stat_activity
   WHERE datname = current_database()
   GROUP BY 1, 2, 3
   ORDER BY count(*) DESC;
   ```
   Terminate wedged idle-in-transaction connections older than 5 minutes if they block locks:
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE datname = current_database()
     AND state = 'idle in transaction'
     AND now() - state_change > interval '5 minutes';
   ```
3. **If `pg_isready` fails**, proceed to Section 3 (Managed Standby Failover) or Section 4 (Point-in-Time / Dump Restore).

---

## 3. Primary-to-Standby Failover Procedure

1. **Enter drain mode on API and scheduler pods** so no new outbound campaigns or background writes start during promotion:
   ```bash
   kubectl scale deployment/voxdesk-scheduler --replicas=0
   ```
2. **Promote the synchronous/streaming PostgreSQL standby** (or trigger managed RDS / Cloud SQL / Patroni failover):
   ```bash
   pg_ctl promote -D "${PGDATA}"
   ```
3. **Verify the promoted primary accepts writes** and has reached the expected WAL LSN:
   ```bash
   psql -h "${NEW_PGHOST}" -U "${POSTGRES_USER:-voxdesk}" -d "${POSTGRES_DB:-voxdesk}" \
     -c "SELECT pg_is_in_recovery();"
   # Must return: f
   ```
4. **Update `DATABASE_URL` (if DNS/VIP did not flip automatically) and roll the deployments**:
   ```bash
   kubectl rollout restart deployment/voxdesk
   kubectl scale deployment/voxdesk-scheduler --replicas=1
   kubectl rollout status deployment/voxdesk --timeout=120s
   ```
5. **Confirm readiness and Alembic schema head**:
   ```bash
   alembic heads
   curl -fsS http://localhost:8000/health/ready
   ```

---

## 4. Full Restore from Verified Backup (`scripts/backup.sh` / `scripts/restore.sh`)

When a logical corruption or catastrophic primary loss requires restoring from a custom-format dump:

1. **Locate and verify the latest dump archive** using `scripts/backup_verify.sh`:
   ```bash
   LATEST_DUMP="$(ls -1t ./backups/voxdesk-*.dump | head -n 1)"
   sh scripts/backup_verify.sh "${LATEST_DUMP}"
   ```
2. **Restore into a scratch database first (mandatory safety guard)** or into the target database:
   ```bash
   # Drill / validation restore into a clean target database:
   RESTORE_TARGET_DB=voxdesk_restore_drill sh scripts/restore.sh "${LATEST_DUMP}"

   # Production overwrite (requires explicit operator authorization):
   RESTORE_ALLOW_OVERWRITE=1 sh scripts/restore.sh "${LATEST_DUMP}"
   ```
3. **Apply any idempotent schema migrations up to head**:
   ```bash
   python3 scripts/migrate.py
   alembic heads
   ```
   Expected head: `0062_drop_pcap_artifacts (head)`.
4. **Recover in-flight durable job leases and verify outbox backlog**:
   - The `scripts/scheduler.py` `durable_jobs_loop()` automatically invokes `recover_abandoned()` on startup, reclaiming any `jobs` rows whose `leased_until` expired during the outage.
   - Verify `/health/ready` returns `200` and `voxdesk_db_up == 1`.

---

## 5. Automated DR Drill & Measured RPO / RTO (`scripts/dr_drill.sh`)

Run the scripted disaster-recovery drill before every release or monthly operations review:

```bash
bash scripts/dr_drill.sh
```

- **What `scripts/dr_drill.sh` executes**:
  1. Initializes an isolated PostgreSQL instance (or connects to the drill cluster), creates the VoxDesk schema, and seeds baseline tenant, call, turn, and outbox records with a UTC high-water mark timestamp (`pre_backup_high_water_utc`).
  2. Runs `scripts/backup.sh` to create a compressed `pg_dump -Fc` archive.
  3. Runs `scripts/backup_verify.sh` to verify archive TOC readability via `pg_restore --list`.
  4. Runs `scripts/restore.sh` with `RESTORE_TARGET_DB=voxdesk_restore_drill` to restore into a separate database.
  5. Verifies restored table and row counts match the source snapshot and records measured **RPO** and **RTO** in `evidence/dr/dr_drill_latest.json` and `evidence/dr/dr_drill_latest.log`.
