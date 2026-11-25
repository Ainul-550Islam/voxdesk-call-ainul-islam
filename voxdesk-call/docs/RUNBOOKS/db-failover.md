# Runbook: PostgreSQL Database Failover and Restore (`db-failover.md`)

## 1. Trigger Conditions & Alerts

| Signal | Threshold / Condition | Source |
|---|---|---|
| `VoxDeskDatabaseDown` | `voxdesk_db_up == 0` for `1m` | `observability/alerts.yml` |
| `/health/ready` | HTTP `503` with `checks.database.ok == false` | `app/core/health.py` |
| `health.database_check_failed` | Structured log event with `error_type` (`OperationalError`, `TimeoutError`, `InterfaceError`) | `app/core/health.py` |

A Kubernetes readiness probe can remove an unready pod from service endpoints. Do not assume a standalone proxy has fenced a writer; check the actual routing and scheduler state. Liveness is independent of database readiness.

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
   Identify actual blockers before terminating any session. An idle transaction's age alone does not establish that killing it is safe.
3. **If `pg_isready` fails**, proceed to Section 3 (Managed Standby Failover) or Section 4 (Point-in-Time / Dump Restore).

---

## 3. Primary-to-Standby Failover Procedure

1. Obtain operator authorization, drain API traffic and pause all writers, including the scheduler. Record the last known good write and fence the old primary to prevent split-brain.
2. Follow the database operator's tested promotion procedure for the actual replication technology. Confirm the candidate's replay position and that the old primary cannot accept writes; `pg_is_in_recovery()` alone cannot prove that data has caught up.
3. Point one API instance at the new primary. Confirm `/health/ready` and read-only tenant checks. Run `alembic current` against that database and compare with `alembic heads` in the deployment's image. This checkout's single head is `0095_calls_number_width`; the older drill ran at `0062_drop_pcap_artifacts`.
4. Resume traffic, then workers, only after checking durable job leases, the outbox and the possibility of duplicate external effects. Record recovery time and any acknowledged data loss.

## 4. Logical dump restore

The dump is a snapshot, not continuous point-in-time recovery. For the verified dump, a **new empty target**, the exact environment variables accepted by `scripts/restore.sh` and post-restore validation, follow [the recovery procedure](../DR_RUNBOOK.md#recovery-on-a-real-deployment). Do not run a destructive fixture drill or a blind production overwrite as an incident step.

## 5. Rehearsal and evidence

`scripts/dr_drill.sh` alters a privileged drill role and **drops both configured databases**. Use only a disposable, isolated PostgreSQL cluster. Its configurable `--output` JSON and `--backup-dir` are documented in [the drill runbook](../DR_RUNBOOK.md#isolated-drill). The provisioning command uses local administrative `psql`; verify that it and the later connection URLs resolve to the intended disposable cluster. The historical result is `evidence/dr/dr_drill_report.json`, not a current SLA.
